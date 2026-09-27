"""Warehouse DDL for creforge output: Athena, Glue, DuckDB, Postgres, Redshift, Snowflake."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from .config import Profile, load_profile
from .dataset import DiskDataset
from .generator import SCHEMA_VERSION
from .schema import BOOL, CODE, DATE, ID, INT16, MONEY, RATE, SCHEMA, SHORT, Column, Table, code_values

Dialect = Literal["athena", "glue", "duckdb", "postgres", "redshift", "snowflake"]
DIALECTS: tuple[str, ...] = ("athena", "glue", "duckdb", "postgres", "redshift", "snowflake")

# Engines that enforce CHECK constraints; the others get keys only (declared, not enforced).
_CHECKS = {"duckdb", "postgres"}

_SQL_TYPES = {
    "duckdb": {ID: "VARCHAR(13)", SHORT: "VARCHAR(16)", CODE: "VARCHAR(32)", DATE: "DATE",
               INT16: "SMALLINT", RATE: "DOUBLE", BOOL: "BOOLEAN"},
    "postgres": {ID: "VARCHAR(13)", SHORT: "VARCHAR(16)", CODE: "VARCHAR(32)", DATE: "DATE",
                 INT16: "SMALLINT", RATE: "DOUBLE PRECISION", BOOL: "BOOLEAN"},
    "redshift": {ID: "VARCHAR(13)", SHORT: "VARCHAR(16)", CODE: "VARCHAR(32)", DATE: "DATE",
                 INT16: "SMALLINT", RATE: "DOUBLE PRECISION", BOOL: "BOOLEAN"},
    "snowflake": {ID: "VARCHAR(13)", SHORT: "VARCHAR(16)", CODE: "VARCHAR(32)", DATE: "DATE",
                  INT16: "SMALLINT", RATE: "FLOAT", BOOL: "BOOLEAN"},
    # Athena and Glue use Hive types. Small integers are declared `int`: Parquet stores them
    # as 32-bit integers, which every Hive/Trino/Spark reader accepts as int.
    "athena": {ID: "string", SHORT: "string", CODE: "string", DATE: "date", INT16: "int",
               RATE: "double", BOOL: "boolean"},
}
_SQL_TYPES["glue"] = _SQL_TYPES["athena"]
_FLOAT_MONEY = {"duckdb": "DOUBLE", "postgres": "DOUBLE PRECISION", "redshift": "DOUBLE PRECISION",
                "snowflake": "FLOAT", "athena": "double", "glue": "double"}


def _type(dialect: str, col: Column, money: str) -> str:
    if col.type == MONEY:
        if money == "float":
            return _FLOAT_MONEY[dialect]
        return "decimal(18,2)" if dialect in ("athena", "glue") else "DECIMAL(18,2)"
    return _SQL_TYPES[dialect][col.type]


def _qualified(name: str, schema: str | None) -> str:
    return f"{schema}.{name}" if schema else name


def ddl(
    dialect: Dialect,
    *,
    profile: str | Path | Profile = "baseline",
    money: Literal["decimal", "float"] = "decimal",
    format: Literal["parquet", "csv"] = "parquet",
    location: str | None = None,
    database: str | None = None,
    constraints: bool = False,
) -> str:
    """CREATE TABLE statements (or, for ``glue``, Glue ``TableInput`` JSON) for all tables.

    ``database`` qualifies table names (a schema in Postgres/DuckDB/Redshift/Snowflake).
    ``constraints`` adds NOT NULL, primary and foreign keys, and (Postgres, DuckDB)
    CHECK constraints on code columns. Snowflake and Redshift record keys but don't
    enforce them. Athena and Glue need ``location``, the S3 prefix holding the tables.
    """
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; choose one of {', '.join(DIALECTS)}")
    if money not in ("decimal", "float"):
        raise ValueError("money must be 'decimal' or 'float'")
    prof = profile if isinstance(profile, Profile) else load_profile(profile)
    if dialect in ("athena", "glue"):
        if not location:
            raise ValueError(f"{dialect} tables are external: pass location='s3://bucket/prefix'")
        if dialect == "glue":
            return _glue(location.rstrip("/"), format, money)
        return _athena(location.rstrip("/"), format, money, database)
    return _sql(dialect, money, database, constraints, code_values(prof))


def _header(dialect: str, extra: str = "") -> str:
    from . import __version__
    return (f"-- creforge {__version__}: {dialect} DDL for schema version {SCHEMA_VERSION} "
            f"(see docs/data-dictionary.md).{extra}\n")


def _sql(dialect: str, money: str, database: str | None, constraints: bool,
         codes: dict[str, list[str]]) -> str:
    note = " Keys are declared but not enforced by this engine." if (
        constraints and dialect in ("redshift", "snowflake")) else ""
    out = [_header(dialect, note)]
    if database:
        out.append(f"CREATE SCHEMA IF NOT EXISTS {database};\n")
    for table in SCHEMA:
        lines = []
        for col in table.columns:
            line = f"    {col.name} {_type(dialect, col, money)}"
            if constraints and not col.nullable:
                line += " NOT NULL"
            if constraints and col.codes and dialect in _CHECKS:
                allowed = ", ".join(f"'{v}'" for v in codes[col.codes])
                line += f" CHECK ({col.name} IN ({allowed}))"
            lines.append(line)
        if constraints:
            lines.append(f"    PRIMARY KEY ({', '.join(table.primary_key)})")
            for col in table.columns:
                if col.references:
                    ref_table, ref_col = col.references.split(".")
                    lines.append(f"    FOREIGN KEY ({col.name}) REFERENCES "
                                 f"{_qualified(ref_table, database)} ({ref_col})")
        body = ",\n".join(lines)
        out.append(f"CREATE TABLE {_qualified(table.name, database)} (\n{body}\n);\n")
    return "\n".join(out)


def _athena(location: str, fmt: str, money: str, database: str | None) -> str:
    out = [_header("athena")]
    if database:
        out.append(f"CREATE DATABASE IF NOT EXISTS {database};\n")
    for table in SCHEMA:
        cols = ",\n".join(f"    `{c.name}` {_type('athena', c, money)}" for c in table.columns)
        stmt = f"CREATE EXTERNAL TABLE IF NOT EXISTS {_qualified(table.name, database)} (\n{cols}\n)\n"
        if fmt == "parquet":
            stmt += "STORED AS PARQUET\n"
            props = ""
        else:
            stmt += ("ROW FORMAT DELIMITED FIELDS TERMINATED BY ','\n"
                     "STORED AS TEXTFILE\n")
            props = ("\nTBLPROPERTIES ('skip.header.line.count'='1', "
                     "'serialization.null.format'='')")
        stmt += f"LOCATION '{location}/{table.name}/'{props};\n"
        out.append(stmt)
    return "\n".join(out)


def _glue(location: str, fmt: str, money: str) -> str:
    """A JSON array of Glue ``TableInput`` objects, one per table."""
    if fmt == "parquet":
        storage = {
            "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
            "SerdeInfo": {"SerializationLibrary":
                          "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"},
        }
        params = {"classification": "parquet"}
    else:
        storage = {
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe",
                "Parameters": {"field.delim": ",", "serialization.null.format": ""},
            },
        }
        params = {"classification": "csv", "skip.header.line.count": "1"}
    tables = []
    for table in SCHEMA:
        tables.append({
            "Name": table.name,
            "TableType": "EXTERNAL_TABLE",
            "Parameters": dict(params),
            "StorageDescriptor": {
                "Columns": [{"Name": c.name, "Type": _type("glue", c, money)} for c in table.columns],
                "Location": f"{location}/{table.name}/",
                **storage,
            },
        })
    return json.dumps(tables, indent=2) + "\n"


def copy_script(dataset: str | Path, database: str | None = None) -> str:
    """psql ``\\copy`` commands loading a CSV creforge dataset, table by table in key order."""
    ds = DiskDataset.open(dataset)
    if ds.manifest["format"] != "csv":
        raise ValueError("the Postgres \\copy script needs a CSV dataset (creforge generate --format csv)")
    root = Path(dataset).resolve()
    lines = [f"-- Load {root} into Postgres with: psql -f this_file.sql"]
    for table in SCHEMA:
        for part in sorted((root / table.name).glob("part-*.csv")):
            cols = ", ".join(c.name for c in table.columns)
            path = part.as_posix().replace("'", "''")
            lines.append(f"\\copy {_qualified(table.name, database)} ({cols}) FROM '{path}' "
                         "WITH (FORMAT csv, HEADER true, NULL '')")
    return "\n".join(lines) + "\n"


def columns_of(table: str) -> Table:
    return next(t for t in SCHEMA if t.name == table)
