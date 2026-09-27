"""Public generation API: in-memory ``Dataset`` and streaming ``write_dataset``."""

from __future__ import annotations

import json
import multiprocessing
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl

from . import __version__
from .config import Config
from .generator import MONEY_COLUMNS, MONEY_DECIMAL, SCHEMA_VERSION, TABLES, generate_chunk, make_context

Format = Literal["parquet", "csv"]
MANIFEST = "manifest.json"
PRIVACY_STATEMENT = (
    "Generated entirely from explicit behavioural rules and random draws. No real, "
    "row-level or personal data was read or used; identifiers are keyed permutations "
    "of row counters. Any resemblance to real persons or accounts is coincidental."
)


@dataclass
class Dataset:
    """Generated tables, kept per chunk so validation can stream over them."""

    config: Config
    chunks: list[dict[str, pl.DataFrame]]

    def table(self, name: str) -> pl.DataFrame:
        return pl.concat([c[name] for c in self.chunks])

    @property
    def subject(self) -> pl.DataFrame:
        return self.table("subject")

    @property
    def inquiry(self) -> pl.DataFrame:
        return self.table("inquiry")

    @property
    def account(self) -> pl.DataFrame:
        return self.table("account")

    @property
    def account_month(self) -> pl.DataFrame:
        return self.table("account_month")

    @property
    def account_party(self) -> pl.DataFrame:
        return self.table("account_party")

    def iter_chunks(self) -> Iterator[dict[str, pl.DataFrame]]:
        yield from self.chunks

    def write(self, out: str | Path, format: Format = "parquet") -> dict:
        out = Path(out)
        counts = [_write_chunk(out, i, c, format) for i, c in enumerate(self.chunks)]
        return _write_manifest(out, self.config, format, counts)


def generate(config: Config) -> Dataset:
    """Generate all chunks in memory. Use :func:`write_dataset` for large runs."""
    ctx = make_context(config)
    return Dataset(config, [generate_chunk(config, i, ctx) for i in range(config.n_chunks)])


def write_dataset(
    config: Config, out: str | Path, format: Format = "parquet", workers: int = 1
) -> dict:
    """Generate chunk by chunk straight to disk; memory stays bounded by one chunk per worker.

    Output is byte-identical for a given config regardless of ``workers``.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = range(config.n_chunks)
    if workers <= 1 or config.n_chunks == 1:
        ctx = make_context(config)
        counts = [_write_chunk(out, i, generate_chunk(config, i, ctx), format) for i in jobs]
    else:
        # "spawn": forking after Polars' thread pool has started can deadlock.
        spawn = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=spawn) as pool:
            counts = list(pool.map(_chunk_job, [(config, i, str(out), format) for i in jobs]))
    return _write_manifest(out, config, format, counts)


def _chunk_job(args: tuple[Config, int, str, Format]) -> dict[str, int]:
    config, chunk, out, format = args
    return _write_chunk(Path(out), chunk, generate_chunk(config, chunk), format)


def part_path(out: Path, table: str, chunk: int, format: Format) -> Path:
    return out / table / f"part-{chunk:05d}.{format}"


def _write_chunk(out: Path, chunk: int, frames: dict[str, pl.DataFrame], format: Format) -> dict:
    for name in (t for t in TABLES if t in frames):
        path = part_path(out, name, chunk, format)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = frames[name]
        if format == "parquet":
            df.write_parquet(path, compression="zstd", statistics=True)
        else:
            df.write_csv(path)
    return {name: frames[name].height for name in TABLES if name in frames}


def _write_manifest(out: Path, config: Config, format: Format, counts: list[dict]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "creforge_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "format": format,
        "chunks": config.n_chunks,
        "config_sha256": config.sha256(),
        "row_counts": {t: sum(c.get(t, 0) for c in counts) for t in TABLES},
        "privacy": PRIVACY_STATEMENT,
        "config": config.model_dump(mode="json"),
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


@dataclass
class DiskDataset:
    """A dataset previously written by :func:`write_dataset`, read one chunk at a time."""

    path: Path
    manifest: dict

    @classmethod
    def open(cls, path: str | Path) -> DiskDataset:
        path = Path(path)
        manifest = path / MANIFEST
        if not manifest.is_file():
            raise FileNotFoundError(f"{path} is not a creforge dataset (no {MANIFEST} found)")
        return cls(path, json.loads(manifest.read_text(encoding="utf-8")))

    @property
    def config(self) -> Config:
        return Config.model_validate(self.manifest["config"])

    def iter_chunks(self) -> Iterator[dict[str, pl.DataFrame]]:
        fmt = self.manifest["format"]
        # Datasets written before 0.3 have no "money" setting; they used Float64.
        decimal = self.manifest["config"].get("money", "float") == "decimal"
        for i in range(self.manifest["chunks"]):
            # Datasets written by older versions may lack newer tables (e.g. account_party).
            yield {t: _read(part_path(self.path, t, i, fmt), fmt, t, decimal) for t in TABLES
                   if part_path(self.path, t, i, fmt).exists()}


# CSV loses types; restore the non-string columns explicitly instead of guessing.
_CSV_TYPES: dict[str, pl.DataType] = {
    **dict.fromkeys(("created_month", "inquiry_date", "open_date", "close_date", "as_of_month",
                     "start_date"), pl.Date()),
    **dict.fromkeys(("requested_amount", "credit_limit", "principal", "interest_rate", "balance",
                     "amount_due", "amount_paid"), pl.Float64()),
    **dict.fromkeys(("birth_year", "tenor_months", "months_in_arrears"), pl.Int16()),
    "secured": pl.Boolean(),
}


def _read(path: Path, fmt: str, table: str, decimal: bool) -> pl.DataFrame:
    if fmt == "parquet":
        return pl.read_parquet(path)
    df = pl.read_csv(path, infer_schema_length=0)  # every column as text
    money = MONEY_COLUMNS.get(table, ()) if decimal else ()
    def restore(c: str, t: pl.DataType) -> pl.Expr:
        # Older Polars reads empty CSV fields as "" rather than null.
        col = pl.when(pl.col(c) == "").then(None).otherwise(pl.col(c))
        # strict=False: unreadable values (e.g. in a deliberately corrupted dataset) become
        # null, which `validate` reports as missing required values.
        if t == pl.Date():
            return col.str.to_date(strict=False).alias(c)
        if t == pl.Boolean():
            return (col == "true").alias(c)
        return col.cast(MONEY_DECIMAL if c in money else t, strict=False).alias(c)

    return df.with_columns(restore(c, t) for c, t in _CSV_TYPES.items() if c in df.columns)
