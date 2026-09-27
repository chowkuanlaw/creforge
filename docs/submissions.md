# Monthly submissions: testing bureau ingestion

A credit bureau never receives a clean set of tables. Each lender sends **one file per
month**, and those files arrive late, arrive twice, arrive in parts, skip a month, or
carry wrong values that a later correction file fixes. The ingestion pipeline has to
rebuild the true picture from them, and that is where many bureau data bugs start.

`creforge submissions` turns a generated dataset into that stream of monthly files,
with delivery problems injected, and `creforge reconcile` checks whether your pipeline
rebuilt the truth.

```console
$ creforge generate -n 50000 -o clean
$ creforge submissions clean --issues standard --seed 7 -o inbox
$ #  ... your pipeline ingests inbox/ and writes its account and account_month tables ...
$ creforge reconcile clean rebuilt/ --inbox inbox
```

## The inbox

```
inbox/
  submissions.parquet             delivery log: one row per file
  issues.parquet                  answer key: every delivery issue injected
  inbox.json                      profile, seed, counts, source dataset
  L0007/2024-03/S0000123.parquet  lender / reporting month / submission id
  ...
```

### Submission files

Each file is what one lender sends for one month: **one row per account it reports**,
with the account's fields and that month's fields together, as real bureau reporting
formats do.

| Columns | From |
|---|---|
| `account_id`, `as_of_month` | The key: one account in one month |
| `subject_id`, `inquiry_id`, `lender_id`, `product_type`, `open_date`, `credit_limit`, `principal`, `tenor_months`, `interest_rate`, `secured` | The account's fields (see the [data dictionary](data-dictionary.md)) |
| `close_date`, `close_reason` | Filled only in the month the account closes or is written off; empty before |
| `balance`, `amount_due`, `amount_paid`, `dpd_bucket`, `months_in_arrears`, `status` | The month's fields |

Files are Parquet or CSV (`--format`, default: the dataset's format).

### Delivery log (`submissions.parquet`)

| Column | Meaning |
|---|---|
| `submission_id` | `S0000001`, … numbered in arrival order |
| `lender_id` | Sender |
| `reporting_month` | The month the delivery is for |
| `first_month` | Earliest month in the file; earlier than `reporting_month` when the file catches up a skipped month |
| `kind` | `original`, `supplement` (the rest of a partial file), `resend` (a duplicate) or `correction` |
| `received_at` | Arrival timestamp; process files in this order |
| `row_count`, `file` | Rows in the file, and its path relative to the inbox |

## Delivery issues

| Issue | What happens | What the pipeline must do |
|---|---|---|
| `late` | A month's file arrives 50–80 days after month end, after the following month has ended (and normally after that month's file) | Place data by `as_of_month`, not by arrival |
| `duplicate` | The same file is sent again (`kind = resend`) | Deduplicate; don't double-count |
| `correction` | The original has wrong values for some accounts; a later `correction` file restates those accounts with the true values | Replace per account and month |
| `out_of_order_correction` | As above, but the correction arrives **before** the original | A correction beats an original, whatever the arrival order |
| `missing_then_catch_up` | A lender skips a month and sends it inside its next month's file | Accept rows for several months in one file |
| `partial` | A file holds only part of the lender's accounts; a `supplement` file brings the rest | Merge both parts |

Wrong values in an original are the ones lenders typically restate: a payment not yet
posted (`amount_paid`), a balance taken before the month's last transactions
(`balance`), or arrears one bucket worse than true (`dpd_bucket`, `months_in_arrears`
and, for a current account, `status`). The correction always carries the true values.

The answer key `issues.parquet` has one row per delivery issue: `issue_type`,
`lender_id`, `reporting_month`, `submission_id` (the file with the problem) and
`related_submission_id` (the original a resend copies, the correction that fixes a
wrong original, …). For corrections it has one row per wrong value, with `account_id`,
`as_of_month`, `column`, `true_value` and `submitted_value`.

### Issue profiles

| Profile | Share of deliveries (lender × month) per issue type | Wrong rows in a corrected file |
|---|---|---|
| `none` | 0: clean monthly files | – |
| `light` | 1% | 2% |
| `standard` (default) | 3% | 2% |
| `nasty` | 10% | 5% |

`creforge issues list|show` prints them. A custom profile is a YAML file:

```yaml
extends: standard
name: late_lenders
rates:
  late: 0.20
correction_share: 0.05
```

Rates must add up to at most 1; each delivery has at most one issue.

## The rule that rebuilds the truth

Applying every file with this rule rebuilds the dataset's `account` and
`account_month` tables **exactly**:

1. For each account and month, a row from a `correction` file beats any other row.
   Otherwise, the row that arrived last wins (resends are identical, so they're
   harmless once deduplicated).
2. An account's own fields come from its latest month.

The test suite runs this reference ingester (in Polars) on every built-in profile and
checks that `reconcile` passes:

<!-- reference-ingester -->
```python
from pathlib import Path

import polars as pl

MONTH_FIELDS = ["balance", "amount_due", "amount_paid", "dpd_bucket", "months_in_arrears", "status"]


def rebuild(inbox, reader=pl.read_parquet):
    inbox = Path(inbox)
    log = pl.read_parquet(inbox / "submissions.parquet")
    rows = pl.concat([
        reader(inbox / file).with_columns(pl.lit(kind).alias("kind"), pl.lit(received).alias("received_at"))
        for file, kind, received in log.select("file", "kind", "received_at").iter_rows()
    ])
    latest = (rows.sort(pl.col("kind") == "correction", "received_at")
                  .unique(["account_id", "as_of_month"], keep="last"))
    account_month = latest.select("account_id", "as_of_month", *MONTH_FIELDS)
    account = (latest.sort("as_of_month").unique("account_id", keep="last")
                     .drop("as_of_month", "kind", "received_at", *MONTH_FIELDS))
    return {"account": account, "account_month": account_month}
```

A pipeline that appends every file fails on duplicates; one that deduplicates in
arrival order still fails on corrections that arrive before their original. The tests
check both, so each issue type is known to catch a real mistake.

## `creforge reconcile`

```console
$ creforge reconcile DATASET REBUILT [--inbox INBOX] [--max-differences N] [--json]
```

`REBUILT` is either a directory with `account/` and `account_month/` folders of Parquet
or CSV files (any file names), or a DuckDB database file with tables `account` and
`account_month`. Extra columns are ignored; column types are compared loosely (money
to the cent, dates and codes as values), so a CSV or warehouse export works.

The report counts missing rows, extra rows, duplicate keys and wrong values per column,
and shows examples. With `--inbox`, each difference is matched to the delivery issue
recorded for that account and month, so "the original was applied instead of the
correction" is visible directly. The command exits non-zero when there are more than
`--max-differences` (default 0) differences, so it can gate a pipeline's CI.

From Python:

```python
import creforge as cf

cf.submissions("clean", "inbox", issues="nasty", seed=7)
report = cf.reconcile("clean", {"account": my_account_df, "account_month": my_month_df}, inbox="inbox")
assert report.ok, report.to_markdown()
```

## Notes

- **Deterministic:** the same dataset, profile and seed give identical files.
- **Scale:** `submissions` reads the dataset one chunk at a time and holds one
  lender's records at a time. `reconcile` loads both sides of both tables into memory.
  For 100,000 borrowers × 36 months (7.5 million records, about 1,550 files) on a
  4-vCPU machine: `submissions` took 20 s with a 2.2 GB peak, and the reference
  ingester plus `reconcile` took 27 s with a 5 GB peak.
- `subject`, `inquiry` and `account_party` are not part of the submission files.
- Record-level faults can be layered on top: run `creforge inject` first and make
  submissions from the dirty copy, so both kinds of problem reach the pipeline. The
  exact-rebuild guarantee above holds for clean datasets only: faults such as
  duplicate keys or orphan rows have no single true answer.
