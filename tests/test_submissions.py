import re
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from click.testing import CliRunner

import creforge as cf
from creforge.cli import main
from creforge.submissions import ISSUE_TYPES, IssueProfile

DOC = Path(__file__).resolve().parent.parent / "docs" / "submissions.md"


def _reference_ingester():
    """The ingester shown in docs/submissions.md, executed as documented."""
    text = DOC.read_text(encoding="utf-8")
    code = re.search(r"<!-- reference-ingester -->\s*```python\n(.*?)```", text, re.S).group(1)
    scope: dict = {}
    exec(code, scope)
    return scope["rebuild"]


rebuild = _reference_ingester()


def _naive(inbox: Path, dedupe: bool, reader=pl.read_parquet):
    """Arrival order, later wins (if ``dedupe``), else simply append everything."""
    log = pl.read_parquet(inbox / "submissions.parquet").sort("received_at")
    rows = pl.concat([reader(inbox / f) for f in log["file"]])
    if dedupe:
        rows = rows.unique(["account_id", "as_of_month"], keep="last", maintain_order=True)
    am = rows.select("account_id", "as_of_month", "balance", "amount_due", "amount_paid",
                     "dpd_bucket", "months_in_arrears", "status")
    acc = rows.sort("as_of_month").unique("account_id", keep="last", maintain_order=True).drop(
        "as_of_month", "balance", "amount_due", "amount_paid", "dpd_bucket", "months_in_arrears", "status")
    return {"account": acc, "account_month": am}


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("sub") / "clean"
    cf.write_dataset(cf.Config.from_profile("baseline", subjects=800, months=12, seed=3, chunk_size=400), out)
    return out


@pytest.fixture(scope="module")
def nasty(dataset, tmp_path_factory):
    inbox = tmp_path_factory.mktemp("sub") / "inbox"
    cf.submissions(dataset, inbox, issues="nasty", seed=5)
    return inbox


@pytest.mark.parametrize("profile", ["none", "light", "standard", "nasty"])
def test_reference_ingester_rebuilds_the_truth(dataset, tmp_path, profile):
    inbox = tmp_path / "inbox"
    cf.submissions(dataset, inbox, issues=profile, seed=1)
    rep = cf.reconcile(dataset, rebuild(inbox), inbox=inbox)
    assert rep.ok, rep.to_markdown()


def test_naive_ingesters_fail_for_the_right_reasons(dataset, nasty):
    rep = cf.reconcile(dataset, _naive(nasty, dedupe=False), inbox=nasty)
    assert not rep.ok and rep.tables["account_month"].duplicate_keys > 0
    assert "duplicate" in rep.causes
    # Deduplicating in arrival order handles everything except corrections that arrive
    # before the original they correct.
    rep = cf.reconcile(dataset, _naive(nasty, dedupe=True), inbox=nasty)
    assert not rep.ok and rep.tables["account_month"].duplicate_keys == 0
    assert set(rep.causes) == {"out_of_order_correction"}, rep.causes
    assert "out_of_order_correction" in rep.to_markdown()


def test_every_issue_type_appears_and_is_consistent(nasty):
    log = pl.read_parquet(nasty / "submissions.parquet")
    key = pl.read_parquet(nasty / "issues.parquet")
    assert set(key["issue_type"]) == set(ISSUE_TYPES)
    assert log["submission_id"].to_list() == sorted(log["submission_id"])
    assert log["received_at"].is_sorted()
    by_id = {r["submission_id"]: r for r in log.iter_rows(named=True)}

    def rows(sid):
        return pl.read_parquet(nasty / by_id[sid]["file"])

    for r in key.filter(pl.col("issue_type") == "late").iter_rows(named=True):
        # Arrives after the following month has ended, so after that month's normal file.
        after_next = pl.Series([r["reporting_month"]]).dt.offset_by("2mo").item()
        assert by_id[r["submission_id"]]["received_at"].date() > after_next
    for r in key.filter(pl.col("issue_type") == "duplicate").iter_rows(named=True):
        assert rows(r["submission_id"]).equals(rows(r["related_submission_id"]))
    for r in key.filter(pl.col("issue_type") == "missing_then_catch_up").iter_rows(named=True):
        carrier = by_id[r["submission_id"]]
        assert carrier["first_month"] <= r["reporting_month"] < carrier["reporting_month"]
        assert r["reporting_month"] in set(rows(r["submission_id"])["as_of_month"])
    for r in key.filter(pl.col("issue_type") == "partial").iter_rows(named=True):
        sup, orig = rows(r["submission_id"]), rows(r["related_submission_id"])
        month = orig.filter(pl.col("as_of_month") == r["reporting_month"])
        assert sup.height and month.join(sup, on="account_id", how="inner").height == 0
    for r in key.filter(pl.col("issue_type").str.contains("correction")).iter_rows(named=True):
        wrong = rows(r["submission_id"]).filter((pl.col("account_id") == r["account_id"])
                                                & (pl.col("as_of_month") == r["as_of_month"]))
        right = rows(r["related_submission_id"]).filter(pl.col("account_id") == r["account_id"])
        assert str(wrong[r["column"]].item()) == r["submitted_value"]
        assert str(right[r["column"]].item()) == r["true_value"] != r["submitted_value"]
        order = by_id[r["related_submission_id"]]["received_at"] < by_id[r["submission_id"]]["received_at"]
        assert order == (r["issue_type"] == "out_of_order_correction")


def test_records_carry_close_fields_only_in_the_closing_month(nasty):
    log = pl.read_parquet(nasty / "submissions.parquet")
    recs = pl.concat([pl.read_parquet(nasty / f) for f in log["file"]])
    closed = recs.filter(pl.col("close_date").is_not_null())
    assert closed.height and (closed["close_date"].dt.truncate("1mo") == closed["as_of_month"]).all()
    assert closed["status"].cast(pl.String).is_in(["closed", "written_off"]).all()


def test_deterministic(dataset, nasty, tmp_path):
    again = tmp_path / "again"
    cf.submissions(dataset, again, issues="nasty", seed=5)
    files = sorted(p.relative_to(nasty) for p in nasty.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(again) for p in again.rglob("*") if p.is_file())
    for f in files:
        if f.name != "inbox.json":
            assert (nasty / f).read_bytes() == (again / f).read_bytes(), f
    other = tmp_path / "other"
    cf.submissions(dataset, other, issues="nasty", seed=6)
    assert not pl.read_parquet(other / "issues.parquet").equals(pl.read_parquet(nasty / "issues.parquet"))


def test_csv_inbox_and_rebuilt_folders_and_duckdb(dataset, tmp_path):
    inbox = tmp_path / "inbox"
    cf.submissions(dataset, inbox, issues="standard", format="csv")
    log = pl.read_parquet(inbox / "submissions.parquet")
    assert log["file"].str.ends_with(".csv").all()
    # A CSV pipeline: read every file as text, rebuild, write CSV folders.
    tables = rebuild(inbox, reader=lambda p: pl.read_csv(p, infer_schema_length=0))  # a CSV pipeline
    for name, df in tables.items():
        (tmp_path / "rebuilt" / name).mkdir(parents=True)
        df.write_csv(tmp_path / "rebuilt" / name / "part-0.csv")
    assert cf.reconcile(dataset, tmp_path / "rebuilt").ok

    duckdb = pytest.importorskip("duckdb")
    db = tmp_path / "r.duckdb"
    con = duckdb.connect(str(db))
    as_text = lambda p: pl.read_csv(p, infer_schema_length=0)  # noqa: E731
    for name, df in _naive(inbox, dedupe=False, reader=as_text).items():
        con.register("df", df.to_arrow())
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    rep = cf.reconcile(dataset, db)
    assert not rep.ok and rep.examples and rep.examples[0]["problem"] == "duplicate key"


def test_reconcile_reports_missing_extra_and_columns(dataset, nasty):
    tables = rebuild(nasty)
    am = tables["account_month"]
    first = am.head(1)
    tables["account_month"] = pl.concat([
        am.slice(1),
        first.with_columns(pl.col("as_of_month").dt.offset_by("100y")),
    ])
    tables["account"] = tables["account"].drop("secured")
    rep = cf.reconcile(dataset, tables)
    t = rep.tables["account_month"]
    assert (t.missing_rows, t.extra_rows) == (1, 1)
    assert rep.tables["account"].missing_columns == ["secured"] and not rep.ok
    assert "missing columns: secured" in rep.to_markdown()
    assert rep.to_dict()["differences"] == rep.differences == 2


def test_profiles_and_errors(dataset, tmp_path):
    assert cf.list_issue_profiles() == ["light", "nasty", "none", "standard"]
    assert cf.load_issue_profile("none").rates == {}
    with pytest.raises(ValueError, match="unknown issue types"):
        IssueProfile(name="x", rates={"lost": 0.1})
    with pytest.raises(ValueError, match="at most 1"):
        IssueProfile(name="x", rates={t: 0.2 for t in ISSUE_TYPES})
    with pytest.raises(ValueError, match="overwrite"):
        cf.submissions(dataset, dataset)
    with pytest.raises(FileNotFoundError):
        cf.reconcile(dataset, tmp_path)


def test_partial_needs_two_rows():
    from creforge.submissions import _plan_lender

    profile = IssueProfile(name="p", rates={"partial": 1.0})
    months = {np.datetime64("2024-01", "M"): 1, np.datetime64("2024-02", "M"): 5}
    files, issues = _plan_lender("L1", months, profile, np.random.default_rng(0))
    assert [i.month for i in issues] == [np.datetime64("2024-02", "M")]


def test_cli(dataset, tmp_path):
    runner = CliRunner()
    inbox = tmp_path / "inbox"
    res = runner.invoke(main, ["submissions", str(dataset), "-o", str(inbox), "-i", "nasty", "-s", "2"])
    assert res.exit_code == 0 and "submission files" in res.output, res.output
    for name, df in rebuild(inbox).items():
        (tmp_path / "ok" / name).mkdir(parents=True)
        df.write_parquet(tmp_path / "ok" / name / "part-0.parquet")
        (tmp_path / "bad" / name).mkdir(parents=True)
        (df if name == "account" else df.slice(5)).write_parquet(tmp_path / "bad" / name / "p.parquet")
    res = runner.invoke(main, ["reconcile", str(dataset), str(tmp_path / "ok")])
    assert res.exit_code == 0 and "PASS" in res.output, res.output
    res = runner.invoke(main, ["reconcile", str(dataset), str(tmp_path / "bad"), "--inbox", str(inbox)])
    assert res.exit_code == 1 and "FAIL" in res.output
    res = runner.invoke(main, ["reconcile", str(dataset), str(tmp_path / "bad"), "--max-differences", "5",
                               "--json"])
    assert res.exit_code == 0 and '"differences": 5' in res.output
    res = runner.invoke(main, ["issues", "list"])
    assert "standard" in res.output
    res = runner.invoke(main, ["issues", "show", "nasty"])
    assert "correction_share: 0.05" in res.output
