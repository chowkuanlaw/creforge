# Loading into Apache Iceberg

The optional Iceberg loader writes each creforge table to an Apache Iceberg table
using PyIceberg's SQL catalog. Its local SQLite catalog and file warehouse need no
cloud account or running service. It accepts both Parquet and CSV datasets.

```console
$ pip install "creforge[iceberg]"
$ creforge generate --subjects 1000 --months 12 --seed 42 --out bureau
$ mkdir -p iceberg
$ creforge load iceberg bureau \
    --catalog-uri "sqlite:///$(pwd)/iceberg/catalog.db" \
    --warehouse "file://$(pwd)/iceberg/warehouse"
```

This creates `creforge.subject`, `creforge.inquiry`, `creforge.account`,
`creforge.account_month` and `creforge.account_party`. The Python equivalent is
`creforge.load_iceberg("bureau", catalog_uri, warehouse)`, returning their row counts.
Enum columns are stored as Iceberg strings. Dates, decimals and other numeric types
retain their values and types.

An existing table causes a clear error before any table is changed. Pass `--replace`
(or `replace=True` in Python) to **purge** the existing creforge tables and reload
them. Point a catalog at a separate namespace with `--namespace` when preserving
existing tables matters.

The loader accepts a SQLAlchemy catalog URI and an Iceberg warehouse URI. The example
above is the tested setup; remote catalogs and object stores need their own PyIceberg
credentials and were not tested here.
