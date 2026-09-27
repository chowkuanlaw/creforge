# Fault injection: testing your data-quality checks

`creforge inject` takes a clean creforge dataset and writes a **corrupted copy plus an
answer key**, listing every fault it injected. `creforge score` compares your
data-quality (DQ) tool's findings with that answer key and reports how much it caught.

```console
$ creforge generate -n 50000 -o clean
$ creforge inject clean --faults standard --seed 7 -o dirty
$ # ... run your pipeline / DQ checks on dirty/, export what they flagged ...
$ creforge score dirty/faults.parquet my_findings.csv --min-recall 0.9
```

The clean dataset is never modified. The same clean dataset, fault profile and seed
always produce byte-identical output.

## Fault catalogue

| Group | Fault | What it does | Tables (column) | Caught by `creforge validate` |
|---|---|---|---|---|
| Duplicates | `duplicate_row` | Exact copy of a row | account_month, account, inquiry | `unique_*` checks |
| | `duplicate_key` | Same key, one value changed | account (lender_id), account_month (balance) | `unique_*` checks |
| Referential | `orphan_row` | Copy of a row pointing at an account that doesn't exist | account_month, account_party (account_id) | `fk_month_account`, `fk_party_account` |
| | `missing_month` | A month removed from the middle of an account's history | account_month | `history_span` |
| Values | `null_required` | A required value set to null | subject, inquiry, account, account_month | `null_in_required_columns` |
| | `unknown_code` | A code outside the allowed values (`"999"`, `"unknown"`) | account_month (dpd_bucket, status), account (product_type), inquiry (outcome) | `invalid_codes` |
| | `negative_amount` | An amount made negative | account_month (balance, amount_paid) | `negative_amounts` |
| | `scale_error` | An amount multiplied by 100 | account_month (balance), account (credit_limit, principal) | no: find it with distribution or outlier checks |
| | `future_date` | A date 30–400 days after the window ends | account (open_date), inquiry (inquiry_date) | `dates_outside_window` |
| | `code_formatting` | A code with wrong case or stray spaces (`"CURRENT"`, `"current "`) | account_month (status), account (product_type), inquiry (outcome) | `invalid_codes` |
| Business rules | `dpd_jump` | Account jumps from 0 to 60–89 DPD in one month | account_month (dpd_bucket) | `dpd_skips_bucket` |
| | `paid_while_rolling` | Payment recorded in a month the account rolled worse | account_month (amount_paid) | `paid_while_rolling` |
| | `activity_after_closure` | A `current` row after a write-off or closure | account_month | `rows_after_terminal` |
| | `close_reason_mismatch` | `close_reason` contradicts the final status | account (close_reason) | `close_reason_mismatch` |
| CSV only | `date_format` | A date written as `DD/MM/YYYY` | account (open_date), inquiry (inquiry_date) | as nulls in `null_in_required_columns` |
| | `type_mismatch` | Text (`"N/A"`) in a numeric column | account_month (balance), account (interest_rate) | as nulls in `null_in_required_columns` |

Parquet enforces column types, so the CSV-only faults are applied only when the dirty
output is CSV (`--format csv`). Otherwise they're listed as skipped in the manifest.
Columns that receive out-of-domain text (`unknown_code`, `code_formatting`) become
plain text columns in a Parquet dirty dataset.

## Fault profiles

| Profile | Rate per fault type | Overlap |
|---|---|---|
| `light` | about 0.1% of eligible rows | one fault per row |
| `standard` | about 1% | one fault per row |
| `nasty` | about 5% | several faults may hit the same row |

"Eligible" rows are those a fault can apply to: `missing_month` only removes months from
the middle of a history, and `paid_while_rolling` only targets months where an account
rolled worse. Every enabled fault type is injected **at least once per chunk** when it
has any eligible row, so a small test dataset still covers the whole catalogue.

A custom profile is a YAML file that can extend a built-in one:

```yaml
extends: standard
name: no-deletions
rates:
  missing_month: 0        # switch a fault type off
  duplicate_row: 0.05     # or change its rate
```

Run `creforge faults list` and `creforge faults show standard` to see the built-ins.

## The answer key: `faults.parquet`

| Column | Description |
|---|---|
| `fault_id` | Sequential id. |
| `fault_type` | One of the types above. |
| `table` | Affected table. |
| `row_key` | The affected row, as it appears in the **dirty** data. For `missing_month` it's the row that was removed. |
| `column` | Affected column; null for whole-row faults (duplicates, orphans, missing rows). |
| `original_value`, `injected_value` | Before and after, as text; null where not applicable. |
| `chunk` | Part file the row is in. |

**`row_key` format:** the table's key columns joined with `|`, with dates as
`YYYY-MM-DD` and nulls as empty text:

| Table | Key columns |
|---|---|
| subject | `subject_id` |
| inquiry | `inquiry_id` |
| account | `account_id` |
| account_month | `account_id`, `as_of_month` (e.g. `A1D11ZEG7VM9W|2024-03-01`) |
| account_party | `account_id`, `subject_id`, `role` |

`creforge.row_key(df, table)` builds these keys from a DataFrame, which helps when
exporting findings from Python.

## Scoring your findings

Export what your checks flagged as CSV or Parquet with at least `table` and `row_key`;
an optional `column` makes matching stricter. Then:

```console
$ creforge score dirty/faults.parquet my_findings.csv
```

- **Recall:** the share of injected faults that at least one finding matched. This is
  what your checks caught.
- **Precision:** the share of findings that matched an injected fault. Findings that
  match nothing are false alarms. (The clean data has no faults, so any real problem
  flagged in it would also count against precision.)
- A breakdown of recall by fault type shows which kinds of problem slip through.

`--min-recall 0.9` exits with an error when recall falls below 90%, so the score can
gate a CI pipeline. `--json` gives machine-readable output.

## Practical notes

- **Memory:** injection works one chunk at a time, using about 3 GB per 50,000-subject
  chunk. Generate the clean dataset with a smaller `--chunk-size` to use less.
- **Speed:** about 20 seconds per 100,000 subjects × 36 months on a 4-vCPU container.
- The dirty `manifest.json` records the fault profile, seed and the clean dataset's
  `config_sha256`, so any dirty dataset can be rebuilt exactly.
