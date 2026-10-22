"""Load generated tables into an Apache Iceberg SQL catalog."""

from __future__ import annotations

from itertools import chain
from pathlib import Path

import polars as pl

from .dataset import DiskDataset
from .generator import TABLES


def load_iceberg(dataset: str | Path, catalog_uri: str, warehouse: str, *,
                 namespace: str = "creforge", replace: bool = False) -> dict[str, int]:
    """Load a dataset into Iceberg tables, returning the row count of each table.

    ``catalog_uri`` is a SQLAlchemy URI, and ``warehouse`` is an Iceberg warehouse
    URI. For a local catalog these are ``sqlite:////path/catalog.db`` and
    ``file:///path/warehouse``. Install ``creforge[iceberg]`` first.
    """
    try:
        from pyiceberg.catalog import load_catalog
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError("load_iceberg needs PyIceberg: pip install 'creforge[iceberg]'") from exc

    source = DiskDataset.open(dataset)
    chunks = source.iter_chunks()
    first = next(chunks, None)
    if first is None:
        raise ValueError("dataset has no chunks")
    names = [name for name in TABLES if name in first]
    catalog = load_catalog("creforge", type="sql", uri=catalog_uri, warehouse=warehouse)
    catalog.create_namespace_if_not_exists(namespace)
    identifiers = {name: f"{namespace}.{name}" for name in names}
    existing = [name for name, identifier in identifiers.items() if catalog.table_exists(identifier)]
    if existing and not replace:
        raise ValueError(f"Iceberg tables already exist: {', '.join(existing)}; use replace=True to reload")
    for name in existing:
        catalog.purge_table(identifiers[name])

    tables = {}
    counts = dict.fromkeys(names, 0)
    for frames in chain((first,), chunks):
        for name in names:
            frame = frames[name]
            enum_cols = [col for col, dtype in frame.schema.items() if dtype in (pl.Enum, pl.Categorical)]
            if enum_cols:
                frame = frame.with_columns(pl.col(col).cast(pl.String) for col in enum_cols)
            arrow = frame.to_arrow()
            if name not in tables:
                tables[name] = catalog.create_table(identifiers[name], schema=arrow.schema)
            if arrow.num_rows:
                tables[name].append(arrow)
                counts[name] += arrow.num_rows
    return counts
