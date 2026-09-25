import pytest
import yaml
from pydantic import ValidationError

import creforge as cf
from creforge.config import load_profile


def test_builtin_profiles_load():
    assert {"baseline", "stressed"} <= set(cf.list_profiles())
    stressed = load_profile("stressed")
    assert not stressed.macro.is_flat
    assert stressed.products == load_profile("baseline").products  # inherited


def test_rejects_outflow_above_one():
    data = load_profile("baseline").model_dump()
    data["products"]["credit_card"]["behaviour"]["cure"][0] = 0.9  # D1: 0.25 roll + 0.9 cure
    with pytest.raises(ValidationError, match="exceed 1.0"):
        cf.Profile.model_validate(data)


def test_rejects_bad_shares():
    data = load_profile("baseline").model_dump()
    data["grades"]["A"]["share"] = 0.5
    with pytest.raises(ValidationError, match="grade shares must sum"):
        cf.Profile.model_validate(data)


def test_rejects_restructure_on_revolving():
    data = load_profile("baseline").model_dump()
    data["products"]["credit_card"]["behaviour"]["restructure"] = 0.01
    with pytest.raises(ValidationError, match="restructure"):
        cf.Profile.model_validate(data)


def test_custom_profile_extends_file(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text(yaml.safe_dump(load_profile("baseline").model_dump(mode="json")))
    child = tmp_path / "child.yaml"
    child.write_text("extends: base.yaml\nname: child\nlenders: 7\n")
    prof = load_profile(child)
    assert prof.name == "child" and prof.lenders == 7


def test_config_hash_ignores_chunk_size():
    a = cf.Config.from_profile("baseline", subjects=10, seed=1, chunk_size=5)
    b = cf.Config.from_profile("baseline", subjects=10, seed=1, chunk_size=7)
    c = cf.Config.from_profile("baseline", subjects=10, seed=2)
    assert a.sha256() == b.sha256() != c.sha256()


def test_unknown_profile():
    with pytest.raises(FileNotFoundError):
        load_profile("nope")
