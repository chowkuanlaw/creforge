# Loading creforge data into a warehouse

creforge knows the exact output schema, so it writes the table definitions for you.
`creforge ddl` prints `CREATE TABLE` statements (or Glue catalog JSON) for six targets,
and `creforge load duckdb` loads a dataset into DuckDB in one step.

```console
$ creforge generate -n 50000 -o out
$ creforge ddl --dialect postgres --dataset out --constraints -o schema.sql
$ creforge load duckdb out --db creforge.duckdb
```

The DDL is generated from one internal description of the schema, which a test checks
against real generated output column by column. Column meanings are in the
[data dictionary](data-dictionary.md).

## `creforge ddl`

| Option | Meaning |
|---|---|
| `-d, --dialect` | `athena`, `glue`, `duckdb`, `postgres`, `redshift` or `snowflake` |
| `--dataset DIR` | Take the profile, money type and file format from this dataset's manifest (recommended) |
| `-p, --profile` | Profile whose codes the CHECK constraints allow, when `--dataset` isn't given (default `baseline`) |
| `--money` | `decimal` (`DECIMAL(18,2)`, the default) or `float`, matching `generate --money` |
| `--format` | `parquet` or `csv`; changes the Athena/Glue storage clauses only |
| `--location` | S3 prefix holding the table folders (Athena and Glue; required there) |
| `--database` | Database or schema to create the tables in |
| `--constraints` | Add NOT NULL, primary and foreign keys and, on Postgres and DuckDB, `CHECK` constraints on code columns |
| `--copy-script DIR` | Postgres only: append psql `\copy` commands that load this CSV dataset |
| `-o FILE` | Write to a file instead of printing |

From Python: `cf.ddl("postgres", constraints=True)` and `cf.copy_script("out")`.

### Types

| Logical type | DuckDB / Postgres / Redshift / Snowflake | Athena / Glue |
|---|---|---|
| ids (`subject_id`, `account_id`, …) | `VARCHAR(13)` | `string` |
| short text (`region`, `lender_id`) | `VARCHAR(16)` | `string` |
| codes (`status`, `dpd_bucket`, …) | `VARCHAR(32)` | `string` |
| dates | `DATE` | `date` |
| small integers | `SMALLINT` | `int` |
| money | `DECIMAL(18,2)`, or `DOUBLE`/`DOUBLE PRECISION`/`FLOAT` with `--money float` | `decimal(18,2)` or `double` |
| `interest_rate` | `DOUBLE` / `DOUBLE PRECISION` / `FLOAT` | `double` |
| `secured` | `BOOLEAN` | `boolean` |

Athena and Glue declare small integers as `int` because Parquet stores them as 32-bit
integers, which every Hive, Trino and Spark reader accepts as `int`.

### Constraints

Constraints are **off by default**, so that corrupted datasets from `creforge inject`
load too. With `--constraints`:

- Postgres and DuckDB enforce NOT NULL, primary keys, foreign keys and the allowed
  values of every code column. Loading a dirty dataset then fails, which is itself a
  useful test.
- Redshift and Snowflake accept primary and foreign keys but don't enforce them (the
  generated file says so). They get no `CHECK` constraints.
- Athena and Glue have no constraints; the option is ignored.

### Per dialect

**Athena.** `CREATE EXTERNAL TABLE … LOCATION '<location>/<table>/'`. Upload each table
folder of a dataset (`out/account_month/part-*.parquet`, …) under that prefix. For CSV
datasets, the tables skip the header line and read empty fields as NULL.

```console
$ aws s3 sync out s3://my-bucket/creforge/ --exclude "*" --include "*/part-*"
$ creforge ddl -d athena --dataset out --location s3://my-bucket/creforge --database creforge_test
```

**Glue.** A JSON array with one Glue `TableInput` per table. Create them with the AWS
CLI, one table at a time:

```console
$ creforge ddl -d glue --dataset out --location s3://my-bucket/creforge -o tables.json
$ python -c "import json; [print(json.dumps(t)) for t in json.load(open('tables.json'))]" |
    while read -r t; do aws glue create-table --database-name creforge_test --table-input "$t"; done
```

**Postgres.** Generate a CSV dataset and a single script that creates and loads it:

```console
$ creforge generate -n 50000 --format csv -o out
$ creforge ddl -d postgres --dataset out --constraints --copy-script out -o load.sql
$ psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f load.sql
```

`\copy` reads the files on the machine running psql, so the server can be remote.
Tables are loaded parents first, so foreign keys are satisfied.

**DuckDB.** Use `creforge load duckdb` (below), or run the DDL yourself.

**Redshift and Snowflake.** Ordinary `CREATE TABLE` statements. Load the Parquet files
with `COPY … FORMAT AS PARQUET` (Redshift) or a stage and `COPY INTO … FILE_FORMAT =
(TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE` (Snowflake).

## `creforge load duckdb`

```console
$ pip install "creforge[duckdb]"
$ creforge load duckdb out --db creforge.duckdb [--constraints] [--replace]
```

Creates the tables and loads every part file with DuckDB's own Parquet or CSV reader.
Values are loaded with `TRY_CAST`, so a dirty dataset loads too: unreadable values
(such as `"N/A"` in a number column) become NULL. `--constraints` makes DuckDB reject
bad rows instead, and `--replace` drops existing creforge tables first. From Python:
`cf.load_duckdb("out", "creforge.duckdb")` returns the row count per table.

## How this is tested

| Target | Test |
|---|---|
| DuckDB | Every CI run creates the tables, loads Parquet and CSV datasets with and without constraints, and checks that constraints reject a dirty dataset. |
| Postgres | A CI job starts a real Postgres 16, runs the DDL with constraints and the `\copy` script, checks row counts, and checks that an unknown code is rejected. |
| Athena, Glue, Redshift, Snowflake | Snapshot tests of the generated SQL and JSON, written against each engine's documented syntax. **They are not run against the real services in CI.** Please [open an issue](https://github.com/chowkuanlaw/creforge/issues) if a statement fails on your engine. |
