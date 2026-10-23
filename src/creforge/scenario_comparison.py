"""Paired comparison of a baseline dataset and its scripted scenario run."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .dataset import Dataset, DiskDataset
from .scenarios import ScenarioConfig, apply_scenario
from .validate import validate

_METRICS = ("dpd30_share", "annual_writeoff_rate")
_LABELS = {"dpd30_share": "30+ DPD share", "annual_writeoff_rate": "Annual write-off rate"}


@dataclass(frozen=True)
class ScenarioMetricComparison:
    """One product metric in the baseline and scenario runs."""

    product: str
    metric: str
    baseline: float
    scenario: float
    delta_percentage_points: float
    baseline_account_months: int
    scenario_account_months: int


@dataclass(frozen=True)
class ScenarioComparisonReport:
    """Descriptive product-rate differences for a matched pair of datasets."""

    baseline_config_sha256: str
    scenario_config_sha256: str
    scenario: dict
    subjects: int
    months: int
    seed: int
    start_month: str
    chunk_size: int
    money: str
    comparisons: list[ScenarioMetricComparison]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# Scenario comparison", "",
            f"- Scenario: `{self.scenario['name']}`",
            f"- Subjects: {self.subjects:,}; months: {self.months}; seed: {self.seed}; "
            f"start: {self.start_month}",
            f"- Chunk size: {self.chunk_size:,}; money: {self.money}",
            f"- Baseline config SHA-256: `{self.baseline_config_sha256}`",
            f"- Scenario config SHA-256: `{self.scenario_config_sha256}`",
            "- Events:",
        ]
        for event in self.scenario["events"]:
            detail = (f"; multiplier {event['multiplier']}" if "multiplier" in event
                      else f"; delta {event['delta']:+.2%}" if "delta" in event else "")
            lines.append(f"  - `{event['type']}`: months {event['start_month']}–"
                         f"{event['end_month'] - 1}{detail}")
        lines += [
            "", "| Product | Metric | Baseline | Scenario | Change (pp) | "
            "Active account-months (baseline / scenario) |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for row in self.comparisons:
            lines.append(
                f"| {row.product} | {_LABELS[row.metric]} | {row.baseline:.2%} | "
                f"{row.scenario:.2%} | {row.delta_percentage_points:+.2f} | "
                f"{row.baseline_account_months:,} / {row.scenario_account_months:,} |"
            )
        lines += [
            "", "Changes are scenario minus baseline in percentage points. Rates use each "
            "run's active account-months as the denominator, which may differ between runs. "
            "These are descriptive results from synthetic data, not an estimate of a real "
            "portfolio's response.", "",
        ]
        return "\n".join(lines)


def compare_scenario(
    baseline: Dataset | DiskDataset | str | Path,
    scenario: Dataset | DiskDataset | str | Path,
) -> ScenarioComparisonReport:
    """Compare matched baseline and scripted scenario datasets by product.

    Both runs must use the same generation settings and original profile; the
    second must retain its scenario script in a :class:`ScenarioConfig`.
    """
    base = DiskDataset.open(baseline) if isinstance(baseline, (str, Path)) else baseline
    stressed = DiskDataset.open(scenario) if isinstance(scenario, (str, Path)) else scenario
    base_cfg, stress_cfg = base.config, stressed.config
    if isinstance(base_cfg, ScenarioConfig):
        raise ValueError("baseline dataset must not contain a scenario")
    if not isinstance(stress_cfg, ScenarioConfig):
        raise ValueError("scenario dataset must contain a scripted scenario")
    base_settings = base_cfg.model_dump(mode="json", exclude={"profile"})
    stress_settings = stress_cfg.model_dump(mode="json", exclude={"profile", "scenario"})
    if base_settings != stress_settings:
        raise ValueError("baseline and scenario generation settings must match")
    expected_profile = apply_scenario(base_cfg.profile, stress_cfg.scenario, base_cfg.months)
    if stress_cfg.profile != expected_profile:
        raise ValueError("scenario profile must derive from the baseline profile")
    if isinstance(base, DiskDataset) and isinstance(stressed, DiskDataset):
        if base.manifest.get("creforge_version") != stressed.manifest.get("creforge_version"):
            raise ValueError("baseline and scenario package versions must match")

    base_report, stress_report = validate(base), validate(stressed)
    if not base_report.integrity_ok or not stress_report.integrity_ok:
        raise ValueError("both datasets must pass integrity validation before comparison")
    comparisons = []
    for product in base_cfg.profile.products:
        base_metrics = base_report.metrics["products"][product]
        stress_metrics = stress_report.metrics["products"][product]
        for metric in _METRICS:
            baseline_rate, scenario_rate = base_metrics[metric], stress_metrics[metric]
            comparisons.append(ScenarioMetricComparison(
                product, metric, baseline_rate, scenario_rate,
                (scenario_rate - baseline_rate) * 100.0,
                base_metrics["active_rows"], stress_metrics["active_rows"],
            ))
    return ScenarioComparisonReport(
        base_cfg.sha256(), stress_cfg.sha256(), stress_cfg.scenario.model_dump(mode="json"),
        base_cfg.subjects, base_cfg.months, base_cfg.seed, base_cfg.start_month,
        base_cfg.chunk_size, base_cfg.money, comparisons,
    )
