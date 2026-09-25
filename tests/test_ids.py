import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from creforge.ids import encode, make_ids, permute


def test_ids_unique_across_chunks_and_formatted():
    ids = np.concatenate([make_ids(42, "account", c, 20_000) for c in range(5)])
    assert len(np.unique(ids)) == len(ids)
    assert all(len(i) == 13 and i[0] == "A" for i in ids[:100])


def test_ids_deterministic_and_seed_dependent():
    assert (make_ids(1, "subject", 0, 50) == make_ids(1, "subject", 0, 50)).all()
    assert not (make_ids(1, "subject", 0, 50) == make_ids(2, "subject", 0, 50)).any()


def test_ids_not_sequential():
    ids = make_ids(7, "subject", 0, 1000)
    assert not (np.sort(ids) == ids).all()


@given(st.lists(st.integers(0, (1 << 60) - 1), min_size=1, max_size=200, unique=True),
       st.integers(0, (1 << 60) - 1))
def test_permute_is_injective(values, key):
    out = permute(np.array(values, dtype=np.uint64), key)
    assert len(set(out.tolist())) == len(values)
    assert (out < (1 << 60)).all()


def test_encode_roundtrip_width():
    assert encode(np.array([0], dtype=np.uint64), "S")[0] == "S" + "0" * 12
    assert encode(np.array([(1 << 60) - 1], dtype=np.uint64), "S")[0] == "S" + "Z" * 12
