# Changelog

All notable changes to creforge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Until 1.0, minor versions may change the
output schema; every such change is listed under **Changed**.

## [Unreleased]

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

[Unreleased]: https://github.com/chowkuanlaw/creforge/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/chowkuanlaw/creforge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/chowkuanlaw/creforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chowkuanlaw/creforge/releases/tag/v0.1.0
