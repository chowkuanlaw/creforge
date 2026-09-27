import hashlib
import shutil
from pathlib import Path

import polars as pl
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

import creforge as cf
from creforge.cli import main
from creforge.faults import FAULT_TYPES, KEYS, FaultProfile

TABLES = tuple(KEYS)


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def clean(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("clean")
    cf.write_dataset(cf.Config.from_profile("baseline", subjects=1500, months=24, seed=5,
                                            chunk_size=750), path)
    return path


@pytest.fixture(scope="module")
def dirty(clean, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("dirty")
    cf.inject(clean, path, faults="standard", seed=7)
    return path


def _tables(path: Path) -> dict[str, pl.DataFrame]:
    chunks = list(cf.DiskDataset.open(path).iter_chunks())
    return {t: pl.concat([c[t] for c in chunks], how="vertical_relaxed") for t in chunks[0]}


def test_clean_dataset_is_untouched(clean, tmp_path):
    before = _hashes(clean)
    cf.inject(clean, tmp_path / "d", seed=1)
    assert _hashes(clean) == before


def test_deterministic(clean, tmp_path):
    cf.inject(clean, tmp_path / "a", seed=3)
    cf.inject(clean, tmp_path / "b", seed=3)
    cf.inject(clean, tmp_path / "c", seed=4)
    assert _hashes(tmp_path / "a") == _hashes(tmp_path / "b")
    assert _hashes(tmp_path / "a")["faults.parquet"] != _hashes(tmp_path / "c")["faults.parquet"]


def test_every_fault_type_injected_for_csv(clean, tmp_path):
    manifest = cf.inject(clean, tmp_path / "csv", seed=2, format="csv")
    assert set(manifest["faults"]["counts"]) == set(FAULT_TYPES)
    assert manifest["faults"]["skipped"] == {}


def test_csv_only_faults_skipped_for_parquet(dirty):
    import json
    info = json.loads((dirty / "manifest.json").read_text())["faults"]
    assert set(info["skipped"]) == {"date_format", "type_mismatch"}
    assert set(info["counts"]) == set(FAULT_TYPES) - {"date_format", "type_mismatch"}


def test_answer_key_explains_every_difference(clean, dirty):
    """Every row that differs between clean and dirty is listed in the answer key."""
    key = pl.read_parquet(dirty / "faults.parquet")
    c, d = _tables(clean), _tables(dirty)
    def as_text(df: pl.DataFrame, table: str) -> pl.DataFrame:
        # Nulls become a sentinel so the anti-joins below treat null == null on any Polars 1.x.
        return df.select(pl.all().cast(pl.String).fill_null("<null>")).with_columns(
            cf.row_key(df, table).alias("__key"))

    for table in TABLES:
        cc, dd = as_text(c[table], table), as_text(d[table], table)
        cols = [col for col in cc.columns if col != "__key"]
        changed = pl.concat([
            dd.join(cc, on=cols, how="anti")["__key"],
            cc.join(dd, on=cols, how="anti")["__key"],
        ]).unique()
        listed = set(key.filter(pl.col("table") == table)["row_key"].to_list())
        unexplained = set(changed.to_list()) - listed
        assert not unexplained, (table, sorted(unexplained)[:5])
        # Duplicates add rows without changing values; check counts add up too.
        dup = key.filter((pl.col("table") == table) & pl.col("fault_type").is_in(
            ["duplicate_row", "duplicate_key", "orphan_row", "activity_after_closure"])).height
        removed = key.filter((pl.col("table") == table) & (pl.col("fault_type") == "missing_month")).height
        assert d[table].height == c[table].height + dup - removed, table


def test_validate_flags_injected_faults(dirty):
    report = cf.validate(dirty)
    failed = {c.name for c in report.checks if c.category == "integrity" and c.passed is False}
    expected = {"unique_account_id", "unique_account_month_key", "null_in_required_columns",
                "invalid_codes", "dates_outside_window", "fk_month_account", "history_span",
                "rows_after_terminal", "close_reason_mismatch", "dpd_skips_bucket",
                "paid_while_rolling", "negative_amounts", "fk_party_account"}
    assert expected <= failed, expected - failed


def test_validate_reads_dirty_csv(clean, tmp_path):
    cf.inject(clean, tmp_path / "csv", seed=2, format="csv")
    report = cf.validate(tmp_path / "csv")  # unreadable dates/numbers must not crash
    assert not report.integrity_ok


def test_score_perfect_empty_and_wrong(dirty):
    key = pl.read_parquet(dirty / "faults.parquet")
    perfect = cf.score(key, key.select("table", "row_key", "column"))
    assert perfect.recall == 1.0 and perfect.precision == 1.0
    empty = cf.score(key, pl.DataFrame({"table": [], "row_key": []}, schema={"table": pl.String,
                                                                               "row_key": pl.String}))
    assert empty.recall == 0.0 and empty.precision == 1.0
    wrong = cf.score(key, pl.DataFrame({"table": ["account"], "row_key": ["A-NOT-A-KEY"]}))
    assert wrong.precision == 0.0 and wrong.recall == 0.0


def test_score_column_must_match_when_given(dirty):
    key = pl.read_parquet(dirty / "faults.parquet").filter(pl.col("column").is_not_null()).head(1)
    ok = key.select("table", "row_key", "column")
    bad = ok.with_columns(pl.lit("not_a_column").alias("column"))
    assert cf.score(key, ok).recall == 1.0
    assert cf.score(key, bad).recall == 0.0


def test_inject_refuses_to_overwrite_clean(clean):
    with pytest.raises(ValueError, match="overwrite"):
        cf.inject(clean, clean)


def test_unknown_fault_type_rejected():
    with pytest.raises(ValidationError, match="unknown fault types"):
        FaultProfile(name="x", rates={"bogus": 0.1})


def test_builtin_fault_profiles():
    assert cf.list_fault_profiles() == ["light", "nasty", "standard"]
    assert cf.load_fault_profile("nasty").allow_overlap is True
    assert set(cf.load_fault_profile("light").rates) == set(FAULT_TYPES)


def test_inject_dataset_without_party_table(clean, tmp_path):
    old = tmp_path / "old"
    shutil.copytree(clean, old)
    shutil.rmtree(old / "account_party")  # as written by creforge 0.1
    manifest = cf.inject(old, tmp_path / "d", seed=1)
    assert "account_party" not in manifest["row_counts"]


def test_cli_inject_and_score(clean, tmp_path):
    runner = CliRunner()
    out = tmp_path / "dirty"
    res = runner.invoke(main, ["inject", str(clean), "-o", str(out), "-f", "light", "-s", "9"])
    assert res.exit_code == 0, res.output
    assert "injected faults" in res.output
    key = pl.read_parquet(out / "faults.parquet")
    key.select("table", "row_key").write_csv(tmp_path / "findings.csv")
    res = runner.invoke(main, ["score", str(out / "faults.parquet"), str(tmp_path / "findings.csv"),
                               "--min-recall", "0.99"])
    assert res.exit_code == 0, res.output
    assert "Recall: 100.0%" in res.output
    assert "standard" in runner.invoke(main, ["faults", "list"]).output
