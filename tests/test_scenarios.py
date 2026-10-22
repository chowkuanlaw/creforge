import json
from datetime import date

import polars as pl
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

import creforge as cf
from creforge.cli import main


def _scenario():
    return cf.Scenario.model_validate({
        "name": "test_recession",
        "events": [
            {"type": "macro_multiplier", "start_month": 6, "end_month": 18, "multiplier": 2.0},
            {"type": "macro_multiplier", "start_month": 10, "end_month": 14, "multiplier": 1.5},
        ],
    })


def test_script_changes_behavior_and_is_in_resolved_config():
    baseline = cf.load_profile("baseline")
    stressed = cf.apply_scenario(baseline, _scenario(), 24)
    path = stressed.macro.path(24)
    assert path[:6].tolist() == [1.0] * 6
    assert path[6:10].tolist() == [2.0] * 4
    assert path[10:14].tolist() == [3.0] * 4
    assert path[14:18].tolist() == [2.0] * 4
    assert path[18:].tolist() == [1.0] * 6

    plain_cfg = cf.Config(profile=baseline, subjects=400, months=24, seed=7)
    stress_cfg = cf.Config(profile=stressed, subjects=400, months=24, seed=7)
    assert stress_cfg.sha256() != plain_cfg.sha256()
    plain, stress = cf.generate(plain_cfg), cf.generate(stress_cfg)
    assert plain.subject.equals(stress.subject)
    assert not plain.account_month.equals(stress.account_month)
    assert cf.validate(stress).integrity_ok


def test_scenario_validation_and_cli(tmp_path):
    with pytest.raises(ValidationError, match="end_month must be greater"):
        cf.Scenario.model_validate({"name": "bad", "events": [
            {"type": "macro_multiplier", "start_month": 5, "end_month": 5, "multiplier": 2}]})
    with pytest.raises(ValueError, match="after the 12-month window"):
        cf.apply_scenario(cf.load_profile("baseline"), _scenario(), 12)

    script = tmp_path / "scenario.yaml"
    script.write_text("name: test_recession\nevents:\n  - type: macro_multiplier\n"
                      "    start_month: 6\n    end_month: 18\n    multiplier: 2.0\n")
    out = tmp_path / "data"
    result = CliRunner().invoke(main, ["generate", "-n", "300", "-m", "24", "-s", "7",
                                       "--scenario", str(script), "-o", str(out)])
    assert result.exit_code == 0, result.output
    config = json.loads((out / "manifest.json").read_text())["config"]
    assert config["profile"]["name"] == "baseline_test_recession"
    assert config["profile"]["macro"]["points"][6] == [6, 2.0]
    assert config["scenario"]["events"][0]["type"] == "macro_multiplier"


def test_origination_rate_shock_only_changes_new_accounts_and_replays(tmp_path):
    script = cf.Scenario.model_validate({
        "name": "rate_up", "events": [
            {"type": "origination_rate_delta", "start_month": 6, "end_month": 12,
             "delta": 0.03},
            {"type": "origination_rate_delta", "start_month": 9, "end_month": 10,
             "delta": -0.01},
        ],
    })
    baseline = cf.Config.from_profile(subjects=1000, months=24, seed=7)
    shocked = cf.ScenarioConfig.from_profile(scenario=script, subjects=1000, months=24, seed=7)
    plain, stress = cf.generate(baseline), cf.generate(shocked)
    assert baseline.sha256() != shocked.sha256()
    assert plain.subject.equals(stress.subject)
    assert plain.inquiry.equals(stress.inquiry)
    assert plain.account["account_id"].to_list() == stress.account["account_id"].to_list()
    new = plain.account["inquiry_id"].is_not_null()
    assert plain.account.filter(new)["open_date"].to_list() == stress.account.filter(new)[
        "open_date"
    ].to_list()

    changed = 0
    for opened, old, new in zip(plain.account["open_date"],
                                plain.account["interest_rate"],
                                stress.account["interest_rate"], strict=True):
        delta = 0.03 if date(2023, 7, 1) <= opened < date(2024, 1, 1) else 0.0
        if date(2023, 10, 1) <= opened < date(2023, 11, 1):
            delta -= 0.01
        assert round(new - old, 4) == round(delta, 4)
        changed += delta != 0
    assert changed > 0
    assert not plain.account_month.equals(stress.account_month)
    assert cf.validate(stress).integrity_ok

    manifest = cf.write_dataset(shocked, tmp_path / "rate_up")
    reopened = cf.DiskDataset.open(tmp_path / "rate_up")
    assert isinstance(reopened.config, cf.ScenarioConfig)
    assert reopened.config.sha256() == manifest["config_sha256"]
    assert reopened.config.scenario == script
    assert cf.generate(reopened.config).account.equals(stress.account)


def test_rate_scenario_rejects_invalid_events():
    with pytest.raises(ValidationError, match="Input should be a finite number"):
        cf.Scenario.model_validate({"name": "bad", "events": [
            {"type": "origination_rate_delta", "start_month": 1, "end_month": 2,
             "delta": float("inf")}]})
    with pytest.raises(ValidationError, match="scenario origination rates must stay"):
        cf.ScenarioConfig.from_profile(scenario=cf.Scenario.model_validate({
            "name": "bad", "events": [
                {"type": "origination_rate_delta", "start_month": 1, "end_month": 2,
                 "delta": -1.0}]}), subjects=100, months=12)


def test_moratorium_pauses_current_installment_payments():
    script = cf.Scenario.model_validate({
        "name": "holiday", "events": [
            {"type": "payment_moratorium", "start_month": 6, "end_month": 9},
            {"type": "payment_moratorium", "start_month": 7, "end_month": 8},
        ],
    })
    cfg = cf.ScenarioConfig.from_profile(scenario=script, subjects=500, months=24, seed=7)
    data = cf.generate(cfg)
    loans = [name for name, product in cfg.profile.products.items() if product.kind == "installment"]
    rows = data.account_month.join(data.account.select("account_id", "product_type"),
                                   on="account_id").sort("account_id", "as_of_month").with_columns(
        pl.col("status").shift(1).over("account_id").alias("previous_status")
    ).filter(
        pl.col("product_type").is_in(loans)
        & (pl.col("status") == "current")
        & (pl.col("previous_status") == "current")
        & pl.col("as_of_month").is_between(date(2023, 7, 1), date(2023, 9, 1))
    )
    assert rows.height > 0
    assert rows["amount_due"].to_list() == [0] * rows.height
    assert rows["amount_paid"].to_list() == [0] * rows.height
    assert cf.validate(data).integrity_ok
