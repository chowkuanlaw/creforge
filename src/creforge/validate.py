"""Integrity and calibration report for a generated dataset.

Integrity checks are hard guarantees of the generator: any failure is a bug.
Calibration checks compare observed portfolio statistics to the profile's targets.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import polars as pl

from .dataset import PRIVACY_STATEMENT, Dataset, DiskDataset
from .engine import DPD_BUCKETS

TERMINAL = ("written_off", "closed")
ACTIVE = ("current", "delinquent", "restructured")
AGE_BUCKETS = ((0, 6, "0-5"), (6, 12, "6-11"), (12, 24, "12-23"), (24, 36, "24-35"),
               (36, 60, "36-59"), (60, 10_000, "60+"))
SEASONING_PEAK_BUCKETS = ("6-11", "12-23", "24-35")
BAD_HORIZON = 12  # months
DPD30 = 2  # bucket index of 30-59
DPD90 = 4  # bucket index of 90-119
MIN_ROWS = 1000
MIN_EVENTS = 30  # expected events at the band midpoint before a rate is judged


@dataclass
class Check:
    name: str
    category: str  # "integrity" | "calibration"
    passed: bool | None  # None = skipped
    detail: str


@dataclass
class Report:
    config_sha256: str
    profile: str
    row_counts: dict[str, int]
    checks: list[Check] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    privacy: str = PRIVACY_STATEMENT

    @property
    def integrity_ok(self) -> bool:
        return all(c.passed is not False for c in self.checks if c.category == "integrity")

    @property
    def calibration_ok(self) -> bool:
        return all(c.passed is not False for c in self.checks if c.category == "calibration")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["integrity_ok"], d["calibration_ok"] = self.integrity_ok, self.calibration_ok
        return d

    def to_markdown(self) -> str:
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}
        lines = [
            "# creforge validation report",
            "",
            f"- Profile: `{self.profile}`",
            f"- Config SHA-256: `{self.config_sha256}`",
            "- Rows: " + ", ".join(f"{k} {v:,}" for k, v in self.row_counts.items()),
            f"- Integrity: **{'PASS' if self.integrity_ok else 'FAIL'}**, "
            f"calibration: **{'PASS' if self.calibration_ok else 'FAIL'}**",
            "",
        ]
        for cat in ("integrity", "calibration"):
            lines += [f"## {cat.title()}", "", "| Check | Result | Detail |", "|---|---|---|"]
            lines += [f"| {c.name} | {mark[c.passed]} | {c.detail} |"
                      for c in self.checks if c.category == cat]
            lines.append("")
        m = self.metrics
        lines += ["## Portfolio metrics", "", "| Product | Account-months | 30+ DPD share | "
                  "Annual write-off rate |", "|---|---|---|---|"]
        for p, v in m.get("products", {}).items():
            lines.append(f"| {p} | {v['active_rows']:,} | {v['dpd30_share']:.2%} | "
                         f"{v['annual_writeoff_rate']:.2%} |")
        lines += ["", "12-month bad rate (90+ DPD or write-off) by grade, accounts current at "
                  "window start:", "", "| Grade | Accounts | Bad rate |", "|---|---|---|"]
        for g, v in m.get("grades", {}).items():
            lines.append(f"| {g} | {v['accounts']:,} | {v['bad_rate']:.2%} |")
        lines += ["", "30+ DPD share by months on book:", "", "| Age | Account-months | 30+ share |",
                  "|---|---|---|"]
        for b, v in m.get("seasoning", {}).items():
            lines.append(f"| {b} | {v['rows']:,} | {v['dpd30_share']:.2%} |")
        lines += ["", "## Privacy statement", "", self.privacy, ""]
        return "\n".join(lines)


def validate(data: Dataset | DiskDataset | str | Path) -> Report:
    """Validate an in-memory :class:`Dataset` or a directory written by ``write_dataset``."""
    if isinstance(data, (str, Path)):
        data = DiskDataset.open(data)
    cfg = data.config
    start = pl.lit(cfg.start_month + "-01").str.to_date()
    last_month_idx = cfg.months - 1

    ids: dict[str, list[pl.Series]] = defaultdict(list)
    viol: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    prod_acc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    grade_acc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    age_acc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    month_acc: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    dpd_index = {b: i for i, b in enumerate(DPD_BUCKETS)}
    for ch in data.iter_chunks():
        s, q, a = ch["subject"], ch["inquiry"], ch["account"]
        am = ch["account_month"].with_columns(
            pl.col("dpd_bucket").cast(pl.String).replace_strict(dpd_index, return_dtype=pl.Int8)
            .alias("dpd"),
            pl.col("status").cast(pl.String),
        )
        for name, df in (("subject", s), ("inquiry", q), ("account", a), ("account_month", am)):
            counts[name] += df.height
        ids["subject"].append(s["subject_id"])
        ids["inquiry"].append(q["inquiry_id"])
        ids["account"].append(a["account_id"])

        # Referential integrity (every chunk is self-contained).
        viol["fk_inquiry_subject"] += q.join(s, on="subject_id", how="anti").height
        viol["fk_account_subject"] += a.join(s, on="subject_id", how="anti").height
        viol["fk_month_account"] += am.join(a, on="account_id", how="anti").height
        linked = a.filter(pl.col("inquiry_id").is_not_null()).join(
            q.select("inquiry_id", pl.col("subject_id").alias("q_subject"),
                     pl.col("lender_id").alias("q_lender"),
                     pl.col("product_type").cast(pl.String).alias("q_product"),
                     "inquiry_date", pl.col("outcome").cast(pl.String)),
            on="inquiry_id", how="left",
        )
        viol["fk_account_inquiry"] += linked.filter(
            pl.col("q_subject").is_null()
            | (pl.col("outcome") != "approved")
            | (pl.col("q_subject") != pl.col("subject_id"))
            | (pl.col("q_lender") != pl.col("lender_id"))
            | (pl.col("q_product") != pl.col("product_type").cast(pl.String))
        ).height
        viol["open_before_inquiry"] += linked.filter(
            pl.col("open_date") < pl.col("inquiry_date")).height

        # Per-account history shape.
        acc = a.select(
            "account_id", "open_date", "close_date", pl.col("close_reason").cast(pl.String),
            pl.col("product_type").cast(pl.String).alias("product"), "subject_id",
        ).with_columns(
            _month_index(pl.col("open_date"), start).clip(lower_bound=0).alias("exp_first"),
            pl.when(pl.col("close_date").is_null()).then(pl.lit(last_month_idx))
            .otherwise(_month_index(pl.col("close_date"), start)).alias("exp_last"),
        )
        hist = am.sort("account_id", "as_of_month").with_columns(
            _month_index(pl.col("as_of_month"), start).alias("m"),
            pl.col("dpd").shift(1).over("account_id").alias("prev_dpd"),
        )
        span = hist.group_by("account_id").agg(
            pl.col("m").min().alias("first"), pl.col("m").max().alias("last"),
            pl.len().alias("n"), pl.col("status").last().alias("last_status"),
            (pl.col("status").is_in(TERMINAL) & (pl.col("m") != pl.col("m").max())).sum()
            .alias("early_terminal"),
        ).join(acc, on="account_id", how="left")
        viol["history_span"] += span.filter(
            (pl.col("first") != pl.col("exp_first")) | (pl.col("last") != pl.col("exp_last"))
            | (pl.col("n") != pl.col("last") - pl.col("first") + 1)
        ).height
        viol["rows_after_terminal"] += int(span["early_terminal"].sum())
        viol["close_reason_mismatch"] += span.filter(
            (pl.col("close_reason").is_null() & pl.col("last_status").is_in(TERMINAL))
            | (pl.col("close_reason").is_not_null() & ~pl.col("last_status").is_in(TERMINAL))
            | ((pl.col("close_reason") == "written_off") != (pl.col("last_status") == "written_off"))
        ).height
        viol["accounts_without_history"] += acc.join(hist, on="account_id", how="anti").height
        viol["dpd_skips_bucket"] += hist.filter(pl.col("dpd") - pl.col("prev_dpd") > 1).height
        viol["paid_while_rolling"] += hist.filter(
            (pl.col("dpd") > pl.col("prev_dpd")) & (pl.col("amount_paid") > 0)).height
        viol["negative_amounts"] += hist.filter(
            (pl.col("balance") < 0) | (pl.col("amount_due") < 0) | (pl.col("amount_paid") < 0)).height
        viol["delinquent_without_due"] += hist.filter(
            (pl.col("status") == "delinquent") & (pl.col("amount_due") <= 0)).height

        # Calibration accumulators.
        grades = s.select("subject_id", pl.col("risk_grade").cast(pl.String).alias("grade"))
        h = hist.join(acc.select("account_id", "product", "open_date", "subject_id"),
                      on="account_id").join(grades, on="subject_id")
        active = h.filter(pl.col("status").is_in(ACTIVE))
        for r in h.group_by("product").agg(
            pl.col("status").is_in(ACTIVE).sum().alias("active_rows"),
            (pl.col("status").is_in(ACTIVE) & (pl.col("dpd") >= DPD30)).sum().alias("dpd30_rows"),
            (pl.col("status") == "written_off").sum().alias("writeoffs"),
        ).iter_rows(named=True):
            for k in ("active_rows", "dpd30_rows", "writeoffs"):
                prod_acc[r["product"]][k] += r[k]

        cohort = h.filter((pl.col("m") == 0) & (pl.col("status") == "current")).select(
            "account_id", "grade")
        bad = h.filter(
            (pl.col("m") >= 1) & (pl.col("m") <= BAD_HORIZON)
            & ((pl.col("dpd") >= DPD90) | (pl.col("status") == "written_off"))
        ).select("account_id").unique()
        for r in cohort.with_columns(
            pl.col("account_id").is_in(bad["account_id"].implode()).alias("bad")
        ).group_by("grade").agg(pl.len().alias("n"), pl.col("bad").sum()).iter_rows(named=True):
            grade_acc[r["grade"]]["accounts"] += r["n"]
            grade_acc[r["grade"]]["bad"] += r["bad"]

        age = pl.col("m") - _month_index(pl.col("open_date"), start)
        label = pl.lit("60+")
        for lo, hi, name in reversed(AGE_BUCKETS[:-1]):
            label = pl.when((age >= lo) & (age < hi)).then(pl.lit(name)).otherwise(label)
        for r in active.with_columns(label.alias("age")).group_by("age").agg(
            pl.len().alias("rows"), (pl.col("dpd") >= DPD30).sum().alias("dpd30")
        ).iter_rows(named=True):
            age_acc[r["age"]]["rows"] += r["rows"]
            age_acc[r["age"]]["dpd30"] += r["dpd30"]
        for r in active.group_by("m").agg(
            pl.len().alias("rows"), (pl.col("dpd") >= DPD30).sum().alias("dpd30")
        ).iter_rows(named=True):
            month_acc[r["m"]]["rows"] += r["rows"]
            month_acc[r["m"]]["dpd30"] += r["dpd30"]

    report = Report(config_sha256=cfg.sha256(), profile=cfg.profile.name, row_counts=dict(counts))
    for table, series in ids.items():
        col = pl.concat(series)
        dup = col.len() - col.n_unique()
        report.checks.append(Check(f"unique_{table}_id", "integrity", dup == 0, f"{dup} duplicates"))
    for name in ("fk_inquiry_subject", "fk_account_subject", "fk_account_inquiry",
                 "fk_month_account", "open_before_inquiry", "accounts_without_history",
                 "history_span", "rows_after_terminal", "close_reason_mismatch",
                 "dpd_skips_bucket", "paid_while_rolling", "negative_amounts",
                 "delinquent_without_due"):
        report.checks.append(Check(name, "integrity", viol[name] == 0, f"{viol[name]} violations"))

    _calibration(report, cfg, prod_acc, grade_acc, age_acc, month_acc)
    return report


def _month_index(col: pl.Expr, start: pl.Expr) -> pl.Expr:
    return (col.dt.year().cast(pl.Int32) * 12 + col.dt.month().cast(pl.Int32)) - (
        start.dt.year().cast(pl.Int32) * 12 + start.dt.month().cast(pl.Int32)
    )


def _calibration(report: Report, cfg, prod_acc, grade_acc, age_acc, month_acc) -> None:
    prof = cfg.profile
    products = {}
    for p in prof.products:
        v = prod_acc.get(p, {})
        rows = v.get("active_rows", 0)
        products[p] = {
            "active_rows": rows,
            "dpd30_share": v.get("dpd30_rows", 0) / rows if rows else 0.0,
            "annual_writeoff_rate": 12 * v.get("writeoffs", 0) / rows if rows else 0.0,
        }
    report.metrics["products"] = products

    for p, target in prof.targets.items():
        v = products[p]
        for metric in ("dpd30_share", "annual_writeoff_rate"):
            lo, hi = getattr(target, metric)
            exposure = v["active_rows"] / (12 if metric == "annual_writeoff_rate" else 1)
            if exposure * (lo + hi) / 2 < MIN_EVENTS:
                report.checks.append(Check(f"{p}.{metric}", "calibration", None,
                                           f"too few events to judge ({v[metric]:.2%})"))
                continue
            ok = lo <= v[metric] <= hi
            report.checks.append(Check(f"{p}.{metric}", "calibration", ok,
                                       f"{v[metric]:.2%} (target {lo:.1%}-{hi:.1%})"))

    grades = {}
    for g in prof.grades:
        v = grade_acc.get(g, {})
        n = v.get("accounts", 0)
        grades[g] = {"accounts": n, "bad_rate": v.get("bad", 0) / n if n else 0.0}
    report.metrics["grades"] = grades
    if cfg.months > BAD_HORIZON:
        ok = True
        seq = list(grades.values())
        for prev, nxt in zip(seq, seq[1:], strict=False):
            se = math.sqrt(max(prev["bad_rate"] * (1 - prev["bad_rate"]), 1e-9) / max(prev["accounts"], 1))
            if nxt["bad_rate"] < prev["bad_rate"] - 2 * se:
                ok = False
        worst = " < ".join(f"{g} {v['bad_rate']:.2%}" for g, v in grades.items())
        report.checks.append(Check("bad_rate_monotonic_in_grade", "calibration", ok, worst))
    else:
        report.checks.append(Check("bad_rate_monotonic_in_grade", "calibration", None,
                                   f"needs more than {BAD_HORIZON} months"))

    seasoning = {}
    for _, _, name in AGE_BUCKETS:
        v = age_acc.get(name, {})
        rows = v.get("rows", 0)
        seasoning[name] = {"rows": rows, "dpd30_share": v.get("dpd30", 0) / rows if rows else 0.0}
    report.metrics["seasoning"] = seasoning
    eligible = {k: v for k, v in seasoning.items() if v["rows"] >= MIN_ROWS}
    if eligible:
        peak = max(eligible, key=lambda k: eligible[k]["dpd30_share"])
        report.checks.append(Check("seasoning_peak", "calibration", peak in SEASONING_PEAK_BUCKETS,
                                   f"30+ share peaks at {peak} months on book"))

    by_month = {m: v["dpd30"] / v["rows"] for m, v in sorted(month_acc.items()) if v["rows"]}
    report.metrics["dpd30_share_by_month"] = by_month
    if prof.macro.is_flat and len(by_month) >= 12 and month_acc[0]["rows"] >= MIN_ROWS:
        later = [v for m, v in by_month.items() if m >= 6]
        ref = sum(later) / len(later)
        ratio = by_month[0] / ref if ref else 1.0
        report.checks.append(Check("no_window_start_artefact", "calibration", abs(ratio - 1) <= 0.25,
                                   f"month-0 30+ share is {ratio:.2f}x the later average"))
    else:
        report.checks.append(Check("no_window_start_artefact", "calibration", None,
                                   "needs a flat macro path and enough rows"))
