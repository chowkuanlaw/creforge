"""The output schema, described once: every table, column, logical type and key.

Warehouse DDL (``creforge ddl``) is generated from this, and a test checks it against
real generated output, so the two can't drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Profile
from .engine import CLOSE_REASONS, DPD_BUCKETS, STATUSES
from .generator import OUTCOMES, TABLES
from .parties import ROLES

# Logical types; each dialect maps them to its own SQL types.
ID, SHORT, CODE, DATE, INT16, MONEY, RATE, BOOL = (
    "id", "short", "code", "date", "int16", "money", "rate", "bool")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = False
    references: str | None = None  # "table.column" for a foreign key
    codes: str | None = None  # name of the allowed-value set, for CODE columns


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]


SCHEMA: tuple[Table, ...] = (
    Table("subject", (
        Column("subject_id", ID),
        Column("birth_year", INT16),
        Column("region", SHORT),
        Column("income_band", CODE, codes="income_band"),
        Column("risk_grade", CODE, codes="risk_grade"),
        Column("created_month", DATE),
    ), ("subject_id",)),
    Table("inquiry", (
        Column("inquiry_id", ID),
        Column("subject_id", ID, references="subject.subject_id"),
        Column("inquiry_date", DATE),
        Column("lender_id", SHORT),
        Column("product_type", CODE, codes="product_type"),
        Column("requested_amount", MONEY),
        Column("outcome", CODE, codes="outcome"),
    ), ("inquiry_id",)),
    Table("account", (
        Column("account_id", ID),
        Column("subject_id", ID, references="subject.subject_id"),
        Column("inquiry_id", ID, nullable=True, references="inquiry.inquiry_id"),
        Column("lender_id", SHORT),
        Column("product_type", CODE, codes="product_type"),
        Column("open_date", DATE),
        Column("credit_limit", MONEY, nullable=True),
        Column("principal", MONEY, nullable=True),
        Column("tenor_months", INT16, nullable=True),
        Column("interest_rate", RATE),
        Column("secured", BOOL),
        Column("close_date", DATE, nullable=True),
        Column("close_reason", CODE, nullable=True, codes="close_reason"),
    ), ("account_id",)),
    Table("account_month", (
        Column("account_id", ID, references="account.account_id"),
        Column("as_of_month", DATE),
        Column("balance", MONEY),
        Column("amount_due", MONEY),
        Column("amount_paid", MONEY),
        Column("dpd_bucket", CODE, codes="dpd_bucket"),
        Column("months_in_arrears", INT16),
        Column("status", CODE, codes="status"),
    ), ("account_id", "as_of_month")),
    Table("account_party", (
        Column("account_id", ID, references="account.account_id"),
        Column("subject_id", ID, references="subject.subject_id"),
        Column("role", CODE, codes="role"),
        Column("start_date", DATE),
    ), ("account_id", "subject_id")),
)
assert tuple(t.name for t in SCHEMA) == TABLES


def code_values(profile: Profile) -> dict[str, list[str]]:
    """Allowed values for each CODE column set; some come from the profile."""
    return {
        "income_band": list(profile.population.income_bands),
        "risk_grade": list(profile.grades),
        "product_type": list(profile.products),
        "outcome": list(OUTCOMES),
        "close_reason": list(CLOSE_REASONS),
        "dpd_bucket": list(DPD_BUCKETS),
        "status": list(STATUSES),
        "role": list(ROLES),
    }
