"""Optional, unmistakably synthetic display identities for UI test data."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from .dataset import DiskDataset, part_path


def identities(dataset: str | Path | DiskDataset) -> pl.DataFrame:
    """Return unmistakably fake display and contact values for UI testing.

    The identifiers are derived only from synthetic ``subject_id`` values. The
    ``example.invalid`` domain cannot receive mail, phone numbers have an invalid
    000 area code, and names and addresses say Synthetic. No personal records or
    name lists are read.
    """
    ds = dataset if isinstance(dataset, DiskDataset) else DiskDataset.open(dataset)
    fmt = ds.manifest["format"]
    subjects = []
    for chunk in range(ds.manifest["chunks"]):
        path = part_path(ds.path, "subject", chunk, fmt)
        if fmt == "parquet":
            subjects.append(pl.read_parquet(path, columns=["subject_id"]))
        else:
            subjects.append(pl.read_csv(path, columns=["subject_id"]))
    digits = pl.int_range(pl.len()).cast(pl.String).str.zfill(7)
    return pl.concat(subjects).with_columns(
        pl.concat_str(pl.lit("Synthetic Subject "), pl.col("subject_id")).alias("full_name"),
        pl.concat_str(pl.lit("subject+"), pl.col("subject_id").str.to_lowercase(),
                      pl.lit("@example.invalid")).alias("email"),
        pl.concat_str(pl.lit("000-"), digits.str.slice(0, 3), pl.lit("-"),
                      digits.str.slice(3)).alias("phone"),
        pl.concat_str(pl.lit("SYNTHETIC NO DELIVERY "), pl.col("subject_id")).alias("address_line_1"),
    )
