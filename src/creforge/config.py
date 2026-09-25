"""Profile and run configuration (validated with pydantic)."""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Prob = Annotated[float, Field(ge=0.0, le=1.0)]
NonNeg = Annotated[float, Field(ge=0.0)]
Positive = Annotated[float, Field(gt=0.0)]

_SUM_TOL = 1e-6
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_shares(items: dict[str, Any], what: str) -> None:
    total = sum(v.share for v in items.values())
    if abs(total - 1.0) > _SUM_TOL:
        raise ValueError(f"{what} shares must sum to 1.0, got {total:.6f}")


class LogNormal(_Model):
    median: Positive
    sigma: NonNeg


class Behaviour(_Model):
    """Monthly transition parameters for one product (before grade/seasoning/macro).

    ``roll[i]``: bucket i -> i+1 for C, D1..D4. ``cure[i]``: D1..D5 -> current.
    ``back[i]``: D2..D5 -> one bucket lower. ``restructure``: D2+ -> RS.
    """

    roll: list[Prob] = Field(min_length=5, max_length=5)
    cure: list[Prob] = Field(min_length=5, max_length=5)
    back: list[Prob] = Field(min_length=4, max_length=4)
    restructure: Prob = 0.0
    rs_cure: Prob = 0.0
    rs_redefault: Prob = 0.0
    close: Prob = 0.0

    @model_validator(mode="after")
    def _rows_are_substochastic(self) -> Behaviour:
        rows = {
            "C": self.roll[0] + self.close,
            "D1": self.roll[1] + self.cure[0],
            "D2": self.roll[2] + self.cure[1] + self.back[0] + self.restructure,
            "D3": self.roll[3] + self.cure[2] + self.back[1] + self.restructure,
            "D4": self.roll[4] + self.cure[3] + self.back[2] + self.restructure,
            "D5": self.cure[4] + self.back[3] + self.restructure,
            "RS": self.rs_cure + self.rs_redefault,
        }
        bad = {k: round(v, 6) for k, v in rows.items() if v > 1.0 + _SUM_TOL}
        if bad:
            raise ValueError(f"outflow probabilities exceed 1.0 for states {bad}")
        return self


class Product(_Model):
    kind: Literal["installment", "revolving"]
    holding_rate: NonNeg = Field(description="Expected open accounts per subject at window start")
    inquiry_share: NonNeg = Field(description="Share of new inquiries for this product")
    amount: LogNormal = Field(description="Principal (installment) or limit (revolving)")
    round_to: Positive = 100.0
    tenor_months: list[Annotated[int, Field(ge=1)]] | None = None
    max_age_months: Annotated[int, Field(ge=0)] = 240
    interest_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    rate_grade_sensitivity: NonNeg = 1.0
    secured: bool = False
    min_payment_pct: Prob | None = None
    behaviour: Behaviour

    @model_validator(mode="after")
    def _kind_consistency(self) -> Product:
        if self.kind == "installment":
            if not self.tenor_months:
                raise ValueError("installment products need tenor_months")
        else:
            if self.min_payment_pct is None or self.min_payment_pct <= 0:
                raise ValueError("revolving products need min_payment_pct > 0")
            if self.behaviour.restructure > 0:
                raise ValueError("restructure is only supported for installment products")
        return self


class Grade(_Model):
    share: Prob
    roll_mult: Positive
    cure_mult: Positive
    inquiry_rate: NonNeg = Field(description="Expected inquiries per subject per month")
    approval: Annotated[float, Field(gt=0.0, lt=1.0)]
    utilization: Prob
    transactor_share: Prob
    rate_spread: NonNeg


class IncomeBand(_Model):
    share: Prob
    amount_mult: Positive
    holding_mult: NonNeg


class Population(_Model):
    age_years: tuple[int, int] = (21, 70)
    regions: Annotated[int, Field(ge=1, le=99)] = 13
    income_bands: dict[str, IncomeBand]

    @model_validator(mode="after")
    def _valid(self) -> Population:
        lo, hi = self.age_years
        if not 18 <= lo <= hi <= 100:
            raise ValueError("age_years must satisfy 18 <= min <= max <= 100")
        _check_shares(self.income_bands, "income band")
        return self


class Seasoning(_Model):
    """Roll-rate multiplier by months on book: floor at 0, peak at peak_age, tail after."""

    floor: Positive = 0.5
    peak: Positive = 1.35
    peak_age: Annotated[int, Field(ge=1)] = 15
    tail: Positive = 0.85

    def curve(self, age: np.ndarray) -> np.ndarray:
        a = np.maximum(np.asarray(age, dtype=np.float64), 0.0) / self.peak_age
        hump = a * np.exp(1.0 - a)
        base = np.where(a <= 1.0, self.floor, self.tail)
        return base + (self.peak - base) * hump


class Macro(_Model):
    """Piecewise-linear roll multiplier by window month; flat beyond the given points."""

    points: list[tuple[Annotated[int, Field(ge=0)], Positive]] = [(0, 1.0)]

    @field_validator("points")
    @classmethod
    def _sorted(cls, v: list[tuple[int, float]]) -> list[tuple[int, float]]:
        if not v:
            raise ValueError("macro.points must not be empty")
        months = [m for m, _ in v]
        if months != sorted(set(months)):
            raise ValueError("macro.points months must be strictly increasing")
        return v

    def path(self, months: int) -> np.ndarray:
        xs = np.array([m for m, _ in self.points], dtype=np.float64)
        ys = np.array([y for _, y in self.points], dtype=np.float64)
        return np.interp(np.arange(months, dtype=np.float64), xs, ys)

    @property
    def is_flat(self) -> bool:
        return len({y for _, y in self.points}) == 1


class Inquiries(_Model):
    recent_window_months: Annotated[int, Field(ge=1)] = 6
    recent_penalty: NonNeg = Field(0.35, description="Logit penalty per recent inquiry")
    withdrawn_share: Prob = 0.05


class PartyRates(_Model):
    """Share of a product's accounts that carry a joint borrower / a guarantor."""

    joint: Prob = 0.0
    guarantor: Prob = 0.0


class Parties(_Model):
    """Joint borrowers and guarantors (``account_party``)."""

    enabled: bool = True
    products: dict[str, PartyRates] = {}
    joint_max_age_gap: Annotated[int, Field(ge=0, le=30)] = 8
    guarantor_age_gap: tuple[int, int] = (20, 35)
    guarantor_same_region: Prob = Field(0.7, description="Chance the guarantor lives in the same region")
    guarantor_risky_grades: list[str] = ["D", "E"]
    guarantor_risky_mult: Positive = Field(2.0, description="Guarantor rate multiplier, risky grades")
    guarantor_young_age: Annotated[int, Field(ge=18, le=100)] = 25
    guarantor_young_mult: Positive = Field(2.0, description="Guarantor rate multiplier, young borrowers")
    guarantor_grade_weights: dict[str, NonNeg] = Field(
        default_factory=dict, description="Relative odds of each grade being chosen as a guarantor"
    )
    guarantor_call: Prob = Field(0.15, description="Monthly chance a guarantor is called at 90+ DPD")

    @model_validator(mode="after")
    def _valid(self) -> Parties:
        lo, hi = self.guarantor_age_gap
        if not 0 < lo <= hi <= 60:
            raise ValueError("guarantor_age_gap must satisfy 0 < min <= max <= 60")
        return self


class Target(_Model):
    dpd30_share: tuple[Prob, Prob]
    annual_writeoff_rate: tuple[Prob, Prob]


class Profile(_Model):
    name: str
    description: str = ""
    sources: list[str] = []
    population: Population
    grades: dict[str, Grade]
    products: dict[str, Product]
    seasoning: Seasoning = Seasoning()
    macro: Macro = Macro()
    inquiries: Inquiries = Inquiries()
    lenders: Annotated[int, Field(ge=1, le=9999)] = 40
    writeoff_after_months: Annotated[int, Field(ge=1, le=36)] = Field(
        6, description="Months spent in the 120+ bucket before write-off"
    )
    targets: dict[str, Target] = {}
    parties: Parties = Parties(enabled=False)

    @model_validator(mode="after")
    def _valid(self) -> Profile:
        _check_shares(self.grades, "grade")
        if len(self.grades) < 2:
            raise ValueError("at least two grades are required")
        if not self.products:
            raise ValueError("at least one product is required")
        for name in self.products:
            if not _NAME.match(name):
                raise ValueError(f"product name {name!r} must be snake_case")
        inq = sum(p.inquiry_share for p in self.products.values())
        if abs(inq - 1.0) > _SUM_TOL:
            raise ValueError(f"product inquiry_share must sum to 1.0, got {inq:.6f}")
        unknown = set(self.targets) - set(self.products)
        if unknown:
            raise ValueError(f"targets for unknown products: {sorted(unknown)}")
        unknown = set(self.parties.products) - set(self.products)
        if unknown:
            raise ValueError(f"parties configured for unknown products: {sorted(unknown)}")
        party_grades = set(self.parties.guarantor_risky_grades) | set(self.parties.guarantor_grade_weights)
        unknown = party_grades - set(self.grades)
        if unknown:
            raise ValueError(f"parties reference unknown grades: {sorted(unknown)}")
        return self


class Config(_Model):
    profile: Profile
    subjects: Annotated[int, Field(ge=1)]
    months: Annotated[int, Field(ge=12, le=120)] = 36
    seed: Annotated[int, Field(ge=0)] = 0
    start_month: str = "2023-01"
    chunk_size: Annotated[int, Field(ge=1)] = 50_000
    money: Literal["float", "decimal"] = Field(
        "float", description="Money columns as Float64 (rounded to 2 dp) or exact Decimal(18, 2)"
    )

    @field_validator("start_month")
    @classmethod
    def _month(cls, v: str) -> str:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
            raise ValueError("start_month must look like YYYY-MM")
        return v

    @classmethod
    def from_profile(cls, profile: str | Path = "baseline", **kwargs: Any) -> Config:
        return cls(profile=load_profile(profile), **kwargs)

    @property
    def n_chunks(self) -> int:
        return -(-self.subjects // self.chunk_size)

    def chunk_bounds(self, chunk: int) -> tuple[int, int]:
        start = chunk * self.chunk_size
        return start, min(start + self.chunk_size, self.subjects)

    def sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"chunk_size"})
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


# --- profile loading -----------------------------------------------------------------


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read_profile_dict(ref: str | Path, _seen: tuple[str, ...] = ()) -> dict[str, Any]:
    path = Path(ref)
    if path.suffix in {".yaml", ".yml"} and path.exists():
        text, key, here = path.read_text(encoding="utf-8"), str(path.resolve()), path.parent
    else:
        name = str(ref)
        if name not in list_profiles():
            raise FileNotFoundError(f"unknown profile {name!r}; built-ins: {list_profiles()}")
        text = resources.files("creforge.profiles").joinpath(f"{name}.yaml").read_text("utf-8")
        key, here = f"builtin:{name}", None
    if key in _seen:
        raise ValueError(f"circular profile inheritance: {' -> '.join(_seen + (key,))}")
    data = yaml.safe_load(text) or {}
    parent = data.pop("extends", None)
    if parent is None:
        return data
    parent_ref: str | Path = parent
    if here is not None and (here / parent).exists():
        parent_ref = here / parent
    return _deep_merge(_read_profile_dict(parent_ref, _seen + (key,)), data)


def load_profile(ref: str | Path) -> Profile:
    """Load a built-in profile by name, or a YAML file by path (``extends:`` supported)."""
    return Profile.model_validate(_read_profile_dict(ref))


def list_profiles() -> list[str]:
    root = resources.files("creforge.profiles")
    return sorted(p.name[:-5] for p in root.iterdir() if p.name.endswith(".yaml"))
