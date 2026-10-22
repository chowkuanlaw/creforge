# dbt with creforge and DuckDB

This example declares all five creforge tables as dbt sources and builds a monthly
portfolio summary view. It uses only generated data.

From the repository root, with `creforge` installed:

```sh
python -m pip install dbt-duckdb
creforge generate --subjects 1000 --months 12 --seed 42 --out /tmp/creforge-dbt-data
creforge load duckdb /tmp/creforge-dbt-data --db /tmp/creforge-dbt.duckdb
export CREFORGE_DUCKDB_PATH=/tmp/creforge-dbt.duckdb
dbt run --project-dir examples/dbt --profiles-dir examples/dbt
dbt test --project-dir examples/dbt --profiles-dir examples/dbt
```

Set `CREFORGE_DUCKDB_PATH` to an absolute path. The project reads creforge's tables
from DuckDB's `main` schema and writes its view to `analytics` in the same database.
Use a new database path for each run, or pass `--replace` to `creforge load duckdb`
when reloading an existing one.
