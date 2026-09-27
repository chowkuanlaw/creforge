import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from click.testing import CliRunner

import creforge as cf
from creforge.cli import main
from creforge.features import feature_columns

DOC = Path(__file__).resolve().parent.parent / "docs" / "features.md"
AS_OF = "2024-06"


@pytest.fixture(scope="module")
def ds():
    return cf.generate(cf.Config.from_profile("baseline", subjects=4000, months=36, seed=11, chunk_size=2000))


@pytest.fixture(scope="module")
def feats(ds):
    return cf.features(ds, AS_OF, include_truth=True)


def _mi(d) -> int:
    return d.year * 12 + d.month - 1


def test_shape_and_columns(ds, feats):
    products = list(ds.config.profile.products)
    assert feats.columns == ["subject_id", "as_of_month", "risk_grade", *feature_columns(products),
                             "excluded", "bad", "bad_month"]
    on_file = ds.subject.filter(pl.col("created_month") <= pl.date(2024, 6, 1)).height
    assert feats.height == on_file == feats["subject_id"].n_unique()
    assert set(feats["excluded"].drop_nulls()) == {"already_bad", "no_open_account"}
    assert "risk_grade" not in cf.features(ds, AS_OF).columns


def test_no_leakage(ds, feats):
    """Rewrite everything after the observation month; only the target may change."""
    a = 2024 * 12 + 5
    later = (pl.col("as_of_month").dt.year() * 12 + pl.col("as_of_month").dt.month() - 1) > a
    chunks = []
    for c in ds.chunks:
        am = c["account_month"]
        acc = c["account"]
        inq = c["inquiry"]
        after_open = (acc["open_date"].dt.year() * 12 + acc["open_date"].dt.month() - 1) > a
        chunks.append({
            **c,
            "account_month": am.with_columns(
                # Values taken from other rows, not computed: Decimal arithmetic and float
                # round trips change values or scale in older Polars versions.
                pl.when(later).then(pl.col("balance").reverse()).otherwise(pl.col("balance")),
                pl.when(later).then(pl.lit("120+")).otherwise(pl.col("dpd_bucket").cast(pl.String))
                .cast(am.schema["dpd_bucket"]).alias("dpd_bucket"),
                pl.when(later).then(pl.lit("written_off")).otherwise(pl.col("status").cast(pl.String))
                .cast(am.schema["status"]).alias("status"),
            ),
            "account": acc.with_columns(
                pl.when(pl.Series(after_open)).then(pl.col("credit_limit").reverse())
                .otherwise(pl.col("credit_limit"))),
            "inquiry": pl.concat([inq, inq.filter(
                (pl.col("inquiry_date").dt.year() * 12 + pl.col("inquiry_date").dt.month() - 1) > a)]),
        })
    changed = cf.features(cf.Dataset(ds.config, chunks), AS_OF, include_truth=True)
    keep = [c for c in feats.columns if c not in ("bad", "bad_month")]
    assert changed.select(keep).equals(feats.select(keep))
    assert changed["bad"].sum() > feats["bad"].sum()


def test_target_recomputed_independently(ds, feats):
    a = 2024 * 12 + 5
    owner = dict(ds.account.select("account_id", "subject_id").iter_rows())
    first_bad: dict[str, int] = {}
    for acc_id, month, dpd, status in ds.account_month.select(
            "account_id", "as_of_month", pl.col("dpd_bucket").cast(pl.String),
            pl.col("status").cast(pl.String)).iter_rows():
        m = _mi(month)
        if a < m <= a + 12 and (dpd in ("90-119", "120+") or status == "written_off"):
            s = owner[acc_id]
            first_bad[s] = min(first_bad.get(s, m), m)
    bads = feats.filter(pl.col("bad") == 1).select("subject_id", "bad_month")
    got = {s: _mi(d) for s, d in bads.iter_rows()}
    assert got == first_bad


def test_hand_checked_features(ds, feats):
    a = 2024 * 12 + 5
    inq = defaultdict(int)
    for s, d in ds.inquiry.select("subject_id", "inquiry_date").iter_rows():
        if a - 12 < _mi(d) <= a:
            inq[s] += 1
    got = dict(feats.select("subject_id", "inquiries_12m").iter_rows())
    assert all(got[s] == inq.get(s, 0) for s in got)
    open_now = ds.account_month.filter(
        (pl.col("as_of_month") == pl.date(2024, 6, 1))
        & pl.col("status").cast(pl.String).is_in(["current", "delinquent", "restructured"])
    ).join(ds.account.select("account_id", "subject_id"), on="account_id").group_by("subject_id").len()
    merged = feats.join(open_now, on="subject_id", how="left").with_columns(pl.col("len").fill_null(0))
    assert (merged["len"] == merged["n_open_accounts"]).all()
    assert (feats.filter(pl.col("n_open_accounts") == 0)["excluded"].is_not_null()).all()


def test_signal_gini_and_grade_ordering(feats):
    dev = feats.filter(pl.col("excluded").is_null())
    rates = dev.group_by("risk_grade").agg(pl.col("bad").mean()).sort("risk_grade")["bad"].to_list()
    assert rates == sorted(rates) and rates[-1] > 5 * rates[0]
    cols = [c for c in feature_columns(["credit_card"]) if c in dev.columns]
    x = dev.select(cols).cast(pl.Float64).to_numpy()
    miss = np.isnan(x)
    x = np.where(miss, np.nanmedian(x, axis=0), x)
    x = (x - x.mean(0)) / (x.std(0) + 1e-9)
    x = np.column_stack([np.ones(len(x)), x, miss.any(1)])
    y = dev["bad"].to_numpy().astype(float)
    rng = np.random.default_rng(0)
    train = rng.random(len(y)) < 0.7
    w = np.zeros(x.shape[1])
    for _ in range(300):  # gradient descent with a little L2
        p = 1 / (1 + np.exp(-x[train] @ w))
        w -= 0.5 * (x[train].T @ (p - y[train]) / train.sum() + 1e-3 * w)
    score = x[~train] @ w
    yt = y[~train]
    order = np.argsort(score)
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    auc = (ranks[yt == 1].sum() - yt.sum() * (yt.sum() + 1) / 2) / (yt.sum() * (len(yt) - yt.sum()))
    assert 2 * auc - 1 > 0.4


def test_bad_definitions_nest(ds):
    defs = ("writeoff", "90dpd", "60dpd")
    f = {b: cf.features(ds, AS_OF, bad=b).select("subject_id", "bad") for b in defs}
    wo, d90, d60 = (set(f[b].filter(pl.col("bad") == 1)["subject_id"]) for b in defs)
    assert wo <= d90 <= d60 and wo and len(d60) > len(d90) > len(wo)


def test_snapshots_and_errors(ds):
    two = cf.features(ds, ["2024-03", "2024-06"])
    assert two["as_of_month"].unique().sort().to_list() == [date(2024, 3, 1), date(2024, 6, 1)]
    with pytest.raises(ValueError, match="choose a month from 2023-01 to 2024-12"):
        cf.features(ds, "2025-06")
    with pytest.raises(ValueError, match="bad must be"):
        cf.features(ds, AS_OF, bad="default")
    with pytest.raises(ValueError, match="at least 1"):
        cf.features(ds, AS_OF, performance=0)
    with pytest.raises(ValueError, match="at least one"):
        cf.features(ds, [])


def test_reference_sql_matches(ds, feats, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    text = DOC.read_text(encoding="utf-8")
    sql = re.search(r"<!-- reference-sql -->\s*```sql\n(.*?)```", text, re.S).group(1)
    ds.write(tmp_path / "clean")
    cf.load_duckdb(tmp_path / "clean", tmp_path / "db.duckdb")
    con = duckdb.connect(str(tmp_path / "db.duckdb"), read_only=True)
    got = con.execute(sql, {"as_of": "2024-06-01"}).pl()
    con.close()
    cols = ["subject_id", "n_open_accounts", "total_balance", "worst_dpd_12m", "inquiries_12m"]
    want = feats.select(cols).sort("subject_id")
    got = got.select(cols).with_columns(
        pl.col("total_balance").cast(pl.Float64).round(2),
        *[pl.col(c).cast(pl.Int64) for c in ("n_open_accounts", "worst_dpd_12m", "inquiries_12m")])
    want = want.with_columns(
        pl.col("total_balance").round(2),
        *[pl.col(c).cast(pl.Int64) for c in ("n_open_accounts", "worst_dpd_12m", "inquiries_12m")])
    assert got.equals(want)


def test_cli(ds, tmp_path):
    ds.write(tmp_path / "clean")
    runner = CliRunner()
    out = tmp_path / "f.parquet"
    res = runner.invoke(main, ["features", str(tmp_path / "clean"), "--as-of", "2024-03", "--as-of", AS_OF,
                               "-o", str(out), "--include-truth"])
    assert res.exit_code == 0 and "bad rate" in res.output, res.output
    assert pl.read_parquet(out)["as_of_month"].n_unique() == 2
    res = runner.invoke(main, ["features", str(tmp_path / "clean"), "--as-of", AS_OF, "--bad", "writeoff",
                               "-o", str(tmp_path / "f.csv")])
    assert res.exit_code == 0 and (tmp_path / "f.csv").exists()
    res = runner.invoke(main, ["features", str(tmp_path / "clean"), "--as-of", "2030-01", "-o", str(out)])
    assert res.exit_code != 0 and "choose a month" in res.output
    res = runner.invoke(main, ["features", str(tmp_path / "clean"), "--as-of", AS_OF, "-o",
                               str(tmp_path / "f.json")])
    assert res.exit_code != 0 and ".parquet or .csv" in res.output
