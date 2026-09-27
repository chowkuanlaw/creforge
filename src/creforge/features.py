"""Scorecard feature table: point-in-time bureau attributes and a good/bad target.

``features`` builds one row per subject on file at an observation month: attributes
computed only from data up to the end of that month, plus a target measured over the
following performance window. Chunks hold whole subjects (with their accounts, links
and inquiries), so the table is built chunk by chunk.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import polars as pl

from .dataset import Dataset, DiskDataset
from .engine import DPD_BUCKETS

BadDefinition = Literal["90dpd", "60dpd", "writeoff"]
BAD_DEFINITIONS = ("90dpd", "60dpd", "writeoff")
OPEN_STATUSES = ("current", "delinquent", "restructured")
REVOLVING = ("credit_card", "overdraft")
WINDOWS = (3, 6, 12, 24)
# DPD bucket as an ordinal: 0 = current ... 5 = 120+.
_DPD_RANK = {b: i for i, b in enumerate(DPD_BUCKETS)}
_BAD_RANK = {"90dpd": _DPD_RANK["90-119"], "60dpd": _DPD_RANK["60-89"]}


def _month_index(col: str) -> pl.Expr:
    return (pl.col(col).dt.year().cast(pl.Int32) * 12 + pl.col(col).dt.month().cast(pl.Int32) - 1)


def _parse_month(m) -> int:
    text = str(m)[:7]
    year, month = int(text[:4]), int(text[5:7])
    return year * 12 + month - 1


def _bad_expr(bad: str) -> pl.Expr:
    written_off = pl.col("status") == "written_off"
    if bad == "writeoff":
        return written_off
    return written_off | (pl.col("dpd_rank") >= _BAD_RANK[bad])


def _features_chunk(frames: dict[str, pl.DataFrame], a: int, performance: int, bad: str,
                    products: list[str], include_truth: bool) -> pl.DataFrame:
    subj = frames["subject"].with_columns(_month_index("created_month").alias("created_mi"))
    subj = subj.filter(pl.col("created_mi") <= a)
    acc = frames["account"].with_columns(_month_index("open_date").alias("open_mi")).filter(
        pl.col("open_mi") <= a).select(
        "account_id", "subject_id", pl.col("product_type").cast(pl.String), "secured",
        pl.col("credit_limit").cast(pl.Float64), "open_mi")
    months = frames["account_month"].with_columns(
        _month_index("as_of_month").alias("mi"),
        pl.col("dpd_bucket").cast(pl.String).replace_strict(_DPD_RANK, default=None,
                                                            return_dtype=pl.Int8).alias("dpd_rank"),
        pl.col("status").cast(pl.String),
        pl.col("balance").cast(pl.Float64), pl.col("amount_due").cast(pl.Float64),
        pl.col("amount_paid").cast(pl.Float64))
    hist = months.filter(pl.col("mi") <= a).join(acc, on="account_id", how="inner")
    now = hist.filter((pl.col("mi") == a) & pl.col("status").is_in(OPEN_STATUSES))

    revolving = pl.col("product_type").is_in(REVOLVING)
    portfolio = now.group_by("subject_id").agg(
        pl.len().alias("n_open_accounts"),
        *[(pl.col("product_type") == p).sum().alias(f"n_open_{p}") for p in products],
        pl.col("secured").mean().alias("secured_share"),
        pl.col("balance").sum().alias("total_balance"),
        pl.col("credit_limit").filter(revolving).sum().alias("revolving_limit"),
        pl.col("balance").filter(revolving).sum().alias("revolving_balance"),
        (pl.col("balance") / pl.col("credit_limit")).filter(revolving & (pl.col("credit_limit") > 0))
        .max().alias("max_utilisation"),
        pl.col("dpd_rank").max().alias("worst_dpd_now"),
    ).with_columns(
        pl.when(pl.col("revolving_limit") > 0)
        .then(pl.col("revolving_balance") / pl.col("revolving_limit")).alias("revolving_utilisation"),
    ).drop("revolving_balance")

    delinquent = pl.col("dpd_rank") >= 1
    history = hist.group_by("subject_id").agg(
        *[pl.col("dpd_rank").filter(pl.col("mi") > a - w).max().alias(f"worst_dpd_{w}m") for w in WINDOWS],
        (a - pl.col("mi").filter(delinquent).max()).alias("months_since_delinquency"),
        *[pl.col("account_id").filter(pl.col("dpd_rank") >= r).n_unique().alias(f"n_ever_{d}dpd")
          for d, r in ((30, 2), (60, 3), (90, 4))],
        *[(pl.col("amount_paid").filter(pl.col("mi") > a - w).sum()
           / pl.col("amount_due").filter(pl.col("mi") > a - w).sum()).alias(f"payment_ratio_{w}m")
          for w in (3, 6)],
        (pl.col("status") == "written_off").filter(pl.col("mi") > a - 12).any().alias("_recent_writeoff"),
        _bad_expr(bad).filter(pl.col("mi") == a).any().alias("_bad_now"),
    ).with_columns(pl.col(f"payment_ratio_{w}m").fill_nan(None) for w in (3, 6))
    history = history.with_columns(
        pl.when(pl.col(c).is_infinite()).then(None).otherwise(pl.col(c)).alias(c)
        for c in ("payment_ratio_3m", "payment_ratio_6m"))

    ages = acc.group_by("subject_id").agg(
        (a - pl.col("open_mi").min()).alias("oldest_account_months"),
        (a - pl.col("open_mi").max()).alias("newest_account_months"),
        (pl.col("open_mi") > a - 12).sum().alias("accounts_opened_12m"),
    )
    inq = frames["inquiry"].with_columns(_month_index("inquiry_date").alias("mi")).filter(pl.col("mi") <= a)
    demand = inq.group_by("subject_id").agg(
        (pl.col("mi") > a - w).sum().alias(f"inquiries_{w}m") for w in (3, 6, 12))

    open_ids = now.select("account_id").unique()
    links = pl.DataFrame(schema={"subject_id": pl.String, "n_joint": pl.UInt32, "n_guarantor": pl.UInt32})
    if "account_party" in frames:
        party = frames["account_party"].with_columns(pl.col("role").cast(pl.String)).join(
            open_ids, on="account_id", how="inner")
        links = party.group_by("subject_id").agg(
            (pl.col("role") == "joint").sum().alias("n_joint"),
            (pl.col("role") == "guarantor").sum().alias("n_guarantor"))

    future = months.filter((pl.col("mi") > a) & (pl.col("mi") <= a + performance)).join(
        frames["account"].select("account_id", "subject_id"), on="account_id", how="inner")
    target = future.filter(_bad_expr(bad)).group_by("subject_id").agg(pl.col("mi").min().alias("_bad_mi"))

    out = subj.select(
        "subject_id",
        (a // 12 - pl.col("birth_year").cast(pl.Int32)).alias("age_years"),
        (a - pl.col("created_mi")).alias("months_on_file"),
        *(["risk_grade"] if include_truth else []),
    )
    for part in (portfolio, history, ages, demand, links, target):
        out = out.join(part, on="subject_id", how="left")
    zero = ["n_open_accounts", *[f"n_open_{p}" for p in products], "accounts_opened_12m",
            "inquiries_3m", "inquiries_6m", "inquiries_12m", "n_joint", "n_guarantor",
            "n_ever_30dpd", "n_ever_60dpd", "n_ever_90dpd"]
    out = out.with_columns(pl.col(c).fill_null(0).cast(pl.Int32) for c in zero)
    out = out.with_columns(
        pl.when(pl.col("_bad_now").fill_null(False) | pl.col("_recent_writeoff").fill_null(False))
        .then(pl.lit("already_bad"))
        .when(pl.col("n_open_accounts") == 0).then(pl.lit("no_open_account"))
        .otherwise(None).alias("excluded"),
        pl.col("_bad_mi").is_not_null().cast(pl.Int8).alias("bad"),
        _month_date("_bad_mi").alias("bad_month"),
    )
    return out.drop("_bad_now", "_recent_writeoff", "_bad_mi", "revolving_limit").sort("subject_id")


def _month_date(col: str) -> pl.Expr:
    return pl.date(pl.col(col) // 12, pl.col(col) % 12 + 1, 1)


def feature_columns(products: Iterable[str]) -> list[str]:
    """Feature column names in output order (keys, exclusion and target columns excluded)."""
    return ["age_years", "months_on_file", "n_open_accounts", *[f"n_open_{p}" for p in products],
            "secured_share", "total_balance", "revolving_utilisation", "max_utilisation",
            "worst_dpd_now", *[f"worst_dpd_{w}m" for w in WINDOWS], "months_since_delinquency",
            "n_ever_30dpd", "n_ever_60dpd", "n_ever_90dpd", "payment_ratio_3m", "payment_ratio_6m",
            "oldest_account_months", "newest_account_months", "accounts_opened_12m",
            "inquiries_3m", "inquiries_6m", "inquiries_12m", "n_joint", "n_guarantor"]


def features(dataset: str | Path | Dataset, as_of: str | list[str], performance: int = 12,
             bad: BadDefinition = "90dpd", include_truth: bool = False) -> pl.DataFrame:
    """Point-in-time feature table with a good/bad target.

    One row per subject on file at each observation month ``as_of`` ("YYYY-MM"; a list
    gives several snapshots, stacked). Features use data up to the end of that month
    only; ``bad`` is 1 if any of the subject's accounts meets the bad definition in the
    ``performance`` months that follow. ``include_truth`` adds the generator's hidden
    ``risk_grade``.
    """
    if bad not in BAD_DEFINITIONS:
        raise ValueError(f"bad must be one of {', '.join(BAD_DEFINITIONS)}")
    if performance < 1:
        raise ValueError("performance must be at least 1 month")
    src = dataset if isinstance(dataset, Dataset) else DiskDataset.open(dataset)
    cfg = src.config
    first = _parse_month(cfg.start_month)
    last = first + cfg.months - 1
    products = list(cfg.profile.products)
    snapshots = [as_of] if isinstance(as_of, str) else list(as_of)
    if not snapshots:
        raise ValueError("give at least one as_of month")
    idx = []
    for s in snapshots:
        a = _parse_month(s)
        if a < first or a + performance > last:
            lo, hi = _label(first)[:7], _label(last - performance)[:7]
            raise ValueError(f"as_of {s} leaves no room for a {performance}-month performance window: "
                             f"choose a month from {lo} to {hi}")
        idx.append(a)
    parts = []
    for frames in src.iter_chunks():
        for a in idx:
            df = _features_chunk(frames, a, performance, bad, products, include_truth)
            parts.append(df.with_columns(pl.lit(_label(a)).str.to_date("%Y-%m-%d").alias("as_of_month")))
    out = pl.concat(parts)
    cols = ["subject_id", "as_of_month", *(["risk_grade"] if include_truth else []),
            *feature_columns(products), "excluded", "bad", "bad_month"]
    return out.select(cols).sort("as_of_month", "subject_id")


def _label(mi: int) -> str:
    return f"{mi // 12:04d}-{mi % 12 + 1:02d}-01"
