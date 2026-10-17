"""Names renamed at 1.0 keep working until 2.0, with a DeprecationWarning."""

import pytest

import creforge as cf


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("dep") / "clean"
    cf.write_dataset(cf.Config.from_profile("baseline", subjects=200, months=12, seed=1), out)
    return out


def test_validate_data_keyword(dataset):
    with pytest.warns(DeprecationWarning, match="validate\\(data=...\\) is deprecated; use dataset"):
        rep = cf.validate(data=dataset)
    assert rep.integrity_ok
    with pytest.raises(TypeError, match="both"):
        cf.validate(data=dataset, dataset=dataset)


def test_inject_clean_keyword(dataset, tmp_path):
    with pytest.warns(DeprecationWarning, match="inject\\(clean=...\\)"):
        cf.inject(clean=dataset, out=tmp_path / "d", faults="light")
    assert (tmp_path / "d" / "faults.parquet").exists()


def test_feature_columns_products():
    new = cf.feature_columns("baseline")
    assert new == cf.feature_columns() == cf.feature_columns(cf.load_profile("baseline"))
    products = list(cf.load_profile("baseline").products)
    with pytest.warns(DeprecationWarning, match="feature_columns\\(products\\)"):
        assert cf.feature_columns(products) == new
    with pytest.warns(DeprecationWarning):
        assert cf.feature_columns(products=products) == new
    with pytest.raises(TypeError, match="unexpected"):
        cf.feature_columns(nope=1)
