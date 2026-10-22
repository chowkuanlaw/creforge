# Stability and compatibility

creforge follows [Semantic Versioning](https://semver.org/). **Since 1.0, the rules below
are promises:** nothing listed here breaks without a new major version (2.0, 3.0, …), and
every change is recorded in the [CHANGELOG](../CHANGELOG.md).

## The output schema

The tables, columns, types and allowed values documented in the
[data dictionary](data-dictionary.md) are the contract.

| Change | Allowed in |
|---|---|
| Removing or renaming a table or column, changing a type, removing an allowed value | Major versions only (2.0, 3.0, …) |
| Adding a new table, or a new nullable column at the end of a table | Minor versions |
| Fixing a documented guarantee that was violated (a bug) | Patch versions |

Every schema change bumps `schema_version` in `manifest.json`, so a pipeline can check
what it is reading. Enum values marked *(profile)* in the data dictionary belong to the
profile, not the schema: custom profiles may define their own.

## Other output formats

The same rules (nothing removed, renamed or retyped outside a major version; additions
allowed in minor versions) apply to everything else creforge writes for other programs
to read:

| Format | Documented in |
|---|---|
| Fault answer key `faults.parquet`, `row_key` format, and the findings file `score` reads | [fault-injection.md](fault-injection.md) |
| Submission files, delivery log `submissions.parquet`, answer key `issues.parquet`, `inbox.json` | [submissions.md](submissions.md) |
| Feature table: columns, their meaning, bad definitions and `excluded` values | [features.md](features.md) |
| Optional synthetic identity overlay | [identities.md](identities.md) |
| DDL: the SQL type each column gets in each dialect (the statement text itself may be reformatted) | [warehouses.md](warehouses.md) |
| JSON output of `validate --json`, `score --json` and `reconcile --json` (keys may be added, never removed) | each command's docs |

New feature columns, issue types and fault types may be added in minor versions. A new
fault or issue type only takes effect in the built-in profiles of a minor version, never
a patch, since it changes the output for a given seed.

## Reproducibility (same seed, same data)

- **Same creforge version + same configuration + same seed + same platform →
  byte-identical output**, with any number of workers. This is tested in CI on Linux,
  macOS and Windows. Across platforms or dependency versions, output may differ
  slightly, because low-level floating-point maths libraries can differ.
- **Patch versions** (1.2.0 → 1.2.1) keep output byte-identical, unless the patch fixes
  a bug in the data itself; the CHANGELOG then says so explicitly.
- **Minor versions** (1.2 → 1.3) may change the data a seed produces, for example when
  behaviour is refined. They never change the schema.
- `config_sha256` in `manifest.json` identifies the configuration. To reproduce a
  dataset exactly, install the `creforge_version` from its manifest and rerun with the
  same config.

## Public Python API

Supported names, importable from `creforge`:

| Name | Purpose |
|---|---|
| `Config` | Run configuration (`Config.from_profile(...)`) |
| `Profile` | Profile model |
| `load_profile`, `list_profiles` | Profile loading |
| `generate` | Generate a dataset in memory → `Dataset` |
| `write_dataset` | Generate straight to disk, chunk by chunk |
| `Dataset`, `DiskDataset` | In-memory and on-disk datasets |
| `validate`, `Report` | Integrity and calibration report |
| `fetch_public_reference`, `load_public_reference`, `compare_public_reference`, `PublicCalibrationReport` | Save and compare public aggregate reference series |
| `inject`, `load_fault_profile`, `list_fault_profiles` | Fault injection (corrupted copy plus answer key) |
| `score`, `ScoreReport`, `row_key` | Grade data-quality findings against an answer key |
| `ddl`, `copy_script` | Warehouse DDL (Athena, Glue, DuckDB, Postgres, Redshift, Snowflake) and a Postgres `\copy` script |
| `load_duckdb` | Load a dataset into DuckDB (optional extra `creforge[duckdb]`) |
| `load_iceberg` | Load a dataset into an Iceberg SQL catalog (optional extra `creforge[iceberg]`) |
| `submissions`, `IssueProfile`, `load_issue_profile`, `list_issue_profiles` | Monthly lender submission files with delivery issues |
| `features`, `feature_columns` | Point-in-time scorecard feature table with a good/bad target |
| `identities` | Synthetic display names, contacts and addresses for UI tests |
| `Scenario`, `ScenarioConfig`, `apply_scenario` | Apply timed macro, new-origination rate and moratorium events |
| `reconcile`, `ReconcileReport` | Check tables a pipeline rebuilt from submissions against the truth |
| `__version__` | Package version |

Removing or renaming any of these, or one of their documented parameters, needs a
major version; new names and new optional parameters may appear in minor versions.

Everything else (modules such as `creforge.engine`, `creforge.generator` and
`creforge.parties`, and their functions) is internal and may change in any release.
The fields of `Report.metrics` are informational and may gain keys in minor versions.

## Command line

`creforge generate`, `creforge validate`, `creforge inject`, `creforge score`,
`creforge ddl`, `creforge load duckdb`, `creforge load iceberg`, `creforge features`, `creforge identities`,
`creforge submissions`, `creforge reconcile`,
`creforge profiles list|show`, `creforge faults list|show` and
`creforge issues list|show`, with their
documented options, follow the same rules as the Python API. Removing or renaming an
option needs a major version. New options may appear in minor versions. The Markdown
report text may change at any time; use `--json` for machine-readable output.

## Deprecations

Before anything public is removed, it is deprecated for at least one minor version: it
keeps working, emits a `DeprecationWarning`, and is listed under **Deprecated** in the
CHANGELOG.

## Supported environments

- Python: the versions tested in CI (currently 3.11–3.14). Support for a Python version
  may end in a minor release once that version has reached its upstream end of life.
- Dependencies: the minimum versions declared in `pyproject.toml` are tested in CI,
  alongside the latest releases.
- Operating systems: Linux, macOS and Windows.
