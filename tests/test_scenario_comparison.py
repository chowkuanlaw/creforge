"""Paired scenario reports keep the generator settings and metrics attributable."""

import json

import pytest
from click.testing import CliRunner

import creforge as cf
from creforge.cli import main


def _script():
    return cf.Scenario.model_validate({
        "name": "recession",
        "events": [{"type": "macro_multiplier", "start_month": 6, "end_month": 18,
                    "multiplier": 2.5}],
    })


def test_scenario_report_matches_paired_runs_and_cli(tmp_path):
    base = cf.generate(cf.Config.from_profile(subjects=700, months=24, seed=7))
    stressed = cf.generate(cf.ScenarioConfig.from_profile(
        scenario=_script(), subjects=700, months=24, seed=7
    ))
    report = cf.compare_scenario(base, stressed)
    assert report.scenario["name"] == "recession"
    assert report.baseline_config_sha256 == base.config.sha256()
    assert report.scenario_config_sha256 == stressed.config.sha256()
    assert len(report.comparisons) == 2 * len(base.config.profile.products)
    card = next(row for row in report.comparisons
                if row.product == "credit_card" and row.metric == "dpd30_share")
    assert card.delta_percentage_points == pytest.approx((card.scenario - card.baseline) * 100)
    assert card.baseline_account_months > 0 and card.scenario_account_months > 0
    assert any(row.delta_percentage_points != 0 for row in report.comparisons)
    assert "scenario minus baseline" in report.to_markdown()
    assert "`macro_multiplier`: months 6–17; multiplier 2.5" in report.to_markdown()

    baseline_path, scenario_path = tmp_path / "baseline", tmp_path / "scenario"
    base.write(baseline_path)
    stressed.write(scenario_path)
    md_path, json_path = tmp_path / "report.md", tmp_path / "report.json"
    result = CliRunner().invoke(main, [
        "scenario", "compare", str(baseline_path), str(scenario_path),
        "--out", str(md_path), "--json-out", str(json_path),
    ])
    assert result.exit_code == 0, result.output
    assert "Annual write-off rate" in md_path.read_text()
    assert json.loads(json_path.read_text()) == report.to_dict()


def test_scenario_report_rejects_unmatched_runs(tmp_path):
    base = cf.generate(cf.Config.from_profile(subjects=200, months=24, seed=7))
    wrong_seed = cf.generate(cf.ScenarioConfig.from_profile(
        scenario=_script(), subjects=200, months=24, seed=8
    ))
    with pytest.raises(ValueError, match="generation settings must match"):
        cf.compare_scenario(base, wrong_seed)
    with pytest.raises(ValueError, match="must contain a scripted scenario"):
        cf.compare_scenario(base, base)

    wrong_profile = cf.generate(cf.ScenarioConfig.from_profile(
        "stressed", scenario=_script(), subjects=200, months=24, seed=7
    ))
    with pytest.raises(ValueError, match="must derive from the baseline profile"):
        cf.compare_scenario(base, wrong_profile)

    base_path, stress_path = tmp_path / "baseline", tmp_path / "wrong_seed"
    base.write(base_path)
    wrong_seed.write(stress_path)
    result = CliRunner().invoke(main, ["scenario", "compare", str(base_path), str(stress_path)])
    assert result.exit_code == 1
    assert "generation settings must match" in result.output
    assert "Traceback" not in result.output
