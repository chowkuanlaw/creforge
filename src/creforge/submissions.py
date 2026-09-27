"""Monthly submissions: split a dataset into the files lenders would send a bureau.

``submissions`` writes one file per lender per reporting month, each row an account's
static fields plus that month's status fields. A delivery-issue profile makes some files
arrive late, twice, split in parts, merged with the next month, or with wrong values
that a later correction file fixes. ``inbox/submissions.parquet`` is the delivery log and
``inbox/issues.parquet`` the answer key. Applying the files with the rule
"a correction beats an original, otherwise the later arrival wins" rebuilds the input
dataset's ``account`` and ``account_month`` tables exactly.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .config import _list_builtin, _read_profile_dict
from .dataset import DiskDataset
from .engine import DPD_BUCKETS
from .schema import SCHEMA

ISSUE_PROFILES_PACKAGE = "creforge.submission_profiles"
LOG = "submissions.parquet"
ISSUES = "issues.parquet"
INBOX_MANIFEST = "inbox.json"
ISSUE_TYPES = ("late", "duplicate", "correction", "out_of_order_correction",
               "missing_then_catch_up", "partial")
KINDS = ("original", "supplement", "resend", "correction")

_TABLES = {t.name: t for t in SCHEMA}
ACCOUNT_COLUMNS = tuple(c.name for c in _TABLES["account"].columns)
MONTH_COLUMNS = tuple(c.name for c in _TABLES["account_month"].columns)
# One submission record: the account-month key, the account's fields, the month's fields.
RECORD_COLUMNS = ("account_id", "as_of_month", *ACCOUNT_COLUMNS[1:], *MONTH_COLUMNS[2:])
# Correction targets: the fields a lender most often gets wrong and later restates.
_WRONG_GROUPS = ("amount_paid", "balance", "arrears")

Prob = Annotated[float, Field(ge=0.0, le=1.0)]


class IssueProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    rates: dict[str, Prob] = Field(default_factory=dict,
                                   description="Share of deliveries (lender x month) with each issue")
    correction_share: Prob = Field(0.02, description="Share of a corrected file's rows that are wrong")

    @model_validator(mode="after")
    def _check(self) -> IssueProfile:
        unknown = set(self.rates) - set(ISSUE_TYPES)
        if unknown:
            raise ValueError(f"unknown issue types: {sorted(unknown)}; known: {list(ISSUE_TYPES)}")
        if sum(self.rates.values()) > 1:
            raise ValueError("issue rates are shares of deliveries and must add up to at most 1")
        return self


def load_issue_profile(ref: str | Path) -> IssueProfile:
    """Load a built-in delivery-issue profile by name, or a YAML file by path."""
    return IssueProfile.model_validate(_read_profile_dict(ref, package=ISSUE_PROFILES_PACKAGE))


def list_issue_profiles() -> list[str]:
    return _list_builtin(ISSUE_PROFILES_PACKAGE)


# --- planning ---------------------------------------------------------------------------------


@dataclass
class _Part:
    month: np.datetime64                     # reporting month (datetime64[M])
    rows: np.ndarray | None = None           # row positions within that month; None = all
    wrong: tuple[np.ndarray, np.ndarray] | None = None  # (row positions, _WRONG_GROUPS index)


@dataclass
class _File:
    lender: str
    month: np.datetime64                     # the delivery's own reporting month
    kind: str
    received: np.datetime64                  # datetime64[s]
    parts: list[_Part] = field(default_factory=list)
    id: str = ""

    @property
    def first_month(self) -> np.datetime64:
        return min(p.month for p in self.parts)


@dataclass
class _Issue:
    issue_type: str
    lender: str
    month: np.datetime64
    file: _File
    related: _File | None = None
    rows: tuple[np.ndarray, np.ndarray] | None = None  # corrections: (row positions, groups)


def _month_end(m: np.datetime64) -> np.datetime64:
    return (m + 1).astype("datetime64[D]") - np.timedelta64(1, "D")


def _after(t: np.datetime64, rng, lo: int, hi: int) -> np.datetime64:
    """``t`` plus a random lo..hi days and a random time of day."""
    return (t.astype("datetime64[s]") + np.timedelta64(int(rng.integers(lo, hi + 1)), "D")
            + np.timedelta64(int(rng.integers(8 * 3600, 20 * 3600)), "s"))


def _plan_lender(lender: str, counts: dict[np.datetime64, int], profile: IssueProfile,
                 rng) -> tuple[list[_File], list[_Issue]]:
    months = sorted(counts)
    names = [t for t in ISSUE_TYPES if profile.rates.get(t, 0) > 0]
    cum = np.cumsum([profile.rates[t] for t in names])
    chosen = []
    for m in months:
        u = rng.random()
        i = int(np.searchsorted(cum, u, side="right"))
        issue = names[i] if i < len(names) else None
        if issue == "partial" and counts[m] < 2:
            issue = None
        chosen.append(issue)
    # A skipped month travels with the lender's next delivery; the last month can't be skipped.
    if chosen and chosen[-1] == "missing_then_catch_up":
        chosen[-1] = None

    files: list[_File] = []
    issues: list[_Issue] = []
    carried: list[np.datetime64] = []
    for m, issue in zip(months, chosen, strict=True):
        n = counts[m]
        if issue == "missing_then_catch_up":
            carried.append(m)
            continue
        t0 = _after(_month_end(m), rng, 3, 15)
        lead = [_Part(c) for c in carried]
        original = _File(lender, m, "original", t0, [*lead, _Part(m)])
        files.append(original)
        issues.extend(_Issue("missing_then_catch_up", lender, c, original) for c in carried)
        carried = []
        if issue == "late":
            original.received = _after(_month_end(m), rng, 50, 80)
            issues.append(_Issue("late", lender, m, original))
        elif issue == "duplicate":
            resend = _File(lender, m, "resend", _after(t0, rng, 1, 10), [*lead, _Part(m)])
            files.append(resend)
            issues.append(_Issue("duplicate", lender, m, resend, related=original))
        elif issue in ("correction", "out_of_order_correction"):
            k = min(n, max(1, round(profile.correction_share * n)))
            rows = np.sort(rng.choice(n, size=k, replace=False))
            groups = rng.integers(0, len(_WRONG_GROUPS), size=k)
            original.parts[-1].wrong = (rows, groups)
            fix = _File(lender, m, "correction", _after(t0, rng, 10, 40), [_Part(m, rows)])
            if issue == "out_of_order_correction":
                fix.received, original.received = t0, _after(t0, rng, 5, 20)
            files.append(fix)
            issues.append(_Issue(issue, lender, m, original, related=fix, rows=(rows, groups)))
        elif issue == "partial":
            k = min(n - 1, max(1, round(rng.uniform(0.1, 0.4) * n)))
            later = np.sort(rng.choice(n, size=k, replace=False))
            original.parts[-1].rows = np.setdiff1d(np.arange(n), later)
            supplement = _File(lender, m, "supplement", _after(t0, rng, 3, 20), [_Part(m, later)])
            files.append(supplement)
            issues.append(_Issue("partial", lender, m, supplement, related=original))
    return files, issues


# --- materialising files ----------------------------------------------------------------------


def _records(frames: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """account_month joined to account; close fields only from the month of closure."""
    acc = frames["account"].select(ACCOUNT_COLUMNS)
    rec = frames["account_month"].join(acc, on="account_id", how="inner")
    closing = pl.col("close_date").dt.truncate("1mo") == pl.col("as_of_month")
    return rec.with_columns(
        pl.when(closing).then(pl.col("close_date")).otherwise(None).alias("close_date"),
        pl.when(closing).then(pl.col("close_reason")).otherwise(None).alias("close_reason"),
    ).select(RECORD_COLUMNS)


def _wrong_values(df: pl.DataFrame, rows: np.ndarray, groups: np.ndarray) -> tuple[pl.DataFrame, list[dict]]:
    """Copy of ``df`` with the listed rows made wrong; returns the changes made."""
    dicts = df.to_dicts()
    changes = []
    for r, g in zip(rows.tolist(), groups.tolist(), strict=True):
        rec = dicts[r]
        before = dict(rec)
        kind = _WRONG_GROUPS[g]
        if kind == "arrears" and rec["status"] in ("closed", "written_off"):
            kind = "balance"  # arrears on a closed account would be a different kind of error
        if kind == "amount_paid":  # payment not yet posted, or posted to the wrong month
            paid = rec["amount_paid"]
            rec["amount_paid"] = paid * 0 if paid else (rec["amount_due"] or paid) + _money(paid, 50)
        elif kind == "balance":  # balance before the month's last transactions
            rec["balance"] = rec["balance"] + _money(rec["balance"], 125)
        else:  # arrears one bucket worse than true
            b = DPD_BUCKETS.index(rec["dpd_bucket"])
            rec["dpd_bucket"] = DPD_BUCKETS[min(b + 1, len(DPD_BUCKETS) - 1)]
            rec["months_in_arrears"] = rec["months_in_arrears"] + 1
            if rec["status"] == "current":
                rec["status"] = "delinquent"
        for col in MONTH_COLUMNS[2:]:
            if rec[col] != before[col]:
                changes.append({"account_id": rec["account_id"], "as_of_month": rec["as_of_month"],
                                "column": col, "true_value": _str(before[col]),
                                "submitted_value": _str(rec[col])})
    return pl.DataFrame(dicts, schema=df.schema), changes


def _money(like, amount: int):
    return Decimal(amount) if isinstance(like, Decimal) else float(amount)


def _str(v) -> str | None:
    return None if v is None else str(v)


def _write(df: pl.DataFrame, path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "parquet":
        df.write_parquet(path, compression="zstd", statistics=True)
    else:
        df.write_csv(path)


# --- public API -------------------------------------------------------------------------------


def submissions(dataset: str | Path, out: str | Path, issues: str | Path | IssueProfile = "standard",
                seed: int = 0, format: str | None = None) -> dict:
    """Split ``dataset`` into monthly lender submissions under ``out``.

    Writes ``<lender>/<YYYY-MM>/<submission_id>.<format>`` files, the delivery log
    ``submissions.parquet``, the answer key ``issues.parquet`` and ``inbox.json``.
    Same dataset, profile and seed give identical output. Returns the inbox manifest.
    """
    src = DiskDataset.open(dataset)
    profile = issues if isinstance(issues, IssueProfile) else load_issue_profile(issues)
    fmt = format or src.manifest["format"]
    if fmt not in ("parquet", "csv"):
        raise ValueError("format must be 'parquet' or 'csv'")
    out = Path(out)
    if out.resolve() == Path(dataset).resolve():
        raise ValueError("submissions would overwrite the dataset; choose another output directory")
    out.mkdir(parents=True, exist_ok=True)
    staging = out / "_staging"
    if staging.exists():
        shutil.rmtree(staging)

    # Pass 1: route every record to its lender (bounded memory: one chunk at a time).
    counts: dict[str, dict[np.datetime64, int]] = {}
    for chunk, frames in enumerate(src.iter_chunks()):
        rec = _records(frames)
        for (lender,) in rec.select("lender_id").unique().sort("lender_id").iter_rows():
            part = rec.filter(pl.col("lender_id") == lender)
            _write(part, staging / lender / f"part-{chunk:05d}.parquet", "parquet")
            per = counts.setdefault(lender, {})
            for m, n in part.group_by("as_of_month").len().iter_rows():
                key = np.datetime64(m, "M")
                per[key] = per.get(key, 0) + n

    # Plan every delivery, then number the files in arrival order, as a bureau would.
    lenders = sorted(counts)
    plans: dict[str, list[_File]] = {}
    all_issues: list[_Issue] = []
    for i, lender in enumerate(lenders):
        rng = np.random.default_rng(np.random.SeedSequence([seed, 0x5B17, i]))
        plans[lender], found = _plan_lender(lender, counts[lender], profile, rng)
        all_issues.extend(found)
    ordered = sorted((f for fs in plans.values() for f in fs),
                     key=lambda f: (f.received, f.lender, f.month, KINDS.index(f.kind)))
    for n, f in enumerate(ordered, start=1):
        f.id = f"S{n:07d}"

    # Pass 2: write each lender's files.
    row_counts: dict[str, int] = {}
    wrong_rows: dict[tuple[str, str], list[dict]] = {}
    for lender in lenders:
        rec = pl.concat([pl.read_parquet(p) for p in sorted((staging / lender).glob("*.parquet"))])
        by_month = {np.datetime64(m, "M"): df.sort("account_id")
                    for (m,), df in _split(rec, "as_of_month")}
        for f in plans[lender]:
            pieces = []
            for p in f.parts:
                df = by_month[p.month]
                if p.wrong is not None:
                    df, changes = _wrong_values(df, *p.wrong)
                    wrong_rows[(lender, str(p.month))] = changes
                if p.rows is not None:
                    df = df.filter(pl.Series(np.isin(np.arange(df.height), p.rows)))
                pieces.append(df)
            body = pl.concat(pieces)
            _write(body, out / _file_path(f, fmt), fmt)
            row_counts[f.id] = body.height
        shutil.rmtree(staging / lender)
    shutil.rmtree(staging, ignore_errors=True)

    log = pl.DataFrame({
        "submission_id": [f.id for f in ordered],
        "lender_id": [f.lender for f in ordered],
        "reporting_month": _dates([f.month for f in ordered], "M", "D"),
        "first_month": _dates([f.first_month for f in ordered], "M", "D"),
        "kind": [f.kind for f in ordered],
        "received_at": _dates([f.received for f in ordered], "s", "us"),
        "row_count": pl.Series([row_counts[f.id] for f in ordered], dtype=pl.Int64),
        "file": [_file_path(f, fmt).as_posix() for f in ordered],
    })
    log.write_parquet(out / LOG)
    key = _answer_key(all_issues, wrong_rows)
    key.write_parquet(out / ISSUES)

    manifest = {
        "creforge_version": __version__,
        "format": fmt,
        "source_config_sha256": src.manifest.get("config_sha256"),
        "profile": profile.model_dump(mode="json"),
        "seed": seed,
        "submissions": log.height,
        "rows": int(log["row_count"].sum()),
        "log": LOG,
        "answer_key": ISSUES,
        "issue_counts": {t: n for t in ISSUE_TYPES
                         if (n := key.filter(pl.col("issue_type") == t)
                             .select(["lender_id", "reporting_month"]).unique().height)},
        "privacy": src.manifest.get("privacy"),
    }
    (out / INBOX_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _dates(values: list, unit: str, as_unit: str) -> np.ndarray:
    return np.array(values, dtype=f"datetime64[{unit}]").astype(f"datetime64[{as_unit}]")


def _split(df: pl.DataFrame, col: str):
    """(key tuple, group) pairs in key order; avoids partition_by key-format differences."""
    for (value,) in df.select(col).unique().sort(col).iter_rows():
        yield (value,), df.filter(pl.col(col) == value)


def _file_path(f: _File, fmt: str) -> Path:
    return Path(f.lender) / str(f.month) / f"{f.id}.{fmt}"


def _answer_key(issues: list[_Issue], wrong_rows: dict[tuple[str, str], list[dict]]) -> pl.DataFrame:
    rows = []
    for iss in issues:
        base = {"issue_type": iss.issue_type, "lender_id": iss.lender,
                "reporting_month": iss.month.astype("datetime64[D]").item(),
                "submission_id": iss.file.id,
                "related_submission_id": iss.related.id if iss.related else None}
        if iss.rows is None:
            rows.append({**base, "account_id": None, "as_of_month": None, "column": None,
                         "true_value": None, "submitted_value": None})
        else:
            rows.extend({**base, **c} for c in wrong_rows[(iss.lender, str(iss.month))])
    schema = {"issue_type": pl.String, "lender_id": pl.String, "reporting_month": pl.Date,
              "submission_id": pl.String, "related_submission_id": pl.String,
              "account_id": pl.String, "as_of_month": pl.Date, "column": pl.String,
              "true_value": pl.String, "submitted_value": pl.String}
    df = pl.DataFrame(rows, schema=schema, orient="row") if rows else pl.DataFrame(schema=schema)
    return df.sort("reporting_month", "lender_id", "issue_type", "account_id", "column",
                   nulls_last=True).with_row_index("issue_id")
