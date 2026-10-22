import polars as pl
from click.testing import CliRunner

import creforge as cf
from creforge.cli import main


def test_identity_overlay_is_unique_and_identical_for_csv_and_parquet(tmp_path):
    ds = cf.generate(cf.Config.from_profile("baseline", subjects=120, months=12, seed=7))
    ds.write(tmp_path / "parquet")
    ds.write(tmp_path / "csv", format="csv")

    parquet = cf.identities(tmp_path / "parquet")
    assert parquet.equals(cf.identities(tmp_path / "csv"))
    assert parquet.height == 120
    assert parquet["subject_id"].n_unique() == parquet["email"].n_unique() == 120
    assert parquet["phone"].n_unique() == 120
    assert parquet["full_name"].str.starts_with("Synthetic Subject ").all()
    assert parquet["email"].str.ends_with("@example.invalid").all()
    assert parquet["phone"].str.starts_with("000-").all()
    assert parquet["address_line_1"].str.starts_with("SYNTHETIC NO DELIVERY ").all()
    assert set(parquet["subject_id"]) == set(ds.subject["subject_id"])


def test_identity_cli_writes_both_formats(tmp_path):
    ds = cf.generate(cf.Config.from_profile("baseline", subjects=30, months=12, seed=3))
    ds.write(tmp_path / "data")
    runner = CliRunner()
    parquet = tmp_path / "identities.parquet"
    csv = tmp_path / "identities.csv"
    for out in (parquet, csv):
        result = runner.invoke(main, ["identities", str(tmp_path / "data"), "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert "30 synthetic identities" in result.output
    assert pl.read_parquet(parquet).equals(pl.read_csv(csv))
