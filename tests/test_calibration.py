import pytest

import creforge as cf


@pytest.mark.slow
@pytest.mark.parametrize("profile", ["baseline", "stressed"])
def test_builtin_profiles_meet_their_targets(profile):
    cfg = cf.Config.from_profile(profile, subjects=40_000, months=36, seed=2026)
    report = cf.validate(cf.generate(cfg))
    assert report.integrity_ok
    failed = [c for c in report.checks if c.passed is False]
    assert not failed, failed


@pytest.mark.slow
def test_stress_raises_losses():
    base = cf.validate(cf.generate(cf.Config.from_profile("baseline", subjects=20_000, seed=9)))
    stress = cf.validate(cf.generate(cf.Config.from_profile("stressed", subjects=20_000, seed=9)))
    for p in ("credit_card", "personal_loan"):
        assert (stress.metrics["products"][p]["annual_writeoff_rate"]
                > base.metrics["products"][p]["annual_writeoff_rate"])
