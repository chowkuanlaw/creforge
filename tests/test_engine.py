import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from creforge import engine as E
from creforge.config import load_profile

TABLES = E.Tables.from_profile(load_profile("baseline"))


def _random_book(rng, n):
    t = TABLES
    product = rng.integers(0, len(t.products), n).astype(np.int16)
    rev = t.revolving[product]
    amount = rng.uniform(500, 50_000, n)
    tenor = np.where(rev, 0, rng.integers(3, 120, n)).astype(np.int32)
    rate_m = rng.uniform(0, 0.02, n)
    principal = np.where(rev, 0.0, amount)
    state = rng.choice([E.C, E.D1, E.D2, E.D3, E.D4, E.D5, E.RS], n).astype(np.int8)
    mia = np.where(state <= E.D5, state, 0) + np.where(state == E.D5, rng.integers(0, 3, n), 0)
    book = E.Book(
        product=product, grade=rng.integers(0, len(t.grades), n).astype(np.int8),
        open_abs=-rng.integers(0, 60, n).astype(np.int32), limit=np.where(rev, amount, 0.0),
        principal=principal, tenor=tenor, rate_m=rate_m,
        payment=np.round(np.where(rev, 0.0, E.annuity(principal, rate_m, tenor)), 2),
        state=state, mia=mia.astype(np.int32), bal=np.round(amount * rng.uniform(0.2, 1.0, n), 2),
        arrears=np.zeros(n), inst=np.zeros(n), active=np.ones(n, dtype=bool),
        close_month=np.full(n, -1, dtype=np.int32), close_reason=np.full(n, -1, dtype=np.int8),
        roll_mult=np.ones(n), cure_mult=np.ones(n), guaranteed=rng.random(n) < 0.2,
    )
    book.arrears[:] = np.round(np.minimum(book.mia * 0.03 * book.bal, book.bal), 2)
    book.inst[:] = E.next_installment(t, book, np.arange(n))
    return book


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(0, 2**32 - 1), n=st.integers(1, 400),
       macro=st.floats(0.3, 3.0), months=st.integers(1, 18))
def test_step_preserves_structural_invariants(seed, n, macro, months):
    rng = np.random.default_rng(seed)
    book = _random_book(rng, n)
    prev_dpd = np.where(book.state <= E.D5, np.minimum(book.mia, 5), 0)
    for m in range(months):
        idx = np.flatnonzero(book.active)
        if not len(idx):
            break
        rows = E.step(TABLES, book, idx, m, macro, rng)
        before = prev_dpd[idx]
        assert (rows.dpd - before <= 1).all(), "skipped a delinquency bucket"
        assert (rows.amount_paid[rows.dpd > before] == 0).all(), "paid while rolling forward"
        assert (rows.balance >= 0).all() and (rows.amount_paid >= 0).all()
        assert (rows.amount_due >= 0).all()
        assert (book.arrears[idx] <= book.bal[idx] + 1e-9).all()
        terminal = np.isin(book.state[idx], (E.WO, E.CL))
        assert (~book.active[idx][terminal]).all()
        assert (book.close_month[idx][terminal] == m).all()
        dl = rows.status == E.ST_DELINQUENT
        assert (rows.amount_due[dl] > 0).all()
        prev_dpd[idx] = rows.dpd


def test_writeoff_after_configured_months_in_120_plus():
    rng = np.random.default_rng(0)
    book = _random_book(rng, 200)
    book.state[:] = E.D5
    book.mia[:] = 5
    book.guaranteed[:] = False
    # Remove every exit from D5 so accounts must sit there until write-off.
    t = E.Tables(**{**TABLES.__dict__, "cure": TABLES.cure * 0, "back": TABLES.back * 0,
                    "rs": TABLES.rs * 0})
    book.inst[:] = np.maximum(book.inst, 1.0)
    for m in range(t.writeoff_after):
        E.step(t, book, np.flatnonzero(book.active), m, 1.0, rng)
    assert (book.state == E.WO).all()
    assert (book.close_month == t.writeoff_after - 1).all()


def test_called_guarantor_cures_the_account():
    rng = np.random.default_rng(1)
    book = _random_book(rng, 300)
    book.state[:] = E.D5
    book.mia[:] = 5
    book.guaranteed[:] = True
    book.inst[:] = np.maximum(book.inst, 1.0)
    t = E.Tables(**{**TABLES.__dict__, "guarantor_call": 1.0})
    rows = E.step(t, book, np.arange(300), 0, 1.0, rng)
    # Cured; a small loan can be paid off completely by the guarantor's payment.
    assert np.isin(book.state, (E.C, E.CL)).all() and (book.mia == 0).all()
    assert (rows.amount_paid == rows.amount_due).all()


def test_joint_multiplier_is_geometric_mean():
    from creforge.parties import blended_multipliers
    roll, cure = blended_multipliers(TABLES.g_roll, TABLES.g_cure, np.array([0, 4, 2]), np.array([4, -1, 2]))
    assert np.isclose(roll[0], np.sqrt(TABLES.g_roll[0] * TABLES.g_roll[4]))
    assert roll[1] == TABLES.g_roll[4] and roll[2] == TABLES.g_roll[2]
    assert np.isclose(cure[0], np.sqrt(TABLES.g_cure[0] * TABLES.g_cure[4]))


def test_initial_state_table_is_distribution():
    table = E.initial_state_table(TABLES, 120)
    assert table.shape == (len(TABLES.products), len(TABLES.grades), 121, E.N_TRANSIENT)
    np.testing.assert_allclose(table.sum(axis=-1), 1.0, atol=1e-9)
    assert (table[:, :, 0, E.C] == 1.0).all()
    # Worse grades hold more delinquency at every seasoned age.
    dq = table[:, :, 24, E.D1 : E.D5 + 1].sum(axis=-1)
    assert (np.diff(dq, axis=1) > 0).all()


def test_seasoning_curve_shape():
    s = load_profile("baseline").seasoning
    curve = s.curve(np.arange(0, 120))
    assert curve[0] == s.floor
    assert curve.argmax() == s.peak_age
    assert abs(curve[-1] - s.tail) < 0.05
