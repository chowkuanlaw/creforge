# Contributing to creforge

Thanks for helping. creforge is small on purpose: explicit rules, few dependencies,
and a validator that checks every guarantee the generator makes.

## The clean-room rule (read this first)

creforge is safe to use because **no real or proprietary data ever goes into it**.
That only holds if contributions follow these rules:

- **Do not contribute** parameters, code sets, schemas, field names or distributions
  taken from any employer's, client's or institution's non-public systems or data.
  That applies even if you remember them or they have been "anonymised".
- **Do** use public sources: regulator statistical releases, published financial
  stability reports, academic papers, public data dictionaries. Cite them in the
  profile's `sources:` list.
- If you're unsure whether something is public, leave it out and ask in an issue.

By opening a pull request you confirm that your contribution meets this rule and that
you have the right to license it under Apache-2.0.

## Development setup

```console
$ git clone https://github.com/chowkuanlaw/creforge && cd creforge
$ python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
$ pip install -e ".[dev]"
$ ruff check .
$ pytest -m "not slow"      # fast suite, a few seconds
$ pytest -m slow            # calibration suite, ~20 seconds
```

CI runs both suites on Linux, macOS and Windows with Python 3.10–3.13, plus a job with
the oldest dependency versions we support (`ci/min-constraints.txt`) and a 95% coverage
gate. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## What makes a good pull request

- **One change per PR**, with a test. Changes to the engine need a property test in
  `tests/test_engine.py` if they touch a structural rule.
- **Integrity is sacred.** If `creforge validate` reports an integrity failure, that
  is a generator bug. Never loosen an integrity check to make a test pass.
- **Calibration changes** (profile numbers or target bands) must say which public
  source motivates them, and must keep `pytest -m slow` green.
- **Output schema changes** (columns, types, enum values) must update
  [docs/data-dictionary.md](docs/data-dictionary.md), bump `SCHEMA_VERSION`, and go in
  `CHANGELOG.md` under **Changed**. See [docs/stability.md](docs/stability.md) for what
  each kind of version may change.
- Match the existing style; `ruff check .` must pass.

## Proposing a new profile

Open an issue with the **New profile** template first. A good profile:

1. Starts with `extends: baseline` and overrides only what differs.
2. Lists its public sources.
3. Sets `targets:` bands and passes `creforge validate --strict` at 40k subjects.

## Reporting bugs

Use the **Bug report** template. Please include the exact command, the seed, and the
`config_sha256` from `manifest.json`: with those, any output can be reproduced
exactly.
