# creforge

[![CI](https://github.com/chowkuanlaw/creforge/actions/workflows/ci.yml/badge.svg)](https://github.com/chowkuanlaw/creforge/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/creforge)](https://pypi.org/project/creforge/)
[![Python](https://img.shields.io/pypi/pyversions/creforge)](https://pypi.org/project/creforge/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**PII-safe synthetic credit bureau data, generated from explicit behavioural rules.**

creforge generates realistic, linked credit-bureau-shaped datasets: borrowers, credit
inquiries, accounts, the joint borrowers and guarantors on them, and monthly payment
histories. The data has real delinquency dynamics, vintage curves and stress
scenarios, and **no real data ever goes in**. There is no model fitted to real records,
so there is nothing to memorise or leak.

Use it to test credit data pipelines, demo dashboards, teach credit-risk analytics,
benchmark warehouses, or give contractors and vendors a realistic dataset without a
data-sharing agreement.

**Just want the data?** Every [release](https://github.com/chowkuanlaw/creforge/releases/latest)
has a ready-made sample attached (10,000 borrowers × 36 months, Parquet and CSV) that
opens in DuckDB, Power BI, Excel or pandas, with nothing to install.

```console
$ pip install creforge
$ creforge generate --subjects 1000000 --months 36 --seed 42 --out ./bureau --workers 4
$ creforge validate ./bureau --strict
```

## Why not SDV / Faker?

| | Faker-style | Fit-to-real (SDV, GAN, ...) | **creforge** |
|---|---|---|---|
| Needs real data | No | **Yes**: privacy review, leakage risk | No |
| Delinquency dynamics | None | Learned; can break the rules | Explicit Markov model |
| Can a loan jump from current to 90+? | n/a | Yes | **Never** (tested invariant) |
| Referential integrity across tables | Manual | Often approximate | Guaranteed and validated |
| Reproducible | Mostly | Rarely byte-identical | **Byte-identical per seed**, any worker count |
| Explainable parameters | n/a | No | Every probability is in a commented YAML file |

## The data

| Table | One row per | Highlights |
|---|---|---|
| `subject` | borrower | Latent `risk_grade` A–E is exported, so it doubles as ground truth for testing scorecards. No names, IDs or addresses. |
| `inquiry` | credit application | Approval odds fall as a borrower makes more applications ("credit hungry" behaviour). |
| `account` | credit facility | `credit_card`, `personal_loan`, `mortgage`, `auto_loan`, `overdraft`, `bnpl`. New accounts link to their approved inquiry. Older accounts start part-way through their life. |
| `account_month` | account × month | Balance, amount due, amount paid, DPD bucket (`0` … `120+`), months in arrears, status. |
| `account_party` | person on an account | Role `primary`, `joint` or `guarantor`. About 40% of mortgages are joint; personal and auto loans to risky or young borrowers often have a guarantor (typically an older relative with a better grade). |

Output is one Parquet or CSV part file per chunk per table, plus a `manifest.json`
recording the version, schema version, seed, full resolved config and its SHA-256.
Money columns are exact `Decimal(18, 2)`, or Float64 rounded to 2 decimal places with
`--money float`.

Every column, type and allowed value is documented in the
[data dictionary](docs/data-dictionary.md). What stays stable between versions (the
schema, same seed → same data, the Python API and the CLI) is set out in
[docs/stability.md](docs/stability.md).

## How it works

Every month, each open account draws one event: **roll** (miss a payment, move one
bucket worse), **cure** (pay all arrears), **back** (pay one installment of arrears),
**restructure**, **close**, or **stay**. The probabilities are

```
base[product, state] × grade multiplier × seasoning(months on book) × macro(month)
```

The DPD bucket is derived from months in arrears. Balances and payments are derived
from the event. So an account can never skip a bucket, pay while rolling forward, or
show a DPD that disagrees with its arrears. Accounts that already exist when the window
opens start in a state drawn from the model's own age-conditional distribution, so the
first month shows no warm-up artefact.

Joint accounts blend both borrowers' risk. Guaranteed loans that reach 90+ DPD can be
rescued when the guarantor is called and pays off the arrears, so fewer of them are
written off.

## Validation

`creforge validate` reports:

- **Integrity** (hard guarantees; any failure is a bug): unique ids, every foreign key
  resolves, the account opens on or after its approved inquiry, month histories are
  contiguous and end at closure, no rows after write-off, no skipped buckets, no
  payment on a roll-forward, no negative amounts, no nulls in required columns, no
  codes outside the allowed values, no dates outside the window, exactly one primary
  borrower per account, and no person listed twice on the same account.
- **Calibration** (against the profile's `targets`): 30+ DPD share and annual
  write-off rate per product; 12-month bad rate increasing across grades; the seasoning
  peak falls at 6–35 months on book; no artefact at the start of the window; joint
  accounts go bad less often and guaranteed loans are written off less often than
  comparable single-borrower accounts; guarantors have better grades than the borrowers
  they back.
- **Privacy statement**, with the config hash, to attach to data-handling approvals.

Baseline, 50k subjects × 36 months, seed 11 (creforge 0.4), next to the US commercial
bank range for 2015–2026 that each target is set from:

| Product | 30+ DPD share | Reference range | Annual write-off rate | Reference range |
|---|---|---|---|---|
| credit_card | 2.3% | 1.5–3.2% | 3.9% | 1.6–4.7% |
| personal_loan | 2.0% | 1.5–2.4% | 2.3% | 0.3–1.2% (banks only) |
| mortgage | 2.1% | 1.7–6.2% | 0.2% | 0.0–0.3% |
| auto_loan | 1.4% | 1.5–2.4% | 1.6% | 0.3–1.2% (banks only) |
| overdraft | 1.8% | assumption | 3.0% | assumption |
| bnpl | 6.5% | assumption | 1.0% | assumption |

12-month bad rate by grade: A 0.03% · B 0.3% · C 0.9% · D 5.7% · E 16%.

Sources, method and caveats are in [docs/calibration.md](docs/calibration.md). For
example, personal and auto write-offs sit above the bank-only series on purpose, because
that series excludes finance companies.

## Testing data-quality checks: fault injection

Test data that is *wrong in known ways* proves your pipeline's data-quality checks work.
`creforge inject` corrupts a copy of a clean dataset with 16 kinds of realistic fault
(duplicates, orphan rows, missing months, nulls, unknown codes, ×100 amounts, future
dates, DPD jumps, activity after write-off, bad date formats, text in number columns
and more) and writes an answer key listing every one. `creforge score` then grades
what your checks found:

```console
$ creforge inject clean --faults standard -o dirty
$ creforge score dirty/faults.parquet my_findings.csv --min-recall 0.9
- Recall: 94.2% (injected faults your checks found)
- Precision: 99.1% (findings that were real faults)
```

(The recall and precision above are illustrative output.) Details, the full fault
catalogue and the answer-key format are in
[docs/fault-injection.md](docs/fault-injection.md).

## Scorecard feature table

`creforge features` builds the table every credit-scoring project starts with: one row
per borrower at an observation month, about 30 point-in-time bureau attributes
(utilisation, worst arrears over 3/6/12/24 months, inquiries, account age, joint and
guarantor links, …) and a good/bad target over the following months.

```console
$ creforge features clean --as-of 2024-06 --performance 12 -o features.parquet
```

A test proves there is no leakage from after the observation month. The default bad
definition is 90+ days past due or written off, and exclusions are flagged, not
dropped. Details and a tested reference SQL are in [docs/features.md](docs/features.md).

## Testing bureau ingestion: monthly submissions

A bureau receives one file per lender per month, and those files arrive late, twice,
in parts, a month behind, or with wrong values that a later correction fixes.
`creforge submissions` turns a dataset into that stream of files (with an answer key),
and `creforge reconcile` checks whether your ingestion pipeline rebuilt the truth:

```console
$ creforge submissions clean --issues standard -o inbox
$ creforge reconcile clean rebuilt/ --inbox inbox     # PASS, or each difference with its cause
```

The file layout, the six delivery issues, and a short reference ingester are in
[docs/submissions.md](docs/submissions.md).

## Loading into a warehouse

`creforge ddl` writes the table definitions for Athena, Glue, DuckDB, Postgres, Redshift
or Snowflake, with the right types for every column and, optionally, keys and allowed
values. `creforge load duckdb` loads a dataset in one step.

```console
$ creforge ddl --dialect athena --dataset out --location s3://my-bucket/creforge
$ creforge ddl --dialect postgres --dataset out --constraints -o schema.sql
$ pip install "creforge[duckdb]" && creforge load duckdb out --db creforge.duckdb
```

Postgres users also get a psql `\copy` script. Details, and which engines are tested
for real, are in [docs/warehouses.md](docs/warehouses.md).

## Quickstart notebook

[`examples/quickstart.ipynb`](examples/quickstart.ipynb) walks through generating a
portfolio and building vintage curves, a roll-rate matrix, a simple scorecard check
against the known risk grade, and a baseline-vs-stressed comparison.

## Python API

```python
import creforge as cf

cfg = cf.Config.from_profile("stressed", subjects=100_000, months=48, seed=7)
ds = cf.generate(cfg)                  # in memory; ds.account_month is a polars DataFrame
report = cf.validate(ds)
print(report.to_markdown())

cf.write_dataset(cfg, "out/", workers=4)   # streaming, bounded memory, for big runs

fast = cf.Config.from_profile("baseline", subjects=10_000, money="float")  # Float64 money columns
```

## Profiles

`baseline` (flat economy) and `stressed` (roll rates ramp to 1.8× around month 15,
then recover) are built in. `creforge profiles show baseline` prints every parameter.
To make your own, write a YAML file that starts with `extends: baseline` and override
only what you need:

```yaml
extends: baseline
name: subprime_cards
grades:
  E: {share: 0.20}     # fields merge; this assumes you rebalance the other shares
```

All built-in parameters are **illustrative**. They are hand-set to land in the right
order of magnitude compared with published aggregate statistics: Federal Reserve
delinquency and charge-off rates, and the FFIEC write-off timing rules. Every source is
cited in the YAML and in [docs/calibration.md](docs/calibration.md). They do not
describe any real lender's or bureau's portfolio.

## Performance

| Run | Time | Peak memory per worker | Parquet size |
|---|---|---|---|
| 200k subjects × 36 months, 1 worker | 15 s | < 1 GB | 0.16 GB |
| 1M subjects × 36 months, 4 workers | 24 s | 0.9 GB | 0.78 GB |

Measured on a 4-vCPU Linux container. Run `python benchmarks/bench_generate.py` to
measure your own machine.

## Roadmap

1. ~~Guarantor and joint-account links~~: done in 0.2. ~~Fault injection for
   testing data-quality checks~~: done in 0.5. ~~Monthly submissions with late,
   duplicate and corrected files~~: done in 0.7. ~~Scorecard feature table~~: done in 0.8.
2. Contagion: a called guarantee raising the guarantor's own risk; supplementary cards.
3. Business subjects, directors and shareholding graphs.
4. Collateral and legal/litigation records.
5. Clearly marked synthetic PII for UI testing.
6. Calibration to *published aggregate* statistics.
7. Scripted scenarios (moratoria, rate shocks).
8. ~~DuckDB/Postgres loaders and Glue catalog DDL~~: done in 0.6. Iceberg and dbt
   integration.
9. Country flavour packs built only from public specifications.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), including the clean-room rule. Changes are
listed in [CHANGELOG.md](CHANGELOG.md); report vulnerabilities privately as described
in [SECURITY.md](SECURITY.md).

```console
$ pip install -e ".[dev]"
$ ruff check . && pytest -m "not slow"   # fast suite
$ pytest -m slow                         # calibration suite
```

CI runs on Linux, macOS and Windows with Python 3.10–3.13. Design notes are in
[`docs/superpowers/specs`](docs/superpowers/specs).

**Clean-room rule:** contributions must not contain parameters, code sets or schemas
derived from any employer's or institution's non-public systems or data.

## License

Apache-2.0
