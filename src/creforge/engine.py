"""Vectorized monthly Markov engine for account performance.

Each month every active account draws one *event*:

========  =============================================  ===========================
event     meaning                                        destination
========  =============================================  ===========================
roll      misses the installment                         one bucket worse (C->D1, RS->D1)
cure      pays all arrears                               C
back      pays current + one installment of arrears      one bucket better
restruct  arrears capitalised, schedule extended         RS (installment only)
close     voluntary payoff / closure                     CL
stay      pays current installment only                  same bucket (D5: counts as a miss)
========  =============================================  ===========================

Delinquency buckets are derived from months-in-arrears (``state = min(mia, 5)``), so
an account can never skip a bucket, and DPD and arrears can never disagree.
Balances and payments are derived from the event, never sampled independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Profile

STATES = ("C", "D1", "D2", "D3", "D4", "D5", "RS", "WO", "CL")
C, D1, D2, D3, D4, D5, RS, WO, CL = range(9)
N_TRANSIENT = 7  # C, D1..D5, RS
EV_ROLL, EV_CURE, EV_BACK, EV_RS, EV_CLOSE, EV_STAY = range(6)

DPD_BUCKETS = ("0", "1-29", "30-59", "60-89", "90-119", "120+")
STATUSES = ("current", "delinquent", "restructured", "written_off", "closed")
ST_CURRENT, ST_DELINQUENT, ST_RESTRUCTURED, ST_WRITTEN_OFF, ST_CLOSED = range(5)
CLOSE_REASONS = ("paid_off", "written_off", "closed_by_customer", "refinanced")
CR_NONE, CR_PAID_OFF, CR_WRITTEN_OFF, CR_CUSTOMER, CR_REFINANCED = -1, 0, 1, 2, 3

MAX_OUTFLOW = 0.98  # never let modifiers push a state's total outflow to certainty
REFINANCE_SHARE = 0.3  # share of voluntary installment closures reported as refinanced
_EPS = 0.05  # residual balance treated as paid off (rounding dust)


@dataclass(frozen=True)
class Tables:
    """Profile parameters laid out as arrays indexed by product / grade / state."""

    products: tuple[str, ...]
    grades: tuple[str, ...]
    revolving: np.ndarray  # (P,) bool
    roll: np.ndarray  # (P, 9)
    cure: np.ndarray
    back: np.ndarray
    rs: np.ndarray
    close: np.ndarray
    min_pct: np.ndarray  # (P,)
    g_roll: np.ndarray  # (G,)
    g_cure: np.ndarray
    g_util: np.ndarray
    g_transactor: np.ndarray
    writeoff_after: int
    seasoning: object = field(repr=False)
    guarantor_call: float = 0.0

    @classmethod
    def from_profile(cls, prof: Profile) -> Tables:
        names = tuple(prof.products)
        n = len(names)
        roll, cure, back, rs, close = (np.zeros((n, 9)) for _ in range(5))
        for i, name in enumerate(names):
            b = prof.products[name].behaviour
            roll[i, C : D5] = b.roll  # C..D4
            roll[i, RS] = b.rs_redefault
            cure[i, D1 : D5 + 1] = b.cure
            cure[i, RS] = b.rs_cure
            back[i, D2 : D5 + 1] = b.back
            rs[i, D2 : D5 + 1] = b.restructure
            close[i, C] = b.close
        grades = tuple(prof.grades)
        g = [prof.grades[k] for k in grades]
        return cls(
            products=names,
            grades=grades,
            revolving=np.array([prof.products[p].kind == "revolving" for p in names]),
            roll=roll,
            cure=cure,
            back=back,
            rs=rs,
            close=close,
            min_pct=np.array([prof.products[p].min_payment_pct or 0.0 for p in names]),
            g_roll=np.array([x.roll_mult for x in g]),
            g_cure=np.array([x.cure_mult for x in g]),
            g_util=np.array([x.utilization for x in g]),
            g_transactor=np.array([x.transactor_share for x in g]),
            writeoff_after=prof.writeoff_after_months,
            seasoning=prof.seasoning,
            guarantor_call=prof.parties.guarantor_call if prof.parties.enabled else 0.0,
        )


def event_probs(
    t: Tables,
    prod: np.ndarray,
    roll_mult: np.ndarray,
    cure_mult: np.ndarray,
    state: np.ndarray,
    age: np.ndarray,
    macro: float | np.ndarray,
    can_roll: np.ndarray | bool = True,
) -> np.ndarray:
    """Probabilities of (roll, cure, back, restructure, close); stay is the remainder.

    ``roll_mult`` / ``cure_mult`` are per-account risk multipliers: the grade's values for
    a single borrower, or a blend of both borrowers' values for a joint account.
    """
    macro = np.asarray(macro, dtype=np.float64)
    roll = t.roll[prod, state] * roll_mult * t.seasoning.curve(age) * macro
    roll = np.where(can_roll, roll, 0.0)
    good = cure_mult / macro
    p = np.stack(
        [roll, t.cure[prod, state] * good, t.back[prod, state] * good, t.rs[prod, state],
         t.close[prod, state]],
        axis=1,
    )
    total = p.sum(axis=1)
    scale = np.where(total > MAX_OUTFLOW, MAX_OUTFLOW / np.maximum(total, 1e-12), 1.0)
    return p * scale[:, None]


# --- amortization helpers ------------------------------------------------------------


def annuity(principal: np.ndarray, rate_m: np.ndarray, n: np.ndarray) -> np.ndarray:
    n = np.maximum(np.asarray(n, dtype=np.float64), 1.0)
    r = np.asarray(rate_m, dtype=np.float64)
    safe = np.where(r > 0, r, 1.0)
    with np.errstate(over="ignore"):
        pay = principal * safe / (1.0 - (1.0 + safe) ** -n)
    return np.where(r > 0, pay, principal / n)


def scheduled_balance(principal, rate_m, payment, k) -> np.ndarray:
    k = np.asarray(k, dtype=np.float64)
    r = np.asarray(rate_m, dtype=np.float64)
    safe = np.where(r > 0, r, 1.0)
    growth = (1.0 + safe) ** k
    bal = np.where(r > 0, principal * growth - payment * (growth - 1.0) / safe, principal - payment * k)
    return np.maximum(bal, 0.0)


# --- account book --------------------------------------------------------------------


@dataclass
class Book:
    """Mutable per-account simulation state for one chunk."""

    product: np.ndarray  # int16
    grade: np.ndarray  # int8, primary borrower's grade
    open_abs: np.ndarray  # int32 window month of opening (negative: before window)
    limit: np.ndarray  # float64 (revolving) else 0
    principal: np.ndarray  # float64 (installment) else 0
    tenor: np.ndarray  # int32 (installment) else 0
    rate_m: np.ndarray  # monthly interest rate
    payment: np.ndarray  # scheduled installment (installment products)
    state: np.ndarray  # int8
    mia: np.ndarray  # int32
    bal: np.ndarray
    arrears: np.ndarray
    inst: np.ndarray  # amount of the installment due next month (excl. arrears)
    active: np.ndarray  # bool
    close_month: np.ndarray  # int32, -1 while open
    close_reason: np.ndarray  # int8, -1 while open
    roll_mult: np.ndarray  # float64 risk multipliers (grade, or joint blend)
    cure_mult: np.ndarray
    guaranteed: np.ndarray  # bool: a guarantor can be called at 90+ DPD

    @property
    def size(self) -> int:
        return len(self.product)


def next_installment(t: Tables, book: Book, idx: np.ndarray) -> np.ndarray:
    """Installment due next month (excluding arrears) given the current balance."""
    rev = t.revolving[book.product[idx]]
    bal, arr = book.bal[idx], book.arrears[idx]
    owed = np.maximum(bal - arr, 0.0)
    rev_min = np.maximum(t.min_pct[book.product[idx]] * owed, np.minimum(owed, 0.01 * book.limit[idx]))
    inst_payoff = np.maximum(bal * (1.0 + book.rate_m[idx]) - arr, 0.0)
    inst = np.where(rev, rev_min, np.minimum(book.payment[idx], inst_payoff))
    return np.round(inst, 2)


def target_spend(t: Tables, book: Book, idx: np.ndarray, carried: np.ndarray, rng) -> np.ndarray:
    """New revolving purchases: move utilisation toward a noisy grade-level target."""
    noise = np.exp(rng.normal(0.0, 0.35, len(idx)))
    target = np.clip(t.g_util[book.grade[idx]] * noise, 0.0, 1.0) * book.limit[idx]
    active_month = rng.random(len(idx)) < 0.85
    return np.where(active_month, np.maximum(target - carried, 0.0), 0.0)


@dataclass
class Rows:
    account: np.ndarray
    month: np.ndarray
    balance: np.ndarray
    amount_due: np.ndarray
    amount_paid: np.ndarray
    dpd: np.ndarray
    mia: np.ndarray
    status: np.ndarray


def _dpd_status(state: np.ndarray, mia: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dpd = np.where(state <= D5, np.minimum(mia, 5), 0)
    dpd = np.where(state == WO, 5, dpd)
    status = np.select(
        [state == C, state <= D5, state == RS, state == WO],
        [ST_CURRENT, ST_DELINQUENT, ST_RESTRUCTURED, ST_WRITTEN_OFF],
        ST_CLOSED,
    )
    return dpd.astype(np.int8), status.astype(np.int8)


def step(t: Tables, book: Book, idx: np.ndarray, month: int, macro: float, rng) -> Rows:
    """Advance accounts ``idx`` by one month, mutating ``book``; return their rows."""
    n = len(idx)
    prod, grade, s = book.product[idx], book.grade[idx], book.state[idx].astype(np.int64)
    rev = t.revolving[prod]
    age = month - book.open_abs[idx]
    inst, arr, bal, rate = book.inst[idx], book.arrears[idx], book.bal[idx], book.rate_m[idx]
    mia = book.mia[idx]

    # Fixed draw order keeps runs reproducible.
    u_event = rng.random(n)
    u_reason = rng.random(n)
    u_transactor = rng.random(n)
    u_payfrac = rng.random(n)

    u_call = rng.random(n)

    probs = event_probs(t, prod, book.roll_mult[idx], book.cure_mult[idx], s, age, macro,
                        can_roll=inst > 0)
    ev = (u_event[:, None] >= probs.cumsum(axis=1)).sum(axis=1)
    # A guarantor called at 90+ DPD pays off the arrears: the account cures.
    called = book.guaranteed[idx] & (s >= D4) & (s <= D5) & (u_call < t.guarantor_call)
    ev[called] = EV_CURE

    is_roll, is_cure, is_back = ev == EV_ROLL, ev == EV_CURE, ev == EV_BACK
    is_rs, is_close, is_stay = ev == EV_RS, ev == EV_CLOSE, ev == EV_STAY
    missed = is_roll | (is_stay & (s == D5))

    new_mia = mia.copy()
    new_mia[missed] = mia[missed] + 1
    new_mia[is_cure | is_rs | is_close] = 0
    new_mia[is_back] = np.minimum(mia[is_back], 5) - 1

    new_state = np.minimum(new_mia, 5).astype(np.int64)
    new_state[is_rs | (is_stay & (s == RS))] = RS
    new_state[is_close] = CL

    # Payments implied by the event.
    due = inst + arr
    paid = np.zeros(n)
    new_arr = arr.copy()
    pay_current = is_stay & ~missed
    paid[pay_current] = inst[pay_current]
    rev_current = pay_current & rev & (s == C)
    transactor = u_transactor < t.g_transactor[grade]
    extra = np.where(transactor, bal - inst, u_payfrac * 0.6 * np.maximum(bal - inst, 0.0))
    paid[rev_current] = np.maximum(inst + extra, inst)[rev_current]
    paid[is_cure] = due[is_cure]
    new_arr[is_cure] = 0.0
    frac = np.where(mia > 0, new_mia / np.maximum(np.minimum(mia, 5), 1), 0.0)
    frac = np.where(mia > 5, new_mia / np.maximum(mia, 1), frac)
    back_arr = arr * frac
    paid[is_back] = (inst + arr - back_arr)[is_back]
    new_arr[is_back] = back_arr[is_back]
    new_arr[missed] = arr[missed] + inst[missed]
    new_arr[is_rs] = 0.0

    # Balance update.
    inst_bal = bal * (1.0 + rate)
    carried = np.maximum(bal - np.minimum(paid, bal), 0.0)
    rev_bal = carried * (1.0 + rate)
    paid = np.where(rev, np.minimum(paid, bal), np.minimum(paid, inst_bal))
    paid[is_close] = np.where(rev, bal, inst_bal)[is_close]
    new_bal = np.where(rev, rev_bal, inst_bal - paid)
    spend_ok = rev & (new_state == C)
    spend = target_spend(t, book, idx, rev_bal, rng)
    new_bal = np.where(spend_ok, new_bal + spend, new_bal)
    new_bal[is_close] = 0.0
    new_bal = np.round(np.maximum(new_bal, 0.0), 2)
    paid = np.round(paid, 2)
    new_arr = np.round(np.minimum(new_arr, new_bal), 2)

    # Forced transitions: write-off after long 120+; installment paid off.
    wo = (new_state == D5) & (new_mia >= 5 + t.writeoff_after)
    new_state[wo] = WO
    paid_off = ~rev & np.isin(new_state, (C, RS)) & (new_bal <= _EPS) & ~is_close
    new_state[paid_off] = CL
    new_bal[paid_off] = 0.0
    new_arr[paid_off] = 0.0

    # Commit.
    book.state[idx] = new_state
    book.mia[idx] = new_mia
    book.bal[idx] = new_bal
    book.arrears[idx] = new_arr
    rs_idx = idx[is_rs]
    if len(rs_idx):
        remaining = np.maximum(book.tenor[rs_idx] - (month - book.open_abs[rs_idx]), 12) + 12
        book.payment[rs_idx] = annuity(book.bal[rs_idx], book.rate_m[rs_idx], remaining)
    book.inst[idx] = next_installment(t, book, idx)

    ended = (new_state == WO) | (new_state == CL)
    end_idx = idx[ended]
    book.active[end_idx] = False
    book.close_month[end_idx] = month
    reason = np.full(n, CR_NONE, dtype=np.int8)
    reason[new_state == WO] = CR_WRITTEN_OFF
    reason[paid_off] = CR_PAID_OFF
    vol = is_close
    reason[vol & rev] = CR_CUSTOMER
    reason[vol & ~rev] = np.where(u_reason < REFINANCE_SHARE, CR_REFINANCED, CR_PAID_OFF)[vol & ~rev]
    book.close_reason[end_idx] = reason[ended]

    dpd, status = _dpd_status(new_state, new_mia)
    return Rows(
        account=idx,
        month=np.full(n, month, dtype=np.int16),
        balance=new_bal,
        amount_due=np.round(due, 2),
        amount_paid=paid,
        dpd=dpd,
        mia=new_mia.astype(np.int16),
        status=status,
    )


def open_rows(t: Tables, book: Book, idx: np.ndarray, month: int, rng) -> Rows:
    """First reported month of newly opened accounts: current, nothing due yet."""
    rev = t.revolving[book.product[idx]]
    spend = target_spend(t, book, idx, np.zeros(len(idx)), rng)
    book.bal[idx] = np.round(np.where(rev, spend, book.principal[idx]), 2)
    book.inst[idx] = next_installment(t, book, idx)
    n = len(idx)
    zeros = np.zeros(n)
    return Rows(
        account=idx,
        month=np.full(n, month, dtype=np.int16),
        balance=book.bal[idx].copy(),
        amount_due=zeros,
        amount_paid=zeros.copy(),
        dpd=np.zeros(n, dtype=np.int8),
        mia=np.zeros(n, dtype=np.int16),
        status=np.full(n, ST_CURRENT, dtype=np.int8),
    )


# --- steady state for accounts opened before the window ------------------------------


def initial_state_table(t: Tables, max_age: int) -> np.ndarray:
    """P(state | product, grade, months on book), conditioned on the account being open.

    Evolves each (product, grade) cohort from "current at opening" through the engine's
    own mean-field transition matrix (flat macro), so accounts that start before the
    window are drawn from the same dynamics that then simulate them.
    Returns an array of shape ``(P, G, max_age + 1, 7)``.
    """
    n_p, n_g = len(t.products), len(t.grades)
    pp, gg, ss = np.meshgrid(np.arange(n_p), np.arange(n_g), np.arange(N_TRANSIENT), indexing="ij")
    pp, gg, ss = pp.ravel(), gg.ravel(), ss.ravel()
    dest_roll = np.where(ss == RS, D1, np.minimum(ss + 1, D5))
    dest_back = np.maximum(ss - 1, C)
    leak = 1.0 / t.writeoff_after

    out = np.zeros((n_p, n_g, max_age + 1, N_TRANSIENT))
    v = np.zeros((n_p, n_g, N_TRANSIENT))
    v[:, :, C] = 1.0
    rows = np.arange(len(ss))
    for a in range(max_age + 1):
        out[:, :, a] = v / np.maximum(v.sum(axis=2, keepdims=True), 1e-300)
        p = event_probs(t, pp, t.g_roll[gg], t.g_cure[gg], ss, np.full(len(ss), a), 1.0)
        stay = 1.0 - p.sum(axis=1)
        q = np.zeros((len(ss), N_TRANSIENT))
        np.add.at(q, (rows, dest_roll), p[:, EV_ROLL])
        np.add.at(q, (rows, np.full(len(ss), C)), p[:, EV_CURE])
        np.add.at(q, (rows, dest_back), p[:, EV_BACK])
        np.add.at(q, (rows, np.full(len(ss), RS)), p[:, EV_RS])
        np.add.at(q, (rows, ss), np.where(ss == D5, stay * (1.0 - leak), stay))
        q = q.reshape(n_p, n_g, N_TRANSIENT, N_TRANSIENT)
        v = np.einsum("pgs,pgsd->pgd", v, q)
    return out
