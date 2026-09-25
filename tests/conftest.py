import pytest

import creforge as cf


@pytest.fixture(scope="session")
def small_config() -> cf.Config:
    return cf.Config.from_profile("baseline", subjects=3000, months=24, seed=123, chunk_size=1000)


@pytest.fixture(scope="session")
def small_dataset(small_config) -> cf.Dataset:
    return cf.generate(small_config)
