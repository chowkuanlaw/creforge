"""Command-line interface: ``creforge generate | validate | profiles``."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click
import yaml

from . import __version__
from .config import Config, list_profiles, load_profile
from .dataset import write_dataset
from .validate import validate


@click.group()
@click.version_option(__version__, prog_name="creforge")
def main() -> None:
    """PII-safe synthetic credit bureau data from explicit behavioural rules."""


@main.command()
@click.option("--profile", "-p", default="baseline", show_default=True,
              help="Built-in profile name or path to a YAML profile.")
@click.option("--subjects", "-n", type=click.IntRange(min=1), default=10_000, show_default=True)
@click.option("--months", "-m", type=click.IntRange(12, 120), default=36, show_default=True)
@click.option("--seed", "-s", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--start-month", default="2023-01", show_default=True, help="YYYY-MM")
@click.option("--out", "-o", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--format", "fmt", type=click.Choice(["parquet", "csv"]), default="parquet",
              show_default=True)
@click.option("--workers", "-w", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--chunk-size", type=click.IntRange(min=1), default=50_000, show_default=True)
def generate(profile, subjects, months, seed, start_month, out, fmt, workers, chunk_size) -> None:
    """Generate a dataset into OUT (one part file per chunk, plus manifest.json)."""
    cfg = Config.from_profile(profile, subjects=subjects, months=months, seed=seed,
                              start_month=start_month, chunk_size=chunk_size)
    t0 = time.perf_counter()
    manifest = write_dataset(cfg, out, format=fmt, workers=workers)
    secs = time.perf_counter() - t0
    click.echo(f"Wrote {out} in {secs:.1f}s (profile={cfg.profile.name}, seed={seed})")
    for table, n in manifest["row_counts"].items():
        click.echo(f"  {table:<14} {n:>14,}")


@main.command(name="validate")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
@click.option("--report", type=click.Path(dir_okay=False, path_type=Path),
              help="Also write the Markdown report to this file.")
@click.option("--strict", is_flag=True, help="Exit non-zero on calibration failures too.")
def validate_cmd(path, as_json, report, strict) -> None:
    """Check integrity and calibration of a dataset written by `generate`."""
    rep = validate(path)
    md = rep.to_markdown()
    if report:
        report.write_text(md, encoding="utf-8")
    click.echo(json.dumps(rep.to_dict(), indent=2, default=str) if as_json else md)
    if not rep.integrity_ok or (strict and not rep.calibration_ok):
        sys.exit(1)


@main.group()
def profiles() -> None:
    """Inspect built-in profiles."""


@profiles.command(name="list")
def profiles_list() -> None:
    for name in list_profiles():
        desc = " ".join(load_profile(name).description.split())
        click.echo(f"{name:<12} {desc}")


@profiles.command(name="show")
@click.argument("name")
def profiles_show(name) -> None:
    """Print the fully resolved profile (inheritance applied) as YAML."""
    click.echo(yaml.safe_dump(load_profile(name).model_dump(mode="json"), sort_keys=False))


if __name__ == "__main__":
    main()
