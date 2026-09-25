# Data dictionary

This is the contract for creforge output, **schema version 2** (creforge 0.2 and
later). `manifest.json` records the `schema_version` of every dataset. Any change to a
table, column, type or allowed value bumps it and is listed in the
[CHANGELOG](../CHANGELOG.md). See [stability.md](stability.md) for what is guaranteed.

Conventions:

- **Ids** are 13 characters: a table prefix (`S`, `Q`, `A`) plus 12 Crockford-base32
  characters. They are unique within a dataset and change with the seed.
- **Money** columns are `Float64` rounded to 2 decimal places by default, or exact
  `Decimal(18, 2)` with `--money decimal`. Amounts are in an abstract currency unit.
- **Enums** are Polars `Enum` / Parquet dictionary strings. Values marked *(profile)*
  come from the profile, so a custom profile can change them.
- **Dates** are calendar dates. `…_month` columns always hold the first day of the month.
- The simulation **window** is the `--months` months starting at `--start-month`.

## `subject`

One row per borrower (or co-borrower or guarantor). There is no personal data: no
names, national ids, addresses or contact details.

| Column | Type | Description |
|---|---|---|
| `subject_id` | String | Primary key (`S…`). |
| `birth_year` | Int16 | Year of birth. Ages at window start fall within the profile's `age_years`. |
| `region` | String | Abstract region code `R01`, `R02`, … (no real geography). |
| `income_band` | Enum *(profile)* | Income band, `B1` (lowest) to `B6` in the built-in profiles. |
| `risk_grade` | Enum *(profile)* | Latent risk grade, best first: `A` … `E` in the built-in profiles. It drives behaviour and is exported as ground truth. |
| `created_month` | Date | First month the subject appears in the data: the earliest opening of an account they hold as primary borrower, or their earliest inquiry. Subjects with neither get the window's first month. |

## `inquiry`

One row per credit application during the window.

| Column | Type | Description |
|---|---|---|
| `inquiry_id` | String | Primary key (`Q…`). |
| `subject_id` | String | FK → `subject`. The applicant. |
| `inquiry_date` | Date | Date of the application. |
| `lender_id` | String | Abstract lender code `L0001`, `L0002`, … |
| `product_type` | Enum *(profile)* | Product applied for: `credit_card`, `personal_loan`, `mortgage`, `auto_loan`, `overdraft`, `bnpl` in the built-in profiles. |
| `requested_amount` | Money | Amount or limit requested. |
| `outcome` | Enum | `approved`, `declined` or `withdrawn`. Each `approved` inquiry opens exactly one account. |

## `account`

One row per credit facility: opened during the window (from an approved inquiry) or
already open when the window starts.

| Column | Type | Description |
|---|---|---|
| `account_id` | String | Primary key (`A…`). |
| `subject_id` | String | FK → `subject`. The primary borrower. Always equals the `primary` row in `account_party`. |
| `inquiry_id` | String, nullable | FK → `inquiry` (an `approved` one, with the same subject, lender and product). Null for accounts opened before the window. |
| `lender_id` | String | Abstract lender code. |
| `product_type` | Enum *(profile)* | See `inquiry.product_type`. |
| `open_date` | Date | Opening date. On or after `inquiry_date` for linked accounts. |
| `credit_limit` | Money, nullable | Limit for revolving products (`credit_card`, `overdraft`); null otherwise. |
| `principal` | Money, nullable | Amount lent for installment products; null for revolving. |
| `tenor_months` | Int16, nullable | Original term in months for installment products; null for revolving. |
| `interest_rate` | Float64 | Annual rate as a fraction (`0.15` = 15%). |
| `secured` | Boolean | Whether the product is secured (e.g. mortgage, auto loan). |
| `close_date` | Date, nullable | Last day of the month the account closed or was written off; null while open. |
| `close_reason` | Enum, nullable | `paid_off`, `written_off`, `closed_by_customer` or `refinanced`; null while open. |

## `account_month`

One row per account per month, from the later of the account's opening month and the
window's first month, through the month it closes or the window ends. There are no
gaps, and no rows after closure or write-off.

| Column | Type | Description |
|---|---|---|
| `account_id` | String | FK → `account`. |
| `as_of_month` | Date | First day of the reporting month. |
| `balance` | Money | Closing balance after this month's payment, interest and (revolving) new spending. On a `written_off` row, the amount charged off. `0` on a `closed` row. |
| `amount_due` | Money | Installment due this month plus any arrears. `0` in an account's opening month. |
| `amount_paid` | Money | Amount paid this month. Always `0` in a month where the account rolls into a worse DPD bucket. |
| `dpd_bucket` | Enum | Days past due: `0`, `1-29`, `30-59`, `60-89`, `90-119`, `120+`. Moves at most one bucket worse per month. |
| `months_in_arrears` | Int16 | Installments overdue. It keeps counting while at `120+`. |
| `status` | Enum | `current`, `delinquent` (DPD above `0`), `restructured`, `written_off` or `closed`. The last two appear only on an account's final row. |

## `account_party`

One row per person on an account (added in schema version 2).

| Column | Type | Description |
|---|---|---|
| `account_id` | String | FK → `account`. |
| `subject_id` | String | FK → `subject`. |
| `role` | Enum | `primary` (exactly one per account), `joint` (at most one) or `guarantor` (at most one). The same person never appears twice on one account. |
| `start_date` | Date | Date the person joined the account; equals `account.open_date` in schema version 2. |

## `manifest.json`

| Key | Description |
|---|---|
| `creforge_version` | Version that wrote the dataset. |
| `schema_version` | Schema version of the tables (this document describes version 2). |
| `format` | `parquet` or `csv`. |
| `chunks` | Number of part files per table (`<table>/part-00000.<format>`, …). |
| `config_sha256` | Hash of the full configuration. Same hash and version means same data. |
| `row_counts` | Rows per table. |
| `privacy` | Statement that no real data was used. |
| `config` | The full resolved configuration, including the profile. |
