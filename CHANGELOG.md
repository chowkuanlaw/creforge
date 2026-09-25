# Changelog

All notable changes to creforge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Until 1.0, minor versions may change the
output schema; every such change is listed under **Changed**.

## [Unreleased]

### Added
- Quickstart notebook (`examples/quickstart.ipynb`): vintage curves, roll rates,
  scoring against the latent grade, baseline vs stressed.
- Contributing guide, security policy, issue templates, README badges.

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

[Unreleased]: https://github.com/chowkuanlaw/creforge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/chowkuanlaw/creforge/releases/tag/v0.1.0
