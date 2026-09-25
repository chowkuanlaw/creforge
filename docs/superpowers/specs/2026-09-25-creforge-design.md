# creforge — v1 Design

**Status:** Draft for review
**Date:** 2026-09-25

## 1. Problem

Teams building credit-risk, bureau and lending pipelines need realistic test data,
but they can't use the real thing (PDPA/GDPR, bank secrecy, bureau licensing).
Today's options:

- **Fit-to-real generators** (SDV, Gretel-style): they have to see real data to learn
  from, which brings back the privacy and memorization risk you were trying to avoid.
  They also don't know the domain. Nothing stops them producing a loan that jumps from
  current straight to 90+ DPD, or a payment on a written-off account.
- **Faker-style row generators:** safe but flat. No delinquency dynamics, no vintage
  curves, no referential integrity across tables.

**creforge** fills the gap between the two. It is an open-source generator for
**credit-bureau-shaped data**. Its dynamics come from explicit, documented behavioural
rules, not from real records, so it is **PII-safe by construction**: no real data goes
in, so no real data can come out.

## 2. Decisions locked in brainstorming

| Topic | Decision |
|---|---|
| Audience | Public OSS library, first and foremost |
| Domain shape | Generic credit bureau model (not tied to any national bureau's code sets) |
| Realism engine | Rule-based Markov model over delinquency states |
| Interface | Python library + CLI, writing files (Parquet/CSV) |
| v1 scope | Core 4 tables + deterministic seeding |
| Scale | ~1M borrowers comfortably, vectorized (NumPy + Polars) |
| Table count | Stay at 4 for v1. Guarantors and joint accounts are the first item on the roadmap (§9) |
| History window | 36 months by default, configurable from 12 to 120. Accounts opened before the window start part-way through their life (§4.5) |
| Name | `creforge` (available on PyPI as of 2026-09-25) |

## 3. Data model (v1: 4 tables)

Generation runs in dependency order: `subject → inquiry → account → account_month`.

### 3.1 `subject`: the borrower (individual)
| Column | Type | Notes |
|---|---|---|
| `subject_id` | str | `S` + 12 base32 chars, random from the seed. Never derived from anything real |
| `birth_year` | int16 | Drawn from the configured age distribution |
| `region` | str | Abstract codes `R01..Rnn` (no real geography in v1) |
| `income_band` | cat | `B1..B6` |
| `risk_grade` | cat | Latent `A..E`. Drives transition multipliers. Exported so users can use it as ground truth for model testing |
| `created_month` | date | First month the subject is known to the bureau |

No names, national IDs, addresses or phone numbers in v1. The schema leaves room for
clearly marked synthetic PII in a later version (see §9).

### 3.2 `inquiry`: credit applications / bureau searches
| Column | Type | Notes |
|---|---|---|
| `inquiry_id` | str | |
| `subject_id` | str | FK → subject |
| `inquiry_date` | date | |
| `lender_id` | str | Synthetic lender pool `L0001..`, configurable size |
| `product_type` | cat | See §3.3 |
| `requested_amount` | decimal | |
| `outcome` | cat | `approved` / `declined` / `withdrawn` |

The approval rate depends on risk grade and on how many recent inquiries the subject
has, so "credit hungry" behaviour shows up in the data.

### 3.3 `account`: credit facility
| Column | Type | Notes |
|---|---|---|
| `account_id` | str | |
| `subject_id` | str | FK → subject |
| `inquiry_id` | str | FK → the approved inquiry that opened it (null for accounts that were opened before the window, §4.5) |
| `lender_id` | str | Same lender as the inquiry |
| `product_type` | cat | `credit_card`, `personal_loan`, `mortgage`, `auto_loan`, `overdraft`, `bnpl` |
| `open_date` | date | ≥ inquiry date |
| `credit_limit` / `principal` | decimal | Limit for revolving products, principal for installment products |
| `tenor_months` | int16 | Null for revolving products |
| `interest_rate` | float | By product and risk grade |
| `secured` | bool | |
| `close_date` | date | Null while open |
| `close_reason` | cat | `paid_off`, `written_off`, `closed_by_customer`, `refinanced` |

### 3.4 `account_month`: monthly performance snapshot
One row per open account per month. This is the largest table (~70–100M rows at 1M
subjects × 36 months).

| Column | Type | Notes |
|---|---|---|
| `account_id` | str | FK → account |
| `as_of_month` | date | First of the month |
| `balance` | decimal | |
| `amount_due` | decimal | |
| `amount_paid` | decimal | Consistent with the state transition |
| `dpd_bucket` | cat | `0`, `1-29`, `30-59`, `60-89`, `90-119`, `120+` |
| `months_in_arrears` | int8 | |
| `status` | cat | `current`, `delinquent`, `restructured`, `written_off`, `closed` |

## 4. Realism engine

### 4.1 State machine
States: `C` (current), `D1`..`D5` (the DPD buckets above), `RS` (restructured),
`WO` (written off, absorbing), `CL` (closed/paid off, absorbing).

Structural rules, which hold for every account:
- A delinquent account can **only roll forward by one bucket per month**: `C→D1→D2…`.
  No jumps.
- An account can **cure** to any lower bucket, including `C`, when it pays.
- After `D5` for *k* months (configurable, default 6), the account moves to `WO`.
- `RS` can be entered from `D2+`. From there it follows its own, stickier matrix.
- `WO` and `CL` are absorbing: an account in either state produces no more rows.

### 4.2 Transition probabilities
For each account and month, the effective transition matrix is:

```
P = normalize( Base[product] ∘ M_grade[risk_grade] ∘ S(age_months) ∘ macro(month) )
```

- `Base[product]` is a hand-calibrated baseline matrix per product. It ships in the
  profile YAML with comments explaining where each number comes from (public
  literature, regulator-published aggregates). No proprietary data goes into it.
- `M_grade` scales the roll-forward probabilities up (and cure probabilities down) for
  worse grades.
- `S(age)` is a **seasoning curve**: delinquency peaks around months 12–24 on book and
  is lower before and after.
- `macro(month)` is a per-month stress multiplier, flat for `baseline`. It is the hook
  for stress scenarios.

### 4.3 Financials follow from state
Balances and payments are **derived from the state transition**, never sampled on
their own, so the numbers always agree with the DPD:
- Installment loans follow a standard amortization schedule. A missed payment adds to
  arrears, and a cure pays the arrears (in full or in part).
- Revolving products take a random walk on utilization, bounded by the limit. The
  minimum payment is a percentage of the balance.
- A write-off freezes the balance at the charge-off amount.

### 4.4 Vectorization
Each monthly step is a batched operation over every active account: build an
`(n_active × n_states)` probability array, then do one categorical draw with
`rng.random` and a cumulative-sum search. There are no Python loops per account.

### 4.5 Accounts opened before the window
A real bureau snapshot mixes new accounts with ones that have been on book for years,
especially mortgages. If every account started at month 0, the data would contain no
mature loans and the seasoning curves would be skewed toward young accounts.

- At the start of the window, each subject gets pre-existing accounts with open dates
  back-dated up to 20 years, depending on the product (bounded by its tenor).
- Each of those accounts starts in a state drawn from the model's own distribution for
  its product, grade and age. For installment products, the balance is the amortized
  balance at that age plus any arrears. This avoids simulating the years before the
  window, so it costs almost nothing.
- New accounts then open during the window from approved inquiries.
- `validate` checks that the delinquency mix in the first month matches the steady
  state, so there is no warm-up artefact at the start of the window.

## 5. Scale and determinism

- **Chunking:** subjects are generated in fixed-size chunks (default 100k). Every
  chunk writes its own Parquet part files, so peak memory stays at about one chunk's
  history, around 1–2 GB, whatever the total size.
- **Seeding:** `numpy.random.SeedSequence(seed).spawn(n_chunks)` gives each chunk an
  independent stream. The same seed and config produce **byte-identical output** no
  matter how many worker processes run. That is a tested guarantee.
- **Parallelism:** optional `--workers N`, one process per chunk.
- **Output:** `out/<table>/part-<chunk>.parquet` (or CSV) plus `out/manifest.json`,
  which records the creforge version, seed, config hash, row counts and schema.
- **Target:** 1M subjects × 36 months in under 10 minutes on an 8-core laptop.
  Verified by a benchmark, not assumed.

## 6. Interfaces

### Python
```python
import creforge as cf

cfg = cf.Config.from_profile("baseline", subjects=100_000, months=36, seed=42)
ds = cf.generate(cfg)            # returns Dataset with lazy Polars frames per table
ds.write("out/", format="parquet")
report = cf.validate(ds)         # calibration + integrity report
```

### CLI
```
creforge generate --profile baseline --subjects 1000000 --months 36 \
                  --seed 42 --out ./out --format parquet --workers 8
creforge validate ./out           # integrity checks + calibration report (markdown/HTML)
creforge profiles list | show <name>
```

### Configuration
One YAML profile covers population mix, product mix, transition matrices, seasoning,
macro path and lender pool size. It is validated with pydantic, so bad matrices (for
example, rows that don't sum to 1) fail fast with a clear message. v1 ships the
`baseline` and `stressed` profiles.

## 7. Validation: the credibility feature

`creforge validate` is what separates this tool from a toy. It reports:
- **Integrity:** every FK resolves; `open_date ≥ inquiry_date`; there are no rows
  after `WO`/`CL`; DPD rolls forward at most one bucket per month; balances are never
  negative; `amount_paid ≤ amount_due + arrears`.
- **Calibration:** observed roll rates, 90+ DPD vintage curves, the annualized default
  rate by grade, and the cure rate, each compared to the profile's targets within a
  set tolerance.
- **Privacy statement:** confirms the output came from rules only (with the config
  hash), for users to attach to their data-handling approvals.

## 8. Engineering

- **Stack:** Python ≥ 3.10, `numpy`, `polars`, `pyarrow`, `pydantic`, `pyyaml`,
  `click`. Nothing else is required.
- **Layout:**
  ```
  src/creforge/
    config.py        # pydantic models, profile loading
    profiles/        # baseline.yaml, stressed.yaml
    ids.py           # deterministic ID generation
    subjects.py  inquiries.py  accounts.py
    engine.py        # Markov step, seasoning, macro
    financials.py    # amortization / revolving balance logic
    writer.py        # chunked Parquet/CSV + manifest
    validate.py      # integrity + calibration report
    cli.py
  tests/
  ```
- **Testing:**
  - Property-based tests (Hypothesis) for the structural invariants in §4.1 and §7.
  - A determinism test: generate twice, and across different worker counts, then
    compare file hashes.
  - Statistical tests: roll rates and default rates within tolerance on 50k subjects.
  - A benchmark job (marked, not run on every PR) for the scale target.
- **CI:** GitHub Actions on Linux, macOS and **Windows**, Python 3.10–3.13.
- **License:** Apache-2.0.

## 9. Out of scope for v1 (roadmap)

1. **v1.1:** Guarantor and joint-account links: an `account_party` table with roles
   `primary`, `joint` and `guarantor`. This changes subject→account from one-to-many to
   many-to-many and brings in contingent liabilities (a guarantee turning into a real
   debt). It has high value for bureau use cases and is the first thing to add after v1.
2. Business subjects, directors and shareholding links (a graph of related parties).
3. Collateral and legal/litigation record tables.
4. Clearly marked synthetic PII (names, IDs with an invalid checksum) for UI testing.
5. Calibrating profiles to *published aggregate* statistics (never row-level data).
6. Scripted scenarios, such as a recession starting at month *m* or a moratorium.
7. Loaders for Postgres/DuckDB/Iceberg, plus dbt seed and Glue catalog integration.
8. Country flavour packs with code-set mappings, built only from public specs.

## 10. Risks and open questions

- **Calibration credibility:** the baseline numbers have to be defensible. The plan is
  to cite public sources in the profile YAML and to state clearly that the profiles are
  illustrative, not a model of any real portfolio.
- **Row volume:** `account_month` at 1M subjects is about 90M rows. Parquet with
  dictionary encoding should keep it to around 2–3 GB, to be confirmed by the
  benchmark.
- **IP hygiene:** the author works in the credit bureau industry. All schemas, code
  sets and parameters must be clean-room and generic, with nothing taken from employer
  systems or data.
