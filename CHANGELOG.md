# Changelog

All notable changes to creforge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Until 1.0, minor versions may change the
output schema; every such change is listed under **Changed**.

## [Unreleased]

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

[Unreleased]: https://github.com/chowkuanlaw/creforge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/chowkuanlaw/creforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chowkuanlaw/creforge/releases/tag/v0.1.0
