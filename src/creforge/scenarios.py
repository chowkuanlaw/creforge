"""Apply time-bounded credit-stress events to a generation run."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import Config, Macro, Profile, load_profile


class _TimedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start_month: int = Field(ge=0)
    end_month: int = Field(gt=0)

    @model_validator(mode="after")
    def _range(self) -> _TimedEvent:
        if self.end_month <= self.start_month:
            raise ValueError("end_month must be greater than start_month")
        return self


class MacroShock(_TimedEvent):
    """Multiply macro stress from ``start_month`` through ``end_month - 1``."""

    type: Literal["macro_multiplier"]
    multiplier: float = Field(gt=0)


class RateShock(_TimedEvent):
    """Add an annual rate fraction to new originations in the event window."""

    type: Literal["origination_rate_delta"]
    delta: float = Field(allow_inf_nan=False)


class Moratorium(_TimedEvent):
    """Suspend scheduled payments for current installment accounts in the window."""

    type: Literal["payment_moratorium"]


class Scenario(BaseModel):
    """A named list of monthly macro, rate, and payment events."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    events: list[Annotated[MacroShock | RateShock | Moratorium, Field(discriminator="type")]] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def _name(self) -> Scenario:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.name):
            raise ValueError("scenario name must be snake_case")
        return self


def _load_scenario(scenario: Scenario | str | Path) -> Scenario:
    if isinstance(scenario, Scenario):
        return scenario
    return Scenario.model_validate(yaml.safe_load(Path(scenario).read_text(encoding="utf-8")))


def apply_scenario(profile: Profile, scenario: Scenario | str | Path, months: int) -> Profile:
    """Resolve a scenario's macro events into the profile's monthly stress path.

    For rate or moratorium events, use :class:`ScenarioConfig` so they remain
    available at generation.
    """
    scenario = _load_scenario(scenario)
    path = profile.macro.path(months)
    for event in scenario.events:
        if event.end_month > months:
            raise ValueError(f"scenario event ends at month {event.end_month}, "
                             f"after the {months}-month window")
        if isinstance(event, MacroShock):
            path[event.start_month:event.end_month] *= event.multiplier
    if not np.isfinite(path).all():
        raise ValueError("scenario multipliers must produce finite roll rates")
    macro = Macro(points=list(enumerate(path.tolist())))
    return profile.model_copy(update={
        "name": f"{profile.name}_{scenario.name}",
        "description": f"{profile.description} Scripted scenario {scenario.name}.",
        "macro": macro,
    })


class ScenarioConfig(Config):
    """Generation config retaining the scenario events in the manifest and run hash."""

    scenario: Scenario

    @model_validator(mode="after")
    def _event_bounds_and_rates(self) -> ScenarioConfig:
        for event in self.scenario.events:
            if event.end_month > self.months:
                raise ValueError(f"scenario event ends at month {event.end_month}, "
                                 f"after the {self.months}-month window")

        rate_events = [event for event in self.scenario.events if isinstance(event, RateShock)]
        if rate_events:
            baseline = np.array([
                np.round(product.interest_rate + grade.rate_spread * product.rate_grade_sensitivity, 4)
                for product in self.profile.products.values() for grade in self.profile.grades.values()
            ])
            delta = np.zeros(self.months)
            for event in rate_events:
                delta[event.start_month:event.end_month] += event.delta
            rates = np.round(baseline[:, None] + delta[None, :], 4)
            if not np.isfinite(rates).all() or (rates < 0).any() or (rates > 1).any():
                raise ValueError("scenario origination rates must stay between 0 and 1")
        return self

    @classmethod
    def from_profile(
        cls, profile: str | Path | Profile = "baseline", *, scenario: Scenario | str | Path,
        **kwargs: Any,
    ) -> ScenarioConfig:
        resolved = profile if isinstance(profile, Profile) else load_profile(profile)
        script = _load_scenario(scenario)
        months = kwargs.get("months", 36)
        return cls(profile=apply_scenario(resolved, script, months), scenario=script, **kwargs)
