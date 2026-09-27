# Changelog

All notable changes to creforge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Until 1.0, minor versions may change the
output schema; every such change is listed under **Changed**.

## [Unreleased]

## [0.8.0] - 2026-09-27

### Added
- **Scorecard feature table** ([docs/features.md](docs/features.md)):
  `creforge features DATASET --as-of YYYY-MM -o features.parquet` gives one row per
  subject on file at the observation month. It has about 30 point-in-time attributes:
  - open accounts by product, balances and revolving utilisation;
  - worst DPD now and over 3/6/12/24 months, months since delinquency, accounts ever
    30+/60+/90+;
  - payment ratios, account ages, inquiries, and joint and guarantor links.

  It also has a `bad` target over a performance window (`--performance`, default 12
  months).
- Bad definitions: `90dpd` (default: 90+ DPD or written off), `60dpd` and `writeoff`.
  Exclusions (`already_bad`, `no_open_account`) are flagged, not dropped.
- `--as-of` can be repeated for out-of-time snapshots. `--include-truth` adds the
  hidden risk grade for diagnostics.
- Tests show:
  - no leakage: rewriting all data after the observation month changes only the target;
  - a correct target: recomputed independently;
  - usable signal: a simple logistic regression reaches a holdout Gini above 0.4 (0.71
    at 100,000 borrowers), and bad rates rise with the hidden grade.
- A reference SQL for four features is run against DuckDB in the tests.
- Python API `features` and `feature_columns`.

## [0.7.0] - 2026-09-27

### Added
- **Monthly submissions** ([docs/submissions.md](docs/submissions.md)):
  `creforge submissions DATASET -o INBOX` splits a dataset into the files lenders send
  a bureau. There is one file per lender per reporting month, and each row holds the
  account's fields plus that month's fields. A delivery log (`submissions.parquet`)
  lists every file with its arrival time.
- Six delivery issues, recorded in an answer key (`issues.parquet`):
  - late files;
  - duplicate resends;
  - wrong originals fixed by a later correction;
  - corrections that arrive before the original;
  - skipped months caught up in the next file;
  - files split into parts.
- Built-in issue profiles `none`, `light`, `standard` and `nasty`, with custom profiles
  in YAML. Output is deterministic per seed.
- `creforge reconcile DATASET REBUILT [--inbox INBOX]` checks the `account` and
  `account_month` tables a pipeline rebuilt against the truth. `REBUILT` can be Parquet
  or CSV folders, or a DuckDB file. The report covers missing and extra rows, duplicate
  keys, and wrong values per column, with the delivery issue behind each difference.
  `--max-differences` makes it usable as a CI gate.
- `creforge issues list|show`, and Python API `submissions`, `reconcile`,
  `ReconcileReport`, `IssueProfile`, `load_issue_profile` and `list_issue_profiles`.
- A tested reference ingester in the docs shows the rule that rebuilds the truth
  exactly.

## [0.6.0] - 2026-09-27

### Added
- **Warehouse DDL** ([docs/warehouses.md](docs/warehouses.md)): `creforge ddl --dialect
  athena|glue|duckdb|postgres|redshift|snowflake` prints the table definitions for a
  dataset (Glue: `TableInput` JSON). `--dataset` takes the profile, money type and format
  from a manifest. `--constraints` adds NOT NULL, primary and foreign keys and, on
  Postgres and DuckDB, `CHECK` constraints listing each code column's allowed values.
  `--copy-script` appends psql `\copy` commands that load a CSV dataset into Postgres.
- `creforge load duckdb DATASET --db FILE` creates the tables and loads a dataset
  (optional extra: `pip install "creforge[duckdb]"`). Dirty datasets load too, with
  unreadable values as NULL, unless `--constraints` is given.
- Python API `ddl`, `copy_script` and `load_duckdb`.
- CI runs the Postgres DDL and `\copy` script against a real Postgres 16. DuckDB is
  tested on every platform. Athena, Glue, Redshift and Snowflake output is snapshot-tested
  only.

## [0.5.0] - 2026-09-27

### Added
- **Fault injection** ([docs/fault-injection.md](docs/fault-injection.md)):
  `creforge inject CLEAN -o DIRTY` writes a corrupted copy of a dataset plus an answer key
  (`faults.parquet`) listing every injected fault. There are 16 fault types:
  duplicates, orphan rows, missing months, nulls, unknown codes, negative and ×100
  amounts, future dates, badly formatted codes, DPD jumps, payments while rolling,
  activity after closure, contradictory close reasons, and (CSV only) bad date formats
  and text in numeric columns. Built-in fault profiles `light`, `standard` and `nasty`;
  custom profiles in YAML. Deterministic per seed; the clean dataset is never changed.
- `creforge score ANSWER_KEY FINDINGS` grades a data-quality tool's findings: recall,
  precision and recall by fault type, with `--min-recall` for CI.
- `creforge faults list|show`; Python API `inject`, `score`, `row_key`,
  `load_fault_profile`, `list_fault_profiles`, `ScoreReport`.
- `creforge validate` has four new integrity checks: `unique_account_month_key`,
  `null_in_required_columns`, `invalid_codes` and `dates_outside_window`. It no longer
  crashes on corrupted data: unknown codes and unreadable CSV values are reported.
- Every GitHub release has sample datasets attached (10,000 borrowers × 36 months,
  seed 42, Parquet and CSV), generated and validated with that release's own package.
- `CITATION.cff`, so GitHub shows "Cite this repository".
- PyPI project links: documentation, changelog, issues.

### Changed
- PyPI status is now "Beta".
- Release notes use absolute links, so they work on the release page.

## [0.4.0] - 2026-09-26

### Changed
- **Money columns default to exact `Decimal(18, 2)`** (schema version 3). Use
  `--money float` / `Config(money="float")` for the previous Float64 columns. Measured
  speed is unchanged.
- **Calibration anchored to cited public sources** ([docs/calibration.md](docs/calibration.md)):
  Federal Reserve delinquency and charge-off rates for cards, mortgages and other consumer
  loans (FRED `DRCCLACBS`, `CORCCACBS`, `DRSFRMACBS`, `CORSFRMACBS`, `DROCLACBS`,
  `COROCLACBS`), 2015–2026 for `baseline` and the 2008–2011 peaks for `stressed`. Target
  bands and product behaviour were retuned to match: for example, card 30+ DPD went from
  3.9% to 2.3% (reference 1.5–3.2%) and mortgage 30+ DPD from 1.0% to 2.1% (reference
  1.7–6.2%). The same seed produces different data from 0.3.
- **Write-off timing per product**, following the FFIEC Uniform Retail Credit
  Classification policy: open-end credit (cards, overdrafts) and mortgages are written off
  after 2 months at 120+ DPD (about 180 days past due), and closed-end credit after 1 month
  (about 120–150 days). It was 6 months for every product, about 300 days past due.
  New per-product setting `writeoff_after_months`. Side effect: guaranteed loans' write-off
  advantage is smaller (about 0.6–0.85x comparable loans, from 0.44x), because installment
  loans now reach write-off sooner and a guarantor has less time to be called.

### Added
- `docs/calibration.md`: sources, observed ranges vs creforge output, and caveats.
- Code of conduct reports go through the repository's private reporting form, which is
  also linked from the issue chooser.

### Fixed
- Reading a CSV dataset written with decimal money keeps the Decimal type. Empty CSV
  fields read as null on older Polars.

## [0.3.0] - 2026-09-25

Groundwork for 1.0: a written contract, and the claims in it checked by CI.

### Added
- `--money decimal` (`Config(money="decimal")`) writes money columns as exact
  `Decimal(18, 2)`. The default stays Float64 rounded to 2 dp.
- `schema_version` in `manifest.json` (currently 2).
- [Data dictionary](docs/data-dictionary.md) documenting every table, column, type and
  allowed value, with a test that fails if the output and the document disagree.
- [Stability policy](docs/stability.md): the schema contract, the same-seed guarantee,
  the public Python API and the CLI, and deprecation rules.
- CI job that installs the **oldest** dependency versions `pyproject.toml` allows and
  runs the full suite; CI coverage gate (95%, currently 98%).
- CLI prints a one-line error, not a traceback, for a missing dataset, an unknown profile
  or an invalid configuration.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `RELEASING.md`, Dependabot.

### Fixed
- creforge did not work with the oldest dependency versions it declared (for example
  polars 1.0 failed on the `close_reason` and `close_date` columns and in validation). It
  now passes the full suite on polars 1.0.0, numpy 1.26.0, pyarrow 14, pydantic 2.5,
  PyYAML 6.0.1 and Click 8.1.0.

### Changed
- `config_sha256` values differ from 0.2 for the same settings, because the
  configuration now includes `money`. The data for a given seed is unchanged.
- Minimum PyYAML is now 6.0.1, the oldest version tested.

## [0.2.0] - 2026-09-25

### Added
- **Joint borrowers and guarantors.** A new `account_party` table links each account to
  its people, with role `primary`, `joint` or `guarantor`. Every account has exactly one
  `primary`, matching `account.subject_id`.
- Joint accounts blend both borrowers' risk (geometric mean of their grade multipliers).
  A co-borrower is drawn from the same region, of similar age, and old enough when the
  account opened.
- Guarantors are typically 20–35 years older with better grades, and are more likely for
  risky grades and young borrowers. Once a guaranteed loan is 90+ DPD, the guarantor can be
  called each month and pays off the arrears.
- New `parties:` profile section (rates per product, age gaps, grade weights, call rate),
  with `enabled: false` to switch the feature off.
- `creforge validate`: 6 new integrity checks for `account_party`, and 3 calibration
  checks (joint accounts lower risk, guaranteed accounts lower write-off, guarantors have
  better grades). Datasets written by 0.1 without `account_party` still validate; the
  party checks are skipped.
- Quickstart notebook (`examples/quickstart.ipynb`): vintage curves, roll rates,
  scoring against the latent grade, baseline vs stressed.
- Contributing guide, security policy, issue templates, README badges.

### Changed
- The same seed produces different data than in 0.1, because joint and guaranteed
  accounts behave differently. The four existing tables keep their schema.

## [0.1.0] - 2026-09-25

First public release.

### Added
- Four linked tables: `subject`, `inquiry`, `account`, `account_month`.
- Rule-based Markov delinquency engine: roll / cure / back / restructure / close /
  stay events; DPD derived from months in arrears; balances and payments derived from
  events; write-off after a configurable time at 120+ DPD.
- Accounts that already exist when the window opens start from the engine's own
  age-conditional distribution (no window-start artefact).
- Keyed-bijection identifiers: unique, non-sequential, seed-dependent.
- Chunked Parquet/CSV output with `manifest.json`; byte-identical output per seed
  regardless of worker count.
- `creforge validate`: 16 integrity checks, calibration against profile targets,
  privacy statement.
- Built-in profiles `baseline` and `stressed`; custom profiles via `extends:`.
- CLI: `creforge generate | validate | profiles list | profiles show`.
- CI on Linux, macOS and Windows (Python 3.10–3.13); PyPI trusted publishing.

[Unreleased]: https://github.com/chowkuanlaw/creforge/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/chowkuanlaw/creforge/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/chowkuanlaw/creforge/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/chowkuanlaw/creforge/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/chowkuanlaw/creforge/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/chowkuanlaw/creforge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/chowkuanlaw/creforge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/chowkuanlaw/creforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chowkuanlaw/creforge/releases/tag/v0.1.0
