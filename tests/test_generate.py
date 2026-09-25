import hashlib
from pathlib import Path

import polars as pl

import creforge as cf


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_small_dataset_passes_integrity(small_dataset):
    report = cf.validate(small_dataset)
    failed = [c for c in report.checks if c.category == "integrity" and not c.passed]
    assert not failed, failed


def test_schema(small_dataset):
    am = small_dataset.account_month
    assert am.columns == ["account_id", "as_of_month", "balance", "amount_due", "amount_paid",
                          "dpd_bucket", "months_in_arrears", "status"]
    acc = small_dataset.account
    rev = acc.filter(pl.col("product_type").is_in(["credit_card", "overdraft"]))
    assert rev["principal"].null_count() == rev.height and rev["tenor_months"].null_count() == rev.height
    inst = acc.filter(~pl.col("product_type").is_in(["credit_card", "overdraft"]))
    assert inst["credit_limit"].null_count() == inst.height
    assert small_dataset.subject.height == 3000


def test_account_party(small_dataset):
    ap = small_dataset.table("account_party")
    assert ap.columns == ["account_id", "subject_id", "role", "start_date"]
    roles = dict(ap["role"].cast(pl.String).value_counts().iter_rows())
    assert roles["primary"] == small_dataset.account.height
    assert roles["joint"] > 0 and roles["guarantor"] > 0


def test_parties_can_be_disabled():
    prof = cf.load_profile("baseline").model_dump()
    prof["parties"]["enabled"] = False
    cfg = cf.Config(profile=cf.Profile.model_validate(prof), subjects=1000, months=12, seed=1)
    ds = cf.generate(cfg)
    assert set(ds.table("account_party")["role"].cast(pl.String).unique()) == {"primary"}
    assert cf.validate(ds).integrity_ok


def test_validates_dataset_without_party_table(tmp_path):
    import shutil
    cfg = cf.Config.from_profile("baseline", subjects=500, months=12, seed=8)
    cf.write_dataset(cfg, tmp_path)
    shutil.rmtree(tmp_path / "account_party")  # as written by creforge 0.1
    report = cf.validate(tmp_path)
    assert report.integrity_ok
    assert all(c.passed is None for c in report.checks if c.name.startswith("party_"))


def test_no_pii_columns(small_dataset):
    cols = {c for name in ("subject", "inquiry", "account") for c in small_dataset.table(name).columns}
    assert not cols & {"name", "national_id", "address", "phone", "email", "dob"}


def test_determinism_and_worker_independence(tmp_path, small_config):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    cf.write_dataset(small_config, a, workers=1)
    cf.write_dataset(small_config, b, workers=2)
    cf.write_dataset(small_config.model_copy(update={"seed": 124}), c, workers=1)
    ha, hb, hc = _hashes(a), _hashes(b), _hashes(c)
    assert ha == hb
    assert ha["subject/part-00000.parquet"] != hc["subject/part-00000.parquet"]


def test_in_memory_matches_disk(tmp_path, small_config, small_dataset):
    cf.write_dataset(small_config, tmp_path)
    disk = pl.concat([ch["account_month"] for ch in cf.DiskDataset.open(tmp_path).iter_chunks()])
    assert disk.equals(small_dataset.account_month)


def test_csv_roundtrip_validates(tmp_path):
    cfg = cf.Config.from_profile("baseline", subjects=500, months=12, seed=5)
    cf.write_dataset(cfg, tmp_path, format="csv")
    assert (tmp_path / "account_month" / "part-00000.csv").exists()
    assert cf.validate(tmp_path).integrity_ok


def test_manifest(tmp_path, small_config):
    manifest = cf.write_dataset(small_config, tmp_path)
    assert manifest["config_sha256"] == small_config.sha256()
    assert manifest["row_counts"]["subject"] == 3000
    assert cf.DiskDataset.open(tmp_path).config == small_config


def test_decimal_money_roundtrip(tmp_path):
    from decimal import Decimal

    cfg = cf.Config.from_profile("baseline", subjects=800, months=12, seed=4, money="decimal")
    manifest = cf.write_dataset(cfg, tmp_path)
    assert manifest["config"]["money"] == "decimal"
    am = pl.concat([c["account_month"] for c in cf.DiskDataset.open(tmp_path).iter_chunks()])
    assert am.schema["balance"] == pl.Decimal(precision=18, scale=2)
    assert isinstance(am["balance"][0], Decimal)
    assert cf.validate(tmp_path).integrity_ok


def test_manifest_schema_version(tmp_path):
    from creforge.generator import SCHEMA_VERSION

    manifest = cf.write_dataset(cf.Config.from_profile("baseline", subjects=100, months=12), tmp_path)
    assert manifest["schema_version"] == SCHEMA_VERSION
