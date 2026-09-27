"""Reconcile: did a pipeline rebuild the true tables from monthly submissions?

``reconcile`` compares the ``account`` and ``account_month`` tables a pipeline built
(a directory of Parquet/CSV files per table, a DuckDB file, or DataFrames) with the
dataset the submissions were made from, and names the delivery issue behind each
difference when the inbox's answer key is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from .dataset import DiskDataset
from .submissions import ACCOUNT_COLUMNS, ISSUES, LOG, MONTH_COLUMNS

KEYS = {"account": ("account_id",), "account_month": ("account_id", "as_of_month")}
COLUMNS = {"account": ACCOUNT_COLUMNS, "account_month": MONTH_COLUMNS}
MONEY_TOLERANCE = 0.005


@dataclass
class TableDiff:
    expected_rows: int
    rebuilt_rows: int
    missing_rows: int = 0
    extra_rows: int = 0
    duplicate_keys: int = 0
    missing_columns: list[str] = field(default_factory=list)
    mismatched_values: dict[str, int] = field(default_factory=dict)

    @property
    def differences(self) -> int:
        return (self.missing_rows + self.extra_rows + self.duplicate_keys
                + sum(self.mismatched_values.values()))


@dataclass
class ReconcileReport:
    tables: dict[str, TableDiff]
    causes: dict[str, int]
    examples: list[dict]

    @property
    def differences(self) -> int:
        return sum(t.differences for t in self.tables.values())

    @property
    def ok(self) -> bool:
        return self.differences == 0 and not any(t.missing_columns for t in self.tables.values())

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "differences": self.differences,
            "tables": {n: {**vars(t), "differences": t.differences} for n, t in self.tables.items()},
            "causes": self.causes,
            "examples": self.examples,
        }

    def to_markdown(self) -> str:
        verdict = "**PASS**: the rebuilt tables match exactly." if self.ok else (
            f"**FAIL**: {self.differences:,} differences.")
        lines = ["# Reconciliation", "", verdict, "",
                 "| Table | Expected rows | Rebuilt rows | Missing | Extra | Duplicate keys | Wrong values |",
                 "|---|---|---|---|---|---|---|"]
        for name, t in self.tables.items():
            wrong = ", ".join(f"{c} {n:,}" for c, n in t.mismatched_values.items()) or "0"
            lines.append(f"| {name} | {t.expected_rows:,} | {t.rebuilt_rows:,} | {t.missing_rows:,} | "
                         f"{t.extra_rows:,} | {t.duplicate_keys:,} | {wrong} |")
        for name, t in self.tables.items():
            if t.missing_columns:
                lines.append(f"\n{name} is missing columns: {', '.join(t.missing_columns)}")
        if self.causes:
            lines += ["", "## Likely causes (from the inbox answer key)", "",
                      "| Delivery issue | Differences |", "|---|---|"]
            lines += [f"| {c} | {n:,} |" for c, n in self.causes.items()]
        if self.examples:
            lines += ["", "## Examples", "",
                      "| Table | Key | Problem | Column | Expected | Rebuilt | Likely cause |",
                      "|---|---|---|---|---|---|---|"]
            for e in self.examples:
                lines.append(f"| {e['table']} | {e['key']} | {e['problem']} | {e.get('column') or ''} | "
                             f"{_cell(e.get('expected'))} | {_cell(e.get('rebuilt'))} | "
                             f"{e.get('cause') or ''} |")
        return "\n".join(lines) + "\n"


def _cell(v) -> str:
    return "" if v is None else str(v)


# --- reading ----------------------------------------------------------------------------------


def _read_expected(dataset: str | Path) -> dict[str, pl.DataFrame]:
    chunks = list(DiskDataset.open(dataset).iter_chunks())
    return {t: pl.concat([c[t] for c in chunks]) for t in KEYS}


def _read_rebuilt(rebuilt) -> dict[str, pl.DataFrame]:
    if isinstance(rebuilt, dict):
        return {t: rebuilt[t] for t in KEYS}
    path = Path(rebuilt)
    if path.is_file():
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError("reading a DuckDB file needs: pip install 'creforge[duckdb]'") from exc
        con = duckdb.connect(str(path), read_only=True)
        try:
            return {t: con.execute(f"SELECT * FROM {t}").pl() for t in KEYS}
        finally:
            con.close()
    out = {}
    for t in KEYS:
        folder = path / t
        files = sorted(folder.glob("*.parquet")) or sorted(folder.glob("*.csv"))
        if not files:
            raise FileNotFoundError(f"no Parquet or CSV files for table {t!r} in {folder}")
        if files[0].suffix == ".parquet":
            out[t] = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
        else:
            out[t] = pl.concat([pl.read_csv(f, infer_schema_length=0) for f in files])
    return out


def _target(dtype: pl.DataType) -> pl.DataType:
    if dtype == pl.Date:
        return pl.Date()
    if dtype == pl.Boolean:
        return pl.Boolean()
    if dtype.is_numeric():
        return pl.Float64() if (dtype.is_float() or dtype == pl.Decimal) else pl.Int64()
    return pl.String()


def _normalise(df: pl.DataFrame, types: dict[str, pl.DataType]) -> pl.DataFrame:
    exprs = []
    for c, t in types.items():
        if c not in df.columns:
            exprs.append(pl.lit(None, t).alias(c))
            continue
        col = pl.col(c)
        if df.schema[c] == pl.String:
            col = pl.when(col == "").then(None).otherwise(col)
            if t == pl.Date:
                exprs.append(col.str.slice(0, 10).str.to_date(strict=False).alias(c))
                continue
            if t == pl.Boolean:
                exprs.append(col.str.to_lowercase().is_in(["true", "t", "1"]).alias(c))
                continue
        exprs.append(col.cast(t, strict=False).alias(c))
    return df.select(exprs)


# --- compare ----------------------------------------------------------------------------------


def _compare(name: str, exp: pl.DataFrame, got: pl.DataFrame) -> tuple[TableDiff, list[pl.DataFrame]]:
    keys = list(KEYS[name])
    cols = [c for c in COLUMNS[name] if c not in keys]
    diff = TableDiff(expected_rows=exp.height, rebuilt_rows=got.height,
                     missing_columns=[c for c in COLUMNS[name] if c not in got.columns])
    if any(k in diff.missing_columns for k in keys):
        raise ValueError(f"rebuilt {name} has no key column(s) {keys}")
    types = {c: _target(exp.schema[c]) for c in COLUMNS[name]}
    exp = _normalise(exp, types)
    got = _normalise(got, types)
    unique = got.unique(keys, keep="first", maintain_order=True)
    diff.duplicate_keys = got.height - unique.height
    missing = exp.join(unique, on=keys, how="anti").select(keys)
    extra = unique.join(exp, on=keys, how="anti").select(keys)
    diff.missing_rows, diff.extra_rows = missing.height, extra.height
    dup_keys = got.group_by(keys).len().filter(pl.col("len") > 1).select(keys)
    problems = [missing.with_columns(pl.lit("missing row").alias("problem")),
                extra.with_columns(pl.lit("extra row").alias("problem")),
                dup_keys.with_columns(pl.lit("duplicate key").alias("problem"))]
    both = exp.join(unique, on=keys, how="inner", suffix="__r")
    for c in cols:
        if c in diff.missing_columns:
            continue
        a, b = pl.col(c), pl.col(f"{c}__r")
        differs = a.is_null() != b.is_null()
        if types[c] == pl.Float64:
            differs = differs | ((a - b).abs() > MONEY_TOLERANCE).fill_null(False)
        else:
            differs = differs | (a != b).fill_null(False)
        bad = both.filter(differs)
        if bad.height:
            diff.mismatched_values[c] = bad.height
            problems.append(bad.select(
                *keys, pl.lit("wrong value").alias("problem"), pl.lit(c).alias("column"),
                a.cast(pl.String).alias("expected"), b.cast(pl.String).alias("rebuilt")))
    return diff, problems


def reconcile(dataset: str | Path, rebuilt: str | Path | dict[str, pl.DataFrame],
              inbox: str | Path | None = None, examples: int = 10) -> ReconcileReport:
    """Compare a pipeline's rebuilt ``account`` and ``account_month`` with ``dataset``.

    ``rebuilt`` is a directory with ``account/`` and ``account_month/`` folders of
    Parquet or CSV files, a DuckDB database file, or a dict of DataFrames. Extra columns
    are ignored. With ``inbox`` (the ``creforge submissions`` output), each difference is
    matched to the delivery issue that most likely caused it.
    """
    expected = _read_expected(dataset)
    got = _read_rebuilt(rebuilt)
    lenders = expected["account"].select("account_id", "lender_id")
    tables, problems = {}, []
    for name in KEYS:
        tables[name], found = _compare(name, expected[name], got[name])
        for p in found:
            if name == "account":
                p = p.with_columns(pl.lit(None, pl.Date).alias("as_of_month"))
            for c in ("column", "expected", "rebuilt"):
                if c not in p.columns:
                    p = p.with_columns(pl.lit(None, pl.String).alias(c))
            problems.append(p.select("account_id", "as_of_month", "problem", "column", "expected",
                                     "rebuilt").with_columns(pl.lit(name).alias("table")))
    allp = pl.concat(problems) if problems else pl.DataFrame()
    causes: dict[str, int] = {}
    if allp.height and inbox is not None:
        allp = _attribute(allp, lenders, Path(inbox) / ISSUES)
        counted = allp.filter(pl.col("cause").is_not_null()).group_by("cause").len().sort(
            ["len", "cause"], descending=[True, False])
        causes = dict(counted.iter_rows())
    elif allp.height:
        allp = allp.with_columns(pl.lit(None, pl.String).alias("cause"))
    shown = []
    if allp.height:
        for row in allp.sort("table", "account_id", "as_of_month", "column", nulls_last=True
                             ).head(examples).iter_rows(named=True):
            key = row["account_id"] + (f"|{row['as_of_month']}" if row["as_of_month"] else "")
            shown.append({"table": row["table"], "key": key, "problem": row["problem"],
                          "column": row["column"], "expected": row["expected"],
                          "rebuilt": row["rebuilt"], "cause": row["cause"]})
    return ReconcileReport(tables=tables, causes=causes, examples=shown)


def _attribute(problems: pl.DataFrame, lenders: pl.DataFrame, answer_key: Path) -> pl.DataFrame:
    """Add ``cause``: the recorded issue for that account-month, else for its lender-month."""
    key = pl.read_parquet(answer_key)
    row_level = (key.filter(pl.col("account_id").is_not_null())
                 .group_by("account_id", "as_of_month").agg(pl.col("issue_type").first().alias("row_cause")))
    # A delivery issue concerns every month its file carries (a catch-up file holds several).
    first = dict(pl.read_parquet(answer_key.parent / LOG).select("submission_id", "first_month").iter_rows())
    months: dict[tuple[str, object], set[str]] = {}
    for iss, lender, month, sid in key.filter(pl.col("account_id").is_null()).select(
            "issue_type", "lender_id", "reporting_month", "submission_id").iter_rows():
        m = np.datetime64(min(first.get(sid) or month, month), "M")
        while m <= np.datetime64(month, "M"):
            months.setdefault((lender, m.astype("datetime64[D]").item()), set()).add(iss)
            m += 1
    delivery = pl.DataFrame(
        [(lender, m, ", ".join(sorted(v))) for (lender, m), v in months.items()],
        schema={"lender_id": pl.String, "as_of_month": pl.Date, "delivery_cause": pl.String}, orient="row")
    out = (problems.join(lenders, on="account_id", how="left")
           .join(row_level, on=["account_id", "as_of_month"], how="left")
           .join(delivery, on=["lender_id", "as_of_month"], how="left"))
    return out.with_columns(pl.coalesce("row_cause", "delivery_cause").alias("cause")).drop(
        "lender_id", "row_cause", "delivery_cause")
