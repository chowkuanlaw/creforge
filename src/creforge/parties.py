"""Joint borrowers and guarantors: who is linked to which account.

Candidates are always drawn from the same chunk of subjects, so each chunk stays
self-contained. Selection is vectorized: every account that needs a linked party
draws a target (region, age), picks a random subject from that bucket, and retries a
bounded number of times. Accounts that find no suitable person simply stay unlinked.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Profile

ROLES = ("primary", "joint", "guarantor")
ROLE_PRIMARY, ROLE_JOINT, ROLE_GUARANTOR = range(3)
_AGE_SLOTS = 128  # ages fit in 7 bits; region * slots + age is a sortable bucket key
_JOINT_TRIES = 6
_GUARANTOR_TRIES = 10


@dataclass
class Links:
    joint: np.ndarray  # int64 subject index per account, -1 if none
    guarantor: np.ndarray


class _Buckets:
    """Subjects sorted by (region, age) for fast random picks within a bucket."""

    def __init__(self, region: np.ndarray, age: np.ndarray):
        key = region.astype(np.int64) * _AGE_SLOTS + age
        self.order = np.argsort(key, kind="stable")
        self.keys = key[self.order]

    def pick(self, region: np.ndarray, age: np.ndarray, u: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        valid = (age >= 0) & (age < _AGE_SLOTS)
        key = region.astype(np.int64) * _AGE_SLOTS + np.clip(age, 0, _AGE_SLOTS - 1)
        lo = np.searchsorted(self.keys, key, side="left")
        hi = np.searchsorted(self.keys, key, side="right")
        found = valid & (hi > lo)
        pos = np.minimum(lo + np.floor(u * (hi - lo)).astype(np.int64), len(self.keys) - 1)
        return self.order[pos], found


def select(
    prof: Profile,
    product_names: tuple[str, ...],
    grade_names: tuple[str, ...],
    subj_age: np.ndarray,
    subj_region: np.ndarray,
    subj_grade: np.ndarray,
    subj_max_history: np.ndarray,
    acc_subject: np.ndarray,
    acc_product: np.ndarray,
    acc_age0: np.ndarray,
    rng: np.random.Generator,
) -> Links:
    """Choose a joint borrower and/or a guarantor for each account (or -1)."""
    n = len(acc_subject)
    joint = np.full(n, -1, dtype=np.int64)
    guarantor = np.full(n, -1, dtype=np.int64)
    cfg = prof.parties
    if not cfg.enabled or n == 0:
        return Links(joint, guarantor)

    rates = [cfg.products.get(p) for p in product_names]
    joint_rate = np.array([r.joint if r else 0.0 for r in rates])[acc_product]
    guar_rate = np.array([r.guarantor if r else 0.0 for r in rates])[acc_product]
    buckets = _Buckets(subj_region, subj_age)

    # Joint borrower: a household member, same region, similar age, old enough at opening.
    pending = np.flatnonzero(rng.random(n) < joint_rate)
    gap = cfg.joint_max_age_gap
    for _ in range(_JOINT_TRIES):
        if not len(pending):
            break
        s = acc_subject[pending]
        target_age = subj_age[s] + rng.integers(-gap, gap + 1, len(pending))
        pick, found = buckets.pick(subj_region[s], target_age, rng.random(len(pending)))
        ok = found & (pick != s) & (subj_max_history[pick] >= acc_age0[pending])
        joint[pending[ok]] = pick[ok]
        pending = pending[~ok]

    # Guarantor: an older relative, usually a better grade; more likely for risky or young
    # primaries.
    risky = np.isin(np.asarray(grade_names)[subj_grade[acc_subject]], cfg.guarantor_risky_grades)
    young = subj_age[acc_subject] < cfg.guarantor_young_age
    rate = guar_rate * np.where(risky, cfg.guarantor_risky_mult, 1.0) * np.where(
        young, cfg.guarantor_young_mult, 1.0
    )
    pending = np.flatnonzero(rng.random(n) < np.minimum(rate, 1.0))
    weights = np.array([cfg.guarantor_grade_weights.get(g, 1.0) for g in grade_names])
    accept = weights / weights.max() if weights.max() > 0 else np.ones(len(grade_names))
    lo_gap, hi_gap = cfg.guarantor_age_gap
    n_regions = prof.population.regions
    for _ in range(_GUARANTOR_TRIES):
        if not len(pending):
            break
        k = len(pending)
        s = acc_subject[pending]
        target_age = subj_age[s] + rng.integers(lo_gap, hi_gap + 1, k)
        same = rng.random(k) < cfg.guarantor_same_region
        region = np.where(same, subj_region[s], rng.integers(1, n_regions + 1, k))
        pick, found = buckets.pick(region, target_age, rng.random(k))
        ok = (found & (pick != s) & (pick != joint[pending])
              & (rng.random(k) < accept[subj_grade[pick]]))
        guarantor[pending[ok]] = pick[ok]
        pending = pending[~ok]

    return Links(joint, guarantor)


def blended_multipliers(
    g_roll: np.ndarray, g_cure: np.ndarray, primary_grade: np.ndarray, joint_grade: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Per-account risk multipliers; joint accounts use the geometric mean of both borrowers."""
    has_joint = joint_grade >= 0
    jg = np.where(has_joint, joint_grade, primary_grade)
    roll = np.sqrt(g_roll[primary_grade] * g_roll[jg])
    cure = np.sqrt(g_cure[primary_grade] * g_cure[jg])
    return roll, cure


def nearest_grade(g_roll: np.ndarray, roll_mult: np.ndarray) -> np.ndarray:
    """Grade whose roll multiplier is closest (in log terms) to a blended multiplier."""
    dist = np.abs(np.log(roll_mult)[:, None] - np.log(g_roll)[None, :])
    return dist.argmin(axis=1)
