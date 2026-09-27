"""Command-line interface: ``creforge generate | validate | profiles``."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click
import yaml
from pydantic import ValidationError

from . import __version__
from .config import Config, list_profiles, load_profile
from .dataset import DiskDataset, write_dataset
from .ddl import DIALECTS, copy_script, ddl
from .faults import inject, list_fault_profiles, load_fault_profile, score
from .reconcile import reconcile
from .submissions import list_issue_profiles, load_issue_profile, submissions
from .validate import validate


class _Group(click.Group):
    """Turn expected user errors into a one-line message instead of a traceback."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ValidationError as exc:
            raise click.ClickException(f"invalid configuration:\n{exc}") from exc
        except (FileNotFoundError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_Group)
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
@click.option("--money", type=click.Choice(["decimal", "float"]), default="decimal", show_default=True,
              help="Money columns as exact Decimal(18, 2) or Float64 (2 dp).")
def generate(profile, subjects, months, seed, start_month, out, fmt, workers, chunk_size, money) -> None:
    """Generate a dataset into OUT (one part file per chunk, plus manifest.json)."""
    cfg = Config.from_profile(profile, subjects=subjects, months=months, seed=seed,
                              start_month=start_month, chunk_size=chunk_size, money=money)
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


@main.command(name="inject")
@click.argument("clean", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--faults", "-f", default="standard", show_default=True,
              help="Built-in fault profile (light, standard, nasty) or path to a YAML profile.")
@click.option("--seed", "-s", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--out", "-o", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--format", "fmt", type=click.Choice(["parquet", "csv"]), default=None,
              help="Output format. Defaults to the clean dataset's. CSV enables the CSV-only faults.")
def inject_cmd(clean, faults, seed, out, fmt) -> None:
    """Write a corrupted copy of dataset CLEAN, with an answer key (faults.parquet)."""
    manifest = inject(clean, out, faults=faults, seed=seed, format=fmt)
    info = manifest["faults"]
    total = sum(info["counts"].values())
    click.echo(f"Wrote {out} with {total:,} injected faults (profile={info['profile']['name']}, "
               f"seed={seed}); answer key: {out / info['answer_key']}")
    for fault, n in info["counts"].items():
        click.echo(f"  {fault:<24} {n:>10,}")
    for fault, why in info["skipped"].items():
        click.echo(f"  {fault:<24} {'skipped':>10}  ({why})")


@main.command(name="score")
@click.argument("answer_key", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("findings", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit the score as JSON.")
@click.option("--min-recall", type=click.FloatRange(0, 1), default=None,
              help="Exit non-zero if recall is below this (for CI).")
def score_cmd(answer_key, findings, as_json, min_recall) -> None:
    """Grade data-quality FINDINGS (CSV or Parquet: table, row_key[, column]) against ANSWER_KEY."""
    rep = score(answer_key, findings)
    click.echo(json.dumps(rep.to_dict(), indent=2) if as_json else rep.to_markdown())
    if min_recall is not None and rep.recall < min_recall:
        sys.exit(1)


@main.command(name="submissions")
@click.argument("dataset", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--issues", "-i", default="standard", show_default=True,
              help="Delivery-issue profile (none, light, standard, nasty) or path to a YAML profile.")
@click.option("--seed", "-s", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--out", "-o", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--format", "fmt", type=click.Choice(["parquet", "csv"]), default=None,
              help="File format. Defaults to the dataset's.")
def submissions_cmd(dataset, issues, seed, out, fmt) -> None:
    """Split DATASET into monthly lender submission files, with delivery issues."""
    m = submissions(dataset, out, issues=issues, seed=seed, format=fmt)
    click.echo(f"Wrote {m['submissions']:,} submission files ({m['rows']:,} rows) to {out} "
               f"(issues={m['profile']['name']}, seed={seed})")
    click.echo(f"  delivery log: {out / m['log']}; answer key: {out / m['answer_key']}")
    for issue, n in m["issue_counts"].items():
        click.echo(f"  {issue:<24} {n:>8,} deliveries")


@main.command(name="reconcile")
@click.argument("dataset", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("rebuilt", type=click.Path(exists=True, path_type=Path))
@click.option("--inbox", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="The submissions inbox, to name the delivery issue behind each difference.")
@click.option("--max-differences", type=click.IntRange(min=0), default=0, show_default=True,
              help="Exit non-zero if there are more differences than this.")
@click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
def reconcile_cmd(dataset, rebuilt, inbox, max_differences, as_json) -> None:
    """Check REBUILT (account/ and account_month/ folders, or a DuckDB file) against DATASET."""
    rep = reconcile(dataset, rebuilt, inbox=inbox)
    click.echo(json.dumps(rep.to_dict(), indent=2, default=str) if as_json else rep.to_markdown())
    if rep.differences > max_differences or any(t.missing_columns for t in rep.tables.values()):
        sys.exit(1)


@main.group()
def issues() -> None:
    """Inspect built-in delivery-issue profiles."""


@issues.command(name="list")
def issues_list() -> None:
    for name in list_issue_profiles():
        click.echo(f"{name:<12} {load_issue_profile(name).description}")


@issues.command(name="show")
@click.argument("name")
def issues_show(name) -> None:
    """Print the fully resolved delivery-issue profile as YAML."""
    click.echo(yaml.safe_dump(load_issue_profile(name).model_dump(mode="json"), sort_keys=False))


@main.command(name="ddl")
@click.option("--dialect", "-d", type=click.Choice(DIALECTS), required=True)
@click.option("--profile", "-p", default="baseline", show_default=True,
              help="Profile whose codes the CHECK constraints allow (ignored when --dataset is given).")
@click.option("--dataset", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Take the profile, money type and format from this dataset's manifest.")
@click.option("--money", type=click.Choice(["decimal", "float"]), default=None,
              help="Money column type [default: decimal, or the dataset's].")
@click.option("--format", "fmt", type=click.Choice(["parquet", "csv"]), default=None,
              help="Storage format for Athena/Glue [default: parquet, or the dataset's].")
@click.option("--location", help="S3 prefix holding the tables (Athena, Glue), e.g. s3://bucket/creforge")
@click.option("--database", help="Database/schema to create the tables in.")
@click.option("--constraints", is_flag=True,
              help="Add NOT NULL, primary/foreign keys and (Postgres, DuckDB) CHECK constraints.")
@click.option("--copy-script", "copy_from", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Postgres only: append psql \\copy commands that load this CSV dataset.")
@click.option("--out", "-o", type=click.Path(dir_okay=False, path_type=Path), help="Write to a file.")
def ddl_cmd(dialect, profile, dataset, money, fmt, location, database, constraints, copy_from, out) -> None:
    """Print CREATE TABLE statements (Glue: TableInput JSON) for a creforge dataset."""
    if dataset:
        manifest = DiskDataset.open(dataset).manifest
        profile = DiskDataset.open(dataset).config.profile
        money = money or manifest["config"].get("money", "float")
        fmt = fmt or manifest["format"]
    text = ddl(dialect, profile=profile, money=money or "decimal", format=fmt or "parquet",
               location=location, database=database, constraints=constraints)
    if copy_from:
        if dialect != "postgres":
            raise click.UsageError("--copy-script is only for --dialect postgres")
        text += "\n" + copy_script(copy_from, database)
    if out:
        out.write_text(text, encoding="utf-8")
        click.echo(f"Wrote {out}")
    else:
        click.echo(text, nl=False)


@main.group(name="load")
def load_group() -> None:
    """Load a dataset into a database."""


@load_group.command(name="duckdb")
@click.argument("dataset", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--db", type=click.Path(dir_okay=False, path_type=Path), required=True,
              help="DuckDB database file (created if missing).")
@click.option("--constraints", is_flag=True, help="Create tables with keys and CHECK constraints.")
@click.option("--replace", is_flag=True, help="Drop existing creforge tables first.")
def load_duckdb_cmd(dataset, db, constraints, replace) -> None:
    """Create the tables in DuckDB and load DATASET (needs: pip install 'creforge[duckdb]')."""
    from .load import load_duckdb

    counts = load_duckdb(dataset, db, constraints=constraints, replace=replace)
    click.echo(f"Loaded {dataset} into {db}")
    for table, n in counts.items():
        click.echo(f"  {table:<14} {n:>14,}")


@main.group()
def faults() -> None:
    """Inspect built-in fault profiles."""


@faults.command(name="list")
def faults_list() -> None:
    for name in list_fault_profiles():
        click.echo(f"{name:<12} {load_fault_profile(name).description}")


@faults.command(name="show")
@click.argument("name")
def faults_show(name) -> None:
    """Print the fully resolved fault profile (inheritance applied) as YAML."""
    click.echo(yaml.safe_dump(load_fault_profile(name).model_dump(mode="json"), sort_keys=False))


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
