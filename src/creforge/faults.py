"""Fault injection: corrupt a clean creforge dataset in known ways, with an answer key.

``inject`` reads a clean dataset chunk by chunk, applies the faults listed in a fault
profile, and writes a dirty copy plus ``faults.parquet``: one row per injected fault,
identifying the affected row by its ``row_key`` as it appears in the dirty data (for a
deleted row, as it appeared in the clean data). ``score`` grades a data-quality tool's
findings against that answer key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .config import _list_builtin, _read_profile_dict
from .dataset import MANIFEST, DiskDataset, _write_chunk
from .engine import DPD_BUCKETS

FAULT_PROFILES_PACKAGE = "creforge.fault_profiles"
ANSWER_KEY = "faults.parquet"
KEY_SEPARATOR = "|"

# Columns that identify a row, joined with "|" (dates as YYYY-MM-DD) to form row_key.
KEYS = {
    "subject": ("subject_id",),
    "inquiry": ("inquiry_id",),
    "account": ("account_id",),
    "account_month": ("account_id", "as_of_month"),
    "account_party": ("account_id", "subject_id", "role"),
}
# Columns that must never be null (also checked by `creforge validate`).
REQUIRED = {
    "subject": ("birth_year", "region", "risk_grade"),
    "inquiry": ("subject_id", "inquiry_date", "outcome"),
    "account": ("subject_id", "product_type", "open_date", "interest_rate"),
    "account_month": ("balance", "dpd_bucket", "status"),
}
UNKNOWN_CODES = {
    "account_month": {"dpd_bucket": "999", "status": "unknown"},
    "account": {"product_type": "unknown_product"},
    "inquiry": {"outcome": "pending"},
}
FORMATTED_CODES = {"account_month": "status", "account": "product_type", "inquiry": "outcome"}
CSV_ONLY = ("date_format", "type_mismatch")
FAULT_TYPES = (
    "duplicate_row", "duplicate_key", "orphan_row", "missing_month", "null_required",
    "unknown_code", "negative_amount", "scale_error", "future_date", "code_formatting",
    "dpd_jump", "paid_while_rolling", "activity_after_closure", "close_reason_mismatch",
    "date_format", "type_mismatch",
)

Prob = Annotated[float, Field(ge=0.0, le=1.0)]


class FaultProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    allow_overlap: bool = Field(False, description="Allow several faults on the same row")
    rates: dict[str, Prob]

    @model_validator(mode="after")
    def _known(self) -> FaultProfile:
        unknown = set(self.rates) - set(FAULT_TYPES)
        if unknown:
            raise ValueError(f"unknown fault types: {sorted(unknown)}; known: {list(FAULT_TYPES)}")
        return self


def load_fault_profile(ref: str | Path) -> FaultProfile:
    """Load a built-in fault profile by name, or a YAML file by path (``extends:`` supported)."""
    return FaultProfile.model_validate(_read_profile_dict(ref, package=FAULT_PROFILES_PACKAGE))


def list_fault_profiles() -> list[str]:
    return _list_builtin(FAULT_PROFILES_PACKAGE)


def row_key(df: pl.DataFrame, table: str) -> pl.Series:
    """The ``row_key`` of each row of ``table``, in the format the answer key uses."""
    parts = [pl.col(c).cast(pl.String).fill_null("") for c in KEYS[table]]
    return df.select(pl.concat_str(parts, separator=KEY_SEPARATOR).alias("row_key"))["row_key"]


# --- per-table working state -----------------------------------------------------------------


class _Table:
    """A table under corruption: helper columns track row origin, deletion and touches."""

    def __init__(self, name: str, df: pl.DataFrame):
        self.name = name
        self.df = df.with_row_index("__rid").with_columns(
            pl.lit(0, pl.Int32).alias("__sub"),
            pl.lit(False).alias("__deleted"),
            pl.lit(False).alias("__touched"),
        )
        self.next_sub = 1

    def eligible(self, mask: pl.Expr | None, overlap: bool, allowed: np.ndarray | None) -> np.ndarray:
        cond = (pl.col("__sub") == 0) & ~pl.col("__deleted")
        if not overlap:
            cond = cond & ~pl.col("__touched")
        if mask is not None:
            cond = cond & mask.fill_null(False)
        rids = self.df.filter(cond)["__rid"].to_numpy()
        return rids if allowed is None else rids[np.isin(rids, allowed)]

    def _hit(self, rids: np.ndarray) -> pl.Series:
        # numpy membership: portable across Polars 1.x (is_in semantics changed over time).
        member = np.isin(self.df["__rid"].to_numpy(), rids) & (self.df["__sub"].to_numpy() == 0)
        return pl.Series("__hit", member)

    def touch(self, rids: np.ndarray) -> None:
        self.df = self.df.with_columns((pl.col("__touched") | self._hit(rids)).alias("__touched"))

    def rows(self, rids: np.ndarray) -> pl.DataFrame:
        sel = pl.DataFrame({"__rid": pl.Series(rids, dtype=pl.UInt32)})
        return sel.join(self.df.filter(pl.col("__sub") == 0), on="__rid", how="left")

    def add(self, new_rows: pl.DataFrame) -> np.ndarray:
        """Append copies (already modified); returns their __sub ids."""
        subs = np.arange(self.next_sub, self.next_sub + new_rows.height, dtype=np.int32)
        self.next_sub += new_rows.height
        new_rows = new_rows.with_columns(pl.Series("__sub", subs), pl.lit(True).alias("__touched"))
        self.df = pl.concat([self.df, new_rows.select(self.df.columns).cast(self.df.schema)])
        return subs

    def delete(self, rids: np.ndarray) -> None:
        self.df = self.df.with_columns((pl.col("__deleted") | self._hit(rids)).alias("__deleted"))

    def to_string(self, column: str) -> None:
        if self.df.schema[column] != pl.String:
            self.df = self.df.with_columns(pl.col(column).cast(pl.String))

    def set(self, rids: np.ndarray, column: str, values: list[Any]) -> None:
        """Set ``column`` on original rows ``rids`` (values in the column's own type)."""
        dtype = self.df.schema[column]
        upd = pl.DataFrame({"__rid": pl.Series(rids, dtype=pl.UInt32),
                            "__new": pl.Series(values, dtype=dtype, strict=False),
                            "__hit": [True] * len(rids)})
        self.df = (
            self.df.join(upd, on="__rid", how="left")
            .with_columns(
                pl.when(pl.col("__hit").fill_null(False) & (pl.col("__sub") == 0))
                .then(pl.col("__new")).otherwise(pl.col(column)).alias(column)
            )
            .drop("__new", "__hit")
        )

    def finish(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        """(clean-shaped output, keys of every (rid, sub) incl. deleted rows)."""
        df = self.df.sort("__rid", "__sub")
        keys = df.select("__rid", "__sub", row_key(df, self.name).alias("row_key"))
        out = df.filter(~pl.col("__deleted")).drop("__rid", "__sub", "__deleted", "__touched")
        return out, keys


@dataclass
class _Ctx:
    rng: np.random.Generator
    profile: FaultProfile
    csv: bool
    window_end: np.datetime64  # last day of the window
    known_accounts: set[str]
    records: list[dict] = field(default_factory=list)

    def count(self, n_eligible: int, rate: float) -> int:
        if rate <= 0 or n_eligible == 0:
            return 0
        return min(n_eligible, max(1, int(round(rate * n_eligible))))

    def sample(self, tbl: _Table, rate: float, mask: pl.Expr | None = None,
               allowed: np.ndarray | None = None) -> np.ndarray:
        cand = np.sort(tbl.eligible(mask, self.profile.allow_overlap, allowed))
        k = self.count(len(cand), rate)
        if k == 0:
            return cand[:0]
        chosen = np.sort(self.rng.choice(cand, size=k, replace=False))
        tbl.touch(chosen)
        return chosen

    def record(self, fault: str, tbl: _Table, rids, subs, column=None, original=None, injected=None):
        n = len(rids)
        subs = np.zeros(n, dtype=np.int32) if subs is None else subs
        orig = [None] * n if original is None else original
        inj = [None] * n if injected is None else injected
        for rid, sub, o, i in zip(rids, subs, orig, inj, strict=True):
            self.records.append({
                "fault_type": fault, "table": tbl.name, "__rid": int(rid), "__sub": int(sub),
                "column": column, "original_value": None if o is None else str(o),
                "injected_value": None if i is None else str(i),
            })


# --- faults ----------------------------------------------------------------------------------


def _duplicate_row(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    for name in ("account_month", "account", "inquiry"):
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate)
            subs = t.add(t.rows(rids))
            ctx.record("duplicate_row", t, rids, subs)


def _duplicate_key(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account" in tables:
        t = tables["account"]
        rids = ctx.sample(t, rate)
        rows = t.rows(rids)
        old = rows["lender_id"].to_list()
        # Next lender code (wrapping at 9999), so the duplicate always differs.
        new = [f"L{(int(v[1:]) % 9999) + 1:04d}" if v else "L0001" for v in old]
        subs = t.add(rows.with_columns(pl.Series("lender_id", new)))
        ctx.record("duplicate_key", t, rids, subs, "lender_id", old, new)
    if "account_month" in tables:
        t = tables["account_month"]
        rids = ctx.sample(t, rate)
        rows = t.rows(rids)
        old = rows["balance"]
        new = (old * 2 + 1).cast(old.dtype)
        subs = t.add(rows.with_columns(new.alias("balance")))
        ctx.record("duplicate_key", t, rids, subs, "balance", old.to_list(), new.to_list())


def _fake_account_id(ctx: _Ctx) -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    while True:
        fake = "A" + "".join(alphabet[i] for i in ctx.rng.integers(0, 32, 12))
        if fake not in ctx.known_accounts:
            ctx.known_accounts.add(fake)
            return fake


def _orphan_row(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    for name in ("account_month", "account_party"):
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate)
            rows = t.rows(rids)
            fakes = [_fake_account_id(ctx) for _ in range(rows.height)]
            subs = t.add(rows.with_columns(pl.Series("account_id", fakes)))
            ctx.record("orphan_row", t, rids, subs, "account_id", rows["account_id"].to_list(), fakes)


def _missing_month(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account_month" not in tables:
        return
    t = tables["account_month"]
    m = pl.col("as_of_month")
    middle = (m > m.min().over("account_id")) & (m < m.max().over("account_id"))
    rids = ctx.sample(t, rate, middle & (pl.col("__sub") == 0))
    t.delete(rids)
    ctx.record("missing_month", t, rids, None)


def _set_faults(ctx, t: _Table, fault: str, rids: np.ndarray, columns: list[str],
                make: Callable[[str, list], list], as_string: bool = False) -> None:
    """Pick a column per row, then set new values made by ``make(column, old_values)``."""
    if not len(rids):
        return
    pick = ctx.rng.integers(0, len(columns), len(rids))
    for ci, column in enumerate(columns):
        sel = rids[pick == ci]
        if not len(sel):
            continue
        if as_string:
            t.to_string(column)
        old = t.rows(sel)[column].to_list()
        new = make(column, old)
        t.set(sel, column, new)
        ctx.record(fault, t, sel, None, column, old, new)


def _null_required(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    for name, cols in REQUIRED.items():
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate)
            _set_faults(ctx, t, "null_required", rids, list(cols), lambda c, old: [None] * len(old))


def _unknown_code(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    for name, codes in UNKNOWN_CODES.items():
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate)
            _set_faults(ctx, t, "unknown_code", rids, list(codes),
                        lambda c, old, codes=codes: [codes[c]] * len(old), as_string=True)


def _negative_amount(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account_month" not in tables:
        return
    t = tables["account_month"]
    for column in ("balance", "amount_paid"):
        rids = ctx.sample(t, rate / 2, pl.col(column) > 0)
        _set_faults(ctx, t, "negative_amount", rids, [column], lambda c, old: [-v for v in old])


def _scale_error(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account_month" in tables:
        t = tables["account_month"]
        rids = ctx.sample(t, rate, pl.col("balance") > 0)
        _set_faults(ctx, t, "scale_error", rids, ["balance"], lambda c, old: [v * 100 for v in old])
    if "account" in tables:
        t = tables["account"]
        for column in ("credit_limit", "principal"):
            rids = ctx.sample(t, rate / 2, pl.col(column).is_not_null())
            _set_faults(ctx, t, "scale_error", rids, [column], lambda c, old: [v * 100 for v in old])


def _future_date(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    for name, column in (("account", "open_date"), ("inquiry", "inquiry_date")):
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate)
            days = ctx.rng.integers(30, 400, len(rids))
            dates = [(ctx.window_end + np.timedelta64(int(d), "D")).item() for d in days]
            _set_faults(ctx, t, "future_date", rids, [column], lambda c, old, dates=dates: dates)


def _code_formatting(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    styles = (str.upper, lambda v: v + " ", lambda v: " " + v, str.title)
    for name, column in FORMATTED_CODES.items():
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate, pl.col(column).is_not_null())
            which = ctx.rng.integers(0, len(styles), len(rids))

            def make(c, old, which=which):
                out = [styles[w](v) for w, v in zip(which, old, strict=True)]
                return [o if o != v else v.upper() + " " for o, v in zip(out, old, strict=True)]

            _set_faults(ctx, t, "code_formatting", rids, [column], make, as_string=True)


def _history(t: _Table) -> pl.DataFrame:
    """account_month original rows with the previous row's DPD bucket and status."""
    return (
        t.df.filter(pl.col("__sub") == 0)
        .sort("account_id", "as_of_month")
        .with_columns(
            pl.col("dpd_bucket").cast(pl.String).shift(1).over("account_id").alias("__prev_dpd"),
        )
    )


def _dpd_jump(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account_month" not in tables:
        return
    t = tables["account_month"]
    h = _history(t)
    ok = h.filter((pl.col("__prev_dpd") == "0") & (pl.col("dpd_bucket").cast(pl.String) == "0"))
    rids = ctx.sample(t, rate, allowed=ok["__rid"].to_numpy())
    _set_faults(ctx, t, "dpd_jump", rids, ["dpd_bucket"], lambda c, old: ["60-89"] * len(old))


def _paid_while_rolling(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account_month" not in tables:
        return
    t = tables["account_month"]
    idx = {b: i for i, b in enumerate(DPD_BUCKETS)}
    h = _history(t).with_columns(
        pl.col("dpd_bucket").cast(pl.String).replace_strict(idx, default=None).alias("__d"),
        pl.col("__prev_dpd").replace_strict(idx, default=None).alias("__p"),
    )
    ok = h.filter((pl.col("__d") > pl.col("__p")) & (pl.col("amount_paid") == 0))
    rids = ctx.sample(t, rate, allowed=ok["__rid"].to_numpy())
    if not len(rids):
        return
    due = t.rows(rids)["amount_due"].to_list()
    _set_faults(ctx, t, "paid_while_rolling", rids, ["amount_paid"],
                lambda c, old, due=due: [d if d else 1 for d in due])


def _activity_after_closure(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account_month" not in tables:
        return
    t = tables["account_month"]
    terminal = pl.col("status").cast(pl.String).is_in(["written_off", "closed"])
    rids = ctx.sample(t, rate, terminal)
    rows = t.rows(rids)
    later = rows.with_columns(
        pl.col("as_of_month").dt.offset_by("1mo").alias("as_of_month"),
        pl.lit("current").cast(t.df.schema["status"]).alias("status"),
        pl.lit("0").cast(t.df.schema["dpd_bucket"]).alias("dpd_bucket"),
    )
    subs = t.add(later)
    ctx.record("activity_after_closure", t, rids, subs, "status",
               rows["status"].cast(pl.String).to_list(), ["current"] * rows.height)


def _close_reason_mismatch(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    if "account" not in tables:
        return
    t = tables["account"]
    rids = ctx.sample(t, rate, pl.col("close_reason").is_not_null())

    def flip(c, old):
        return ["paid_off" if v == "written_off" else "written_off" for v in old]

    _set_faults(ctx, t, "close_reason_mismatch", rids, ["close_reason"], flip)


def _date_format(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    for name, column in (("account", "open_date"), ("inquiry", "inquiry_date")):
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate, pl.col(column).is_not_null())

            def make(c, old):
                return [f"{v[8:10]}/{v[5:7]}/{v[0:4]}" for v in old]

            _set_faults(ctx, t, "date_format", rids, [column], make, as_string=True)


def _type_mismatch(ctx: _Ctx, tables: dict[str, _Table], rate: float) -> None:
    for name, column in (("account_month", "balance"), ("account", "interest_rate")):
        if name in tables:
            t = tables[name]
            rids = ctx.sample(t, rate)
            _set_faults(ctx, t, "type_mismatch", rids, [column], lambda c, old: ["N/A"] * len(old),
                        as_string=True)


# Order matters: rows are added and removed before values change.
_FAULTS: dict[str, Callable] = {
    "duplicate_row": _duplicate_row,
    "duplicate_key": _duplicate_key,
    "orphan_row": _orphan_row,
    "activity_after_closure": _activity_after_closure,
    "missing_month": _missing_month,
    "null_required": _null_required,
    "unknown_code": _unknown_code,
    "negative_amount": _negative_amount,
    "scale_error": _scale_error,
    "future_date": _future_date,
    "code_formatting": _code_formatting,
    "dpd_jump": _dpd_jump,
    "paid_while_rolling": _paid_while_rolling,
    "close_reason_mismatch": _close_reason_mismatch,
    "date_format": _date_format,
    "type_mismatch": _type_mismatch,
}
assert set(_FAULTS) == set(FAULT_TYPES)


# --- inject ----------------------------------------------------------------------------------


def inject(clean: str | Path, out: str | Path, faults: str | Path | FaultProfile = "standard",
           seed: int = 0, format: str | None = None) -> dict:
    """Write a corrupted copy of dataset ``clean`` to ``out``, plus the answer key.

    ``format`` defaults to the clean dataset's. The CSV-only fault types (``date_format``,
    ``type_mismatch``) are skipped for Parquet output, which enforces column types.
    Same clean dataset + profile + seed → identical output.
    """
    src = DiskDataset.open(clean)
    profile = faults if isinstance(faults, FaultProfile) else load_fault_profile(faults)
    fmt = format or src.manifest["format"]
    out = Path(out)
    if out.resolve() == Path(clean).resolve():
        raise ValueError("inject would overwrite the clean dataset; choose another output directory")
    out.mkdir(parents=True, exist_ok=True)
    cfg = src.manifest["config"]
    start = np.datetime64(cfg["start_month"], "M")
    window_end = (start + np.timedelta64(cfg["months"], "M")).astype("datetime64[D]") - np.timedelta64(1, "D")

    skipped = {f: "CSV-only fault; output is Parquet" for f in CSV_ONLY if fmt != "csv"}
    counts: dict[str, int] = {}
    key_parts: list[pl.DataFrame] = []
    for chunk, frames in enumerate(src.iter_chunks()):
        tables = {name: _Table(name, df) for name, df in frames.items()}
        known = set(frames["account"]["account_id"].to_list()) if "account" in frames else set()
        rng = np.random.default_rng(np.random.SeedSequence([seed, chunk, 0xFA17]))
        ctx = _Ctx(rng=rng, profile=profile, csv=fmt == "csv", window_end=window_end,
                   known_accounts=known)
        for fault, fn in _FAULTS.items():
            rate = profile.rates.get(fault, 0.0)
            if rate > 0 and fault not in skipped:
                fn(ctx, tables, rate)

        written = {}
        keys = {}
        for name, t in tables.items():
            written[name], keys[name] = t.finish()
        _write_chunk(out, chunk, written, fmt)
        if ctx.records:
            rec = pl.DataFrame(ctx.records, schema={
                "fault_type": pl.String, "table": pl.String, "__rid": pl.UInt32, "__sub": pl.Int32,
                "column": pl.String, "original_value": pl.String, "injected_value": pl.String})
            parts = []
            for name, k in keys.items():
                part = rec.filter(pl.col("table") == name)
                if part.height:
                    parts.append(part.join(k, on=["__rid", "__sub"], how="left"))
            chunk_key = pl.concat(parts).with_columns(pl.lit(chunk, pl.Int32).alias("chunk"))
            key_parts.append(chunk_key.drop("__rid", "__sub"))
        for r in ctx.records:
            counts[r["fault_type"]] = counts.get(r["fault_type"], 0) + 1

    answer = (pl.concat(key_parts) if key_parts else pl.DataFrame(schema={
        "fault_type": pl.String, "table": pl.String, "column": pl.String,
        "original_value": pl.String, "injected_value": pl.String, "row_key": pl.String,
        "chunk": pl.Int32}))
    answer = answer.with_row_index("fault_id").select(
        "fault_id", "fault_type", "table", "row_key", "column", "original_value",
        "injected_value", "chunk")
    answer.write_parquet(out / ANSWER_KEY)

    manifest = dict(src.manifest)
    manifest["format"] = fmt
    manifest["row_counts"] = _row_counts(out, fmt, manifest["chunks"])
    manifest["faults"] = {
        "creforge_version": __version__,
        "profile": profile.model_dump(mode="json"),
        "seed": seed,
        "clean_config_sha256": src.manifest.get("config_sha256"),
        "answer_key": ANSWER_KEY,
        "counts": {f: counts.get(f, 0) for f in FAULT_TYPES if f in counts},
        "skipped": skipped,
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _row_counts(out: Path, fmt: str, chunks: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        n = 0
        for i in range(chunks):
            path = table_dir / f"part-{i:05d}.{fmt}"
            if path.exists():
                n += (pl.scan_parquet(path) if fmt == "parquet" else pl.scan_csv(path, infer_schema_length=0)
                      ).select(pl.len()).collect().item()
        counts[table_dir.name] = n
    return counts


# --- score -----------------------------------------------------------------------------------


@dataclass
class ScoreReport:
    faults: int
    findings: int
    faults_found: int
    true_findings: int
    by_type: dict[str, dict[str, float]]

    @property
    def recall(self) -> float:
        return self.faults_found / self.faults if self.faults else 1.0

    @property
    def precision(self) -> float:
        return self.true_findings / self.findings if self.findings else 1.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["recall"], d["precision"] = self.recall, self.precision
        return d

    def to_markdown(self) -> str:
        lines = [
            "# Data-quality score",
            "",
            f"- Faults injected: {self.faults:,}; findings submitted: {self.findings:,}",
            f"- **Recall: {self.recall:.1%}** (injected faults your checks found)",
            f"- **Precision: {self.precision:.1%}** (findings that were real faults)",
            "",
            "| Fault type | Injected | Found | Recall |",
            "|---|---|---|---|",
        ]
        for f, v in sorted(self.by_type.items()):
            lines.append(f"| {f} | {int(v['injected']):,} | {int(v['found']):,} | {v['recall']:.1%} |")
        return "\n".join(lines) + "\n"


def _read_table(obj: str | Path | pl.DataFrame) -> pl.DataFrame:
    if isinstance(obj, pl.DataFrame):
        return obj
    path = Path(obj)
    if path.suffix == ".parquet":
        return pl.read_parquet(path)
    return pl.read_csv(path, infer_schema_length=0)


def score(answer_key: str | Path | pl.DataFrame, findings: str | Path | pl.DataFrame) -> ScoreReport:
    """Grade DQ findings against an answer key.

    ``findings`` needs columns ``table`` and ``row_key``; an optional ``column`` makes a
    match stricter (it must then equal the fault's column when the fault has one).
    """
    key = _read_table(answer_key)
    found = _read_table(findings)
    missing = {"table", "row_key"} - set(found.columns)
    if missing:
        raise ValueError(f"findings are missing columns: {sorted(missing)}")
    if "column" not in found.columns:
        found = found.with_columns(pl.lit(None, pl.String).alias("column"))
    found = found.select(
        pl.col("table").cast(pl.String), pl.col("row_key").cast(pl.String),
        pl.col("column").cast(pl.String).alias("f_column"),
    ).with_row_index("finding_id")
    pairs = key.select("fault_id", "fault_type", "table", "row_key", "column").join(
        found, on=["table", "row_key"], how="inner"
    ).filter(
        pl.col("f_column").is_null() | pl.col("column").is_null() | (pl.col("f_column") == pl.col("column"))
    )
    hits = pairs.select("fault_id").unique()
    per_type = key.join(hits.with_columns(pl.lit(True).alias("found")), on="fault_id", how="left")
    by_type = {}
    for row in per_type.group_by("fault_type").agg(
        pl.len().alias("injected"), pl.col("found").fill_null(False).sum().alias("found")
    ).iter_rows(named=True):
        n, f = row["injected"], row["found"]
        by_type[row["fault_type"]] = {"injected": n, "found": f, "recall": f / n if n else 1.0}
    return ScoreReport(
        faults=key.height,
        findings=found.height,
        faults_found=hits.height,
        true_findings=pairs["finding_id"].n_unique(),
        by_type=by_type,
    )
