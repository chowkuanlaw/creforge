import os
import shutil
import subprocess
from pathlib import Path

import polars as pl
import pytest
from click.testing import CliRunner

import creforge as cf
from creforge.cli import main
from creforge.ddl import DIALECTS
from creforge.schema import BOOL, CODE, DATE, ID, INT16, MONEY, RATE, SCHEMA, SHORT

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.environ.get("CREFORGE_UPDATE_SNAPSHOTS") == "1"


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("parquet")
    cf.write_dataset(cf.Config.from_profile("baseline", subjects=600, months=12, seed=3), path)
    return path


@pytest.fixture(scope="module")
def csv_dataset(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("csv")
    cf.write_dataset(cf.Config.from_profile("baseline", subjects=600, months=12, seed=3), path,
                     format="csv")
    return path


@pytest.fixture(scope="module")
def dirty(dataset, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("dirty")
    cf.inject(dataset, path, faults="standard", seed=1)
    return path


# --- the schema description matches real output ----------------------------------------------

def _polars_ok(logical: str, dtype: pl.DataType, money: str) -> bool:
    if logical in (ID, SHORT):
        return dtype == pl.String
    if logical == CODE:
        return dtype == pl.String or isinstance(dtype, pl.Enum)
    return {
        DATE: dtype == pl.Date, INT16: dtype == pl.Int16, RATE: dtype == pl.Float64,
        BOOL: dtype == pl.Boolean,
        MONEY: dtype == (pl.Decimal(precision=18, scale=2) if money == "decimal" else pl.Float64),
    }[logical]


@pytest.mark.parametrize("money", ["decimal", "float"])
def test_schema_matches_generated_output(money):
    ds = cf.generate(cf.Config.from_profile("baseline", subjects=400, months=12, seed=2, money=money))
    for table in SCHEMA:
        df = ds.table(table.name)
        assert df.columns == [c.name for c in table.columns], table.name
        for col in table.columns:
            assert _polars_ok(col.type, df.schema[col.name], money), (table.name, col.name)
            if not col.nullable:
                assert df[col.name].null_count() == 0, (table.name, col.name)


# --- snapshots (engines not available in CI: Athena, Glue, Redshift, Snowflake) ---------------

CASES = {
    "athena_parquet.sql": dict(dialect="athena", location="s3://bucket/creforge", database="creforge"),
    "athena_csv.sql": dict(dialect="athena", location="s3://bucket/creforge", format="csv"),
    "glue_parquet.json": dict(dialect="glue", location="s3://bucket/creforge"),
    "glue_csv.json": dict(dialect="glue", location="s3://bucket/creforge", format="csv"),
    "redshift_constraints.sql": dict(dialect="redshift", constraints=True, database="creforge"),
    "snowflake_constraints.sql": dict(dialect="snowflake", constraints=True),
    "duckdb_constraints.sql": dict(dialect="duckdb", constraints=True),
    "postgres_constraints_float.sql": dict(dialect="postgres", constraints=True, money="float"),
}


def _strip_header(text: str) -> str:
    # The first line names the creforge version; everything else must be stable.
    return text.split("\n", 1)[1] if text.startswith("--") else text


@pytest.mark.parametrize("name", sorted(CASES))
def test_ddl_snapshot(name):
    kwargs = dict(CASES[name])
    text = _strip_header(cf.ddl(kwargs.pop("dialect"), **kwargs))
    path = SNAPSHOTS / name
    if UPDATE or not path.exists():
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8"), f"regenerate with CREFORGE_UPDATE_SNAPSHOTS=1: {name}"


def test_ddl_arguments():
    with pytest.raises(ValueError, match="location"):
        cf.ddl("athena")
    with pytest.raises(ValueError, match="unknown dialect"):
        cf.ddl("oracle")
    assert set(DIALECTS) == {"athena", "glue", "duckdb", "postgres", "redshift", "snowflake"}


# --- DuckDB: executed for real -----------------------------------------------------------------

duckdb = pytest.importorskip("duckdb")


def test_duckdb_loads_clean_data_with_all_constraints(dataset, tmp_path):
    counts = cf.load_duckdb(dataset, tmp_path / "c.duckdb", constraints=True)
    manifest = cf.DiskDataset.open(dataset).manifest
    assert counts == {t: n for t, n in manifest["row_counts"].items()}
    con = duckdb.connect(str(tmp_path / "c.duckdb"))
    types = dict(con.execute("select column_name, data_type from information_schema.columns "
                             "where table_name = 'account_month'").fetchall())
    assert types["balance"] == "DECIMAL(18,2)" and types["as_of_month"] == "DATE"


def test_duckdb_loads_csv(csv_dataset, tmp_path):
    counts = cf.load_duckdb(csv_dataset, tmp_path / "csv.duckdb", constraints=True)
    assert counts["account_month"] == cf.DiskDataset.open(csv_dataset).manifest["row_counts"]["account_month"]


def test_duckdb_dirty_loads_without_constraints_and_fails_with(dirty, tmp_path):
    counts = cf.load_duckdb(dirty, tmp_path / "d.duckdb")
    assert counts["account_month"] == cf.DiskDataset.open(dirty).manifest["row_counts"]["account_month"]
    with pytest.raises(duckdb.ConstraintException):
        cf.load_duckdb(dirty, tmp_path / "d2.duckdb", constraints=True)


def test_duckdb_replace(dataset, tmp_path):
    db = tmp_path / "r.duckdb"
    cf.load_duckdb(dataset, db)
    counts = cf.load_duckdb(dataset, db, replace=True)
    assert counts["subject"] == 600


# --- Postgres: executed for real when a server is available ------------------------------------

@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    """(uri, psql) from CREFORGE_TEST_POSTGRES_URL (CI service), else an embedded pgserver."""
    uri = os.environ.get("CREFORGE_TEST_POSTGRES_URL")
    server = None
    if not uri:
        pgserver = pytest.importorskip("pgserver")
        server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
        uri = server.get_uri()
    psql = shutil.which("psql")
    if server is not None and psql is None:
        bundled = list(Path(pgserver.__file__).parent.rglob("psql"))
        psql = str(bundled[0]) if bundled else None
    if psql is None:
        if server is None:  # CI promised a server: don't let the test silently skip
            pytest.fail("CREFORGE_TEST_POSTGRES_URL is set but psql is not on PATH")
        pytest.skip("psql not found")
    yield uri, psql
    if server is not None:
        server.cleanup()


def _psql(postgres, *args, check=True):
    uri, psql = postgres
    return subprocess.run([psql, uri, "-v", "ON_ERROR_STOP=1", "-q", *args],
                          capture_output=True, text=True, check=check)


def test_postgres_ddl_copy_and_constraints(postgres, csv_dataset, tmp_path):
    script = tmp_path / "load.sql"
    script.write_text("DROP SCHEMA IF EXISTS cf_test CASCADE;\n"
                      + cf.ddl("postgres", constraints=True, database="cf_test")
                      + cf.copy_script(csv_dataset, database="cf_test"), encoding="utf-8")
    _psql(postgres, "-f", str(script))
    out = _psql(postgres, "-At", "-c", "select count(*) from cf_test.account_month").stdout.strip()
    assert int(out) == cf.DiskDataset.open(csv_dataset).manifest["row_counts"]["account_month"]
    bad = _psql(postgres, "-c", "insert into cf_test.account_month select account_id, "
                "as_of_month + interval '100 years', balance, amount_due, amount_paid, '999', "
                "months_in_arrears, status from cf_test.account_month limit 1", check=False)
    assert bad.returncode != 0 and "check constraint" in bad.stderr


def test_copy_script_needs_csv(dataset):
    with pytest.raises(ValueError, match="CSV"):
        cf.copy_script(dataset)


# --- CLI -------------------------------------------------------------------------------------------

def test_cli_ddl_and_load(dataset, tmp_path):
    runner = CliRunner()
    res = runner.invoke(main, ["ddl", "-d", "athena", "--location", "s3://b/p", "--dataset", str(dataset)])
    assert res.exit_code == 0 and "decimal(18,2)" in res.output, res.output
    res = runner.invoke(main, ["ddl", "-d", "glue"])
    assert res.exit_code != 0 and "location" in res.output
    res = runner.invoke(main, ["ddl", "-d", "duckdb", "--copy-script", str(dataset)])
    assert res.exit_code != 0 and "postgres" in res.output
    out = tmp_path / "schema.sql"
    assert runner.invoke(main, ["ddl", "-d", "postgres", "-o", str(out)]).exit_code == 0
    assert "CREATE TABLE account_month" in out.read_text()
    res = runner.invoke(main, ["load", "duckdb", str(dataset), "--db", str(tmp_path / "x.duckdb")])
    assert res.exit_code == 0 and "account_month" in res.output, res.output
