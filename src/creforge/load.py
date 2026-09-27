"""Load a creforge dataset into DuckDB (optional dependency: ``pip install creforge[duckdb]``)."""

from __future__ import annotations

from pathlib import Path

from .dataset import DiskDataset
from .ddl import _type, ddl
from .schema import SCHEMA


def load_duckdb(dataset: str | Path, db: str | Path, *, constraints: bool = False,
                replace: bool = False) -> dict[str, int]:
    """Create the tables in DuckDB database file ``db`` and load every part file.

    Values are loaded with ``TRY_CAST``, so a corrupted dataset (``creforge inject``)
    still loads: unreadable values become NULL. With ``constraints=True`` the tables get
    NOT NULL, keys and CHECK constraints, so DuckDB itself rejects bad rows instead.
    Returns the row count loaded into each table.
    """
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError("load_duckdb needs DuckDB: pip install 'creforge[duckdb]'") from exc

    src = DiskDataset.open(dataset)
    fmt = src.manifest["format"]
    money = src.manifest["config"].get("money", "float")
    root = Path(dataset).resolve()
    con = duckdb.connect(str(db))
    try:
        if replace:
            for table in reversed(SCHEMA):
                con.execute(f"DROP TABLE IF EXISTS {table.name}")
        con.execute(ddl("duckdb", profile=src.config.profile, money=money, constraints=constraints))
        counts = {}
        for table in SCHEMA:
            parts = sorted((root / table.name).glob(f"part-*.{fmt}"))
            if not parts:
                continue  # e.g. account_party in a creforge 0.1 dataset
            files = "[" + ", ".join("'" + p.as_posix().replace("'", "''") + "'" for p in parts) + "]"
            reader = (f"read_parquet({files})" if fmt == "parquet"
                      else f"read_csv({files}, header=true, all_varchar=true, nullstr='')")
            select = ", ".join(f"TRY_CAST({c.name} AS {_type('duckdb', c, money)})" for c in table.columns)
            con.execute(f"INSERT INTO {table.name} SELECT {select} FROM {reader}")
            counts[table.name] = con.execute(f"SELECT count(*) FROM {table.name}").fetchone()[0]
        return counts
    finally:
        con.close()
