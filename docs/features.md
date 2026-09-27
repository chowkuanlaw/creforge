# Scorecard feature table

The first thing every credit-scoring project builds is a table with one row per
borrower as of an observation date: bureau attributes known at that date, and whether
the borrower went bad in the months that followed. `creforge features` builds that
table from a generated dataset, **point in time** (no data from after the observation
month leaks into the features) and with a clearly defined target.

Use it to train and test scorecards on realistic data, or as a reference answer when
you build the same attributes in your own warehouse.

```console
$ creforge generate -n 100000 -o clean
$ creforge features clean --as-of 2024-06 -o features.parquet
$ creforge features clean --as-of 2024-03 --as-of 2024-06 --performance 12 -o snapshots.parquet
```

| Option | Meaning |
|---|---|
| `--as-of YYYY-MM` | Observation month; repeat for several snapshots (stacked, with an `as_of_month` column) |
| `--performance N` | Months after the observation month in which the target is measured (default 12) |
| `--bad` | `90dpd` (default), `60dpd` or `writeoff`; see [Target](#target) |
| `--include-truth` | Add the generator's hidden `risk_grade`, for model diagnostics |
| `-o FILE` | `.parquet` or `.csv` |

The observation month plus the performance window must fit inside the dataset; the
command names the valid range if it doesn't. From Python:
`cf.features("clean", "2024-06", performance=12)` returns a Polars DataFrame.

## Rows

One row per subject **on file** at the observation month (`created_month` on or
before it). Nobody is dropped; standard exclusions are flagged in `excluded` so you
can apply your own policy:

| `excluded` | Meaning |
|---|---|
| null | In scope for model development |
| `already_bad` | Meets the bad definition at the observation month, or had an account written off in the 12 months up to it |
| `no_open_account` | No account open at the observation month |

## Features

All computed from data **up to the end of the observation month** (*A*). "Open"
means the account's status at *A* is `current`, `delinquent` or `restructured`.
Accounts are those where the subject is the primary borrower; joint and guarantor
links are counted separately. DPD buckets are ranked `0` = current, `1` = 1–29 …
`5` = 120+.

| Column | Meaning |
|---|---|
| `age_years` | Age in years at *A* (from `birth_year`) |
| `months_on_file` | Months since the subject's `created_month` |
| `n_open_accounts` | Open accounts |
| `n_open_<product>` | Open accounts per product in the profile (`n_open_credit_card`, …) |
| `secured_share` | Share of open accounts that are secured (null with none open) |
| `total_balance` | Sum of balances on open accounts |
| `revolving_utilisation` | Balance ÷ limit over open credit cards and overdrafts (null without a limit) |
| `max_utilisation` | Highest single revolving account utilisation |
| `worst_dpd_now` | Worst DPD rank over open accounts at *A* |
| `worst_dpd_3m`, `_6m`, `_12m`, `_24m` | Worst DPD rank in the last 3, 6, 12, 24 months (months *A*−n+1 … *A*), any account |
| `months_since_delinquency` | Months since the last month with any account 1+ DPD (0 = delinquent at *A*; null = never in the data) |
| `n_ever_30dpd`, `n_ever_60dpd`, `n_ever_90dpd` | Accounts ever 30+, 60+, 90+ DPD |
| `payment_ratio_3m`, `_6m` | Amount paid ÷ amount due over the last 3 and 6 months (null when nothing was due) |
| `oldest_account_months`, `newest_account_months` | Age of the oldest and newest account opened by *A*, open or closed |
| `accounts_opened_12m` | Accounts opened in the last 12 months |
| `inquiries_3m`, `_6m`, `_12m` | Credit inquiries in the last 3, 6, 12 months |
| `n_joint`, `n_guarantor` | Open accounts on which the subject is a joint borrower or guarantor |

Histories only go back to the dataset's `start_month`: for accounts opened earlier,
the `worst_dpd_*` and `n_ever_*` features see the months inside the dataset only, as a
bureau with a limited retention window would.

## Target

`bad` is 1 if **any** of the subject's accounts (including accounts opened after *A*)
meets the bad definition in months *A*+1 … *A*+`performance`; `bad_month` is the first
such month.

| `--bad` | Bad when |
|---|---|
| `90dpd` (default) | 90+ days past due, or written off. This is the usual industry and Basel II/III default definition. |
| `60dpd` | 60+ days past due, or written off |
| `writeoff` | Written off only |

## Guarantees

- **No leakage.** A test rewrites every record after the observation month (balances,
  arrears, statuses, new accounts, new inquiries) and checks that no feature changes;
  only `bad` and `bad_month` do.
- **Target correct.** The tests recompute the target with a plain Python loop over
  `account_month`.
- **The data carries signal.** A plain logistic regression on these features (fitted
  in the tests with a few lines of numpy) reaches a Gini well above zero on a holdout,
  and bad rates rise with the hidden risk grade. That shows the generator's risk
  ordering survives into bureau attributes. It says nothing about any real portfolio.
- **Deterministic**, and built chunk by chunk, since each chunk holds whole subjects.
  Two snapshots for 100,000 borrowers took 12 s with a 1.2 GB peak on a 4-vCPU
  machine. On that data (baseline profile, 90dpd, 12 months), the in-scope bad rate was
  5.6% and the simple logistic regression reached a holdout Gini of 0.71.

## Reference SQL

The same logic in SQL, for four of the features, runs in the test suite against a
DuckDB database made with `creforge load duckdb` and must match the Python output. Use
it as a starting point for your own warehouse code (`$as_of` is the first day of the
observation month):

<!-- reference-sql -->
```sql
WITH subjects AS (
    SELECT subject_id FROM subject WHERE created_month <= CAST($as_of AS DATE)
),
open_now AS (
    SELECT a.subject_id, count(*) AS n_open_accounts, sum(m.balance) AS total_balance
    FROM account_month m JOIN account a USING (account_id)
    WHERE m.as_of_month = CAST($as_of AS DATE)
      AND m.status IN ('current', 'delinquent', 'restructured')
    GROUP BY a.subject_id
),
worst AS (
    SELECT a.subject_id,
           max(CASE m.dpd_bucket WHEN '0' THEN 0 WHEN '1-29' THEN 1 WHEN '30-59' THEN 2
                                 WHEN '60-89' THEN 3 WHEN '90-119' THEN 4 ELSE 5 END) AS worst_dpd_12m
    FROM account_month m JOIN account a USING (account_id)
    WHERE m.as_of_month > CAST($as_of AS DATE) - INTERVAL 12 MONTH
      AND m.as_of_month <= CAST($as_of AS DATE)
    GROUP BY a.subject_id
),
inq AS (
    SELECT subject_id, count(*) AS inquiries_12m
    FROM inquiry
    WHERE inquiry_date >= CAST($as_of AS DATE) - INTERVAL 11 MONTH
      AND inquiry_date < CAST($as_of AS DATE) + INTERVAL 1 MONTH
    GROUP BY subject_id
)
SELECT s.subject_id,
       coalesce(o.n_open_accounts, 0) AS n_open_accounts,
       o.total_balance,
       w.worst_dpd_12m,
       coalesce(i.inquiries_12m, 0) AS inquiries_12m
FROM subjects s
LEFT JOIN open_now o USING (subject_id)
LEFT JOIN worst w USING (subject_id)
LEFT JOIN inq i USING (subject_id)
ORDER BY s.subject_id
```
