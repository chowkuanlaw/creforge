# Synthetic identities for UI tests

`creforge identities` writes a separate display overlay keyed by `subject_id`. It
adds display and contact values for user interface tests without changing the five
bureau tables or their manifest. These values are deliberately conspicuous: every
name begins with `Synthetic Subject`, every email ends in the reserved, unreachable
`example.invalid` domain, phones use the invalid `000` area code, and addresses say
`SYNTHETIC NO DELIVERY`. No real people, addresses, or name lists are read or used.

```console
$ creforge generate --subjects 1000 --months 12 --seed 42 --out bureau
$ creforge identities bureau -o identities.parquet
$ creforge identities bureau -o identities.csv
```

The Python API is `creforge.identities("bureau")`, returning a Polars DataFrame.
Both file formats have the same five columns:

| Column | Type | Meaning |
|---|---|---|
| `subject_id` | String | Join key to the `subject` table. |
| `full_name` | String | `Synthetic Subject ` followed by the synthetic ID. |
| `email` | String | Unique `subject+<lowercase-id>@example.invalid` placeholder. |
| `phone` | String | Unique `000-NNN-NNNN` placeholders with invalid area code `000`. |
| `address_line_1` | String | `SYNTHETIC NO DELIVERY ` followed by the synthetic ID. |

The overlay is deterministic for a dataset and safe to use in test screens. Its
emails cannot receive messages, and phone numbers and addresses are undeliverable.
Do not use them to test real delivery.
