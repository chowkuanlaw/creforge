"""Chunked generation: subjects -> pre-window accounts -> inquiries -> accounts -> months."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import polars as pl

from . import engine as E
from . import parties as P
from .config import Config, Profile
from .ids import make_ids

TABLES = ("subject", "inquiry", "account", "account_month", "account_party")
OUTCOMES = ("approved", "declined", "withdrawn")
MAX_PRE_WINDOW_AGE = 240
MIN_BORROWER_AGE = 18


@dataclass(frozen=True)
class Context:
    cfg: Config
    tables: E.Tables
    init_table: np.ndarray
    macro: np.ndarray
    start: np.datetime64  # window start month (datetime64[M])

    @property
    def prof(self) -> Profile:
        return self.cfg.profile


@lru_cache(maxsize=8)
def _init_table(prof_json: str) -> np.ndarray:
    prof = Profile.model_validate_json(prof_json)
    return E.initial_state_table(E.Tables.from_profile(prof), MAX_PRE_WINDOW_AGE)


def make_context(cfg: Config) -> Context:
    return Context(
        cfg=cfg,
        tables=E.Tables.from_profile(cfg.profile),
        init_table=_init_table(cfg.profile.model_dump_json()),
        macro=cfg.profile.macro.path(cfg.months),
        start=np.datetime64(cfg.start_month, "M"),
    )


def _choice(rng: np.random.Generator, probs: np.ndarray, n: int) -> np.ndarray:
    """Vectorized categorical draw from a single probability vector."""
    cum = np.cumsum(probs)
    return np.minimum(np.searchsorted(cum / cum[-1], rng.random(n), side="right"), len(probs) - 1)


def _choice_rows(rng: np.random.Generator, probs: np.ndarray) -> np.ndarray:
    """Vectorized categorical draw, one probability row per sample."""
    cum = probs.cumsum(axis=1)
    u = rng.random(len(probs)) * cum[:, -1]
    return np.minimum((u[:, None] >= cum).sum(axis=1), probs.shape[1] - 1)


def _month_dates(ctx: Context, month: np.ndarray, day: np.ndarray | None = None) -> np.ndarray:
    d = (ctx.start + month.astype("timedelta64[M]")).astype("datetime64[D]")
    return d if day is None else d + (day - 1).astype("timedelta64[D]")


def _month_end(ctx: Context, month: np.ndarray) -> np.ndarray:
    return _month_dates(ctx, month + 1) - np.timedelta64(1, "D")


# --- stage 1: subjects ---------------------------------------------------------------


@dataclass
class Subjects:
    grade: np.ndarray
    band: np.ndarray
    birth_year: np.ndarray
    region: np.ndarray
    max_history: np.ndarray  # months the subject could have held credit before the window


def _subjects(ctx: Context, n: int, rng) -> Subjects:
    prof = ctx.prof
    grade = _choice(rng, np.array([g.share for g in prof.grades.values()]), n).astype(np.int8)
    band = _choice(rng, np.array([b.share for b in prof.population.income_bands.values()]), n)
    lo, hi = prof.population.age_years
    age = rng.integers(lo, hi + 1, n)
    start_year = ctx.start.astype("datetime64[Y]").astype(int) + 1970
    region = rng.integers(1, prof.population.regions + 1, n)
    return Subjects(
        grade=grade,
        band=band.astype(np.int8),
        birth_year=(start_year - age).astype(np.int16),
        region=region.astype(np.int8),
        max_history=((age - MIN_BORROWER_AGE) * 12).astype(np.int32),
    )


# --- stage 2/4: account terms --------------------------------------------------------


@dataclass
class Terms:
    subject: np.ndarray
    product: np.ndarray
    amount: np.ndarray
    tenor: np.ndarray
    rate: np.ndarray  # annual


def _draw_terms(ctx: Context, subj: Subjects, s_idx: np.ndarray, product: np.ndarray, rng) -> Terms:
    prof, names = ctx.prof, ctx.tables.products
    n = len(s_idx)
    band_mult = np.array([b.amount_mult for b in prof.population.income_bands.values()])
    spread = np.array([g.rate_spread for g in prof.grades.values()])
    median = np.array([prof.products[p].amount.median for p in names])[product]
    sigma = np.array([prof.products[p].amount.sigma for p in names])[product]
    round_to = np.array([prof.products[p].round_to for p in names])[product]
    raw = median * band_mult[subj.band[s_idx]] * np.exp(rng.normal(0.0, 1.0, n) * sigma)
    amount = np.maximum(np.round(raw / round_to), 1.0) * round_to

    tenor = np.zeros(n, dtype=np.int32)
    u = rng.random(n)
    for i, name in enumerate(names):
        opts = prof.products[name].tenor_months
        if opts:
            m = product == i
            tenor[m] = np.asarray(opts)[np.minimum((u[m] * len(opts)).astype(int), len(opts) - 1)]
    base = np.array([prof.products[p].interest_rate for p in names])[product]
    sens = np.array([prof.products[p].rate_grade_sensitivity for p in names])[product]
    rate = np.round(base + spread[subj.grade[s_idx]] * sens, 4)
    return Terms(subject=s_idx, product=product, amount=amount, tenor=tenor, rate=rate)


# --- stage 2: accounts opened before the window --------------------------------------


def _pre_window(ctx: Context, subj: Subjects, rng) -> tuple[Terms, np.ndarray]:
    prof, names, t = ctx.prof, ctx.tables.products, ctx.tables
    n = len(subj.grade)
    hold = np.array([prof.products[p].holding_rate for p in names])
    hmult = np.array([b.holding_mult for b in prof.population.income_bands.values()])
    counts = rng.poisson(hold[None, :] * hmult[subj.band][:, None])  # (n, P)
    s_idx = np.repeat(np.repeat(np.arange(n), len(names)), counts.ravel())
    product = np.repeat(np.tile(np.arange(len(names)), n), counts.ravel()).astype(np.int16)
    terms = _draw_terms(ctx, subj, s_idx, product, rng)

    cap = np.array([prof.products[p].max_age_months for p in names])[product]
    cap = np.where(t.revolving[product], cap, np.minimum(cap, terms.tenor - 1))
    cap = np.clip(np.minimum(cap, subj.max_history[s_idx]), 0, MAX_PRE_WINDOW_AGE)
    age0 = np.floor(rng.random(len(s_idx)) * (cap + 1)).astype(np.int32)
    return terms, age0


# --- stage 3: inquiries --------------------------------------------------------------


@dataclass
class Inquiries:
    subject: np.ndarray
    month: np.ndarray
    day: np.ndarray
    product: np.ndarray
    lender: np.ndarray
    requested: np.ndarray
    outcome: np.ndarray
    terms: Terms


def _inquiries(ctx: Context, subj: Subjects, rng) -> Inquiries:
    prof, names, months = ctx.prof, ctx.tables.products, ctx.cfg.months
    rate = np.array([g.inquiry_rate for g in prof.grades.values()])[subj.grade]
    counts = rng.poisson(rate * months)
    s_idx = np.repeat(np.arange(len(subj.grade)), counts)
    k = len(s_idx)
    month = rng.integers(0, months, k).astype(np.int32)
    day = rng.integers(1, 29, k).astype(np.int32)
    order = np.lexsort((day, month, s_idx))
    s_idx, month, day = s_idx[order], month[order], day[order]

    product = _choice(rng, np.array([prof.products[p].inquiry_share for p in names]), k)
    product = product.astype(np.int16)
    lender = rng.integers(1, prof.lenders + 1, k).astype(np.int16)
    terms = _draw_terms(ctx, subj, s_idx, product, rng)

    # "Credit hungry" penalty: prior inquiries in the trailing window lower approval odds.
    w = prof.inquiries.recent_window_months
    stride, offset = 64, w + 1
    key = s_idx.astype(np.int64) * (months + offset + 1) * stride + (month + offset) * stride + day
    lo_key = s_idx.astype(np.int64) * (months + offset + 1) * stride + (month + offset - w) * stride + day
    recent = np.arange(k) - np.searchsorted(key, lo_key, side="left")

    appr = np.array([g.approval for g in prof.grades.values()])[subj.grade[s_idx]]
    logit = np.log(appr / (1.0 - appr)) - prof.inquiries.recent_penalty * recent
    approved = rng.random(k) < 1.0 / (1.0 + np.exp(-logit))
    withdrawn = approved & (rng.random(k) < prof.inquiries.withdrawn_share)
    outcome = np.where(withdrawn, 2, np.where(approved, 0, 1)).astype(np.int8)
    return Inquiries(s_idx, month, day, product, lender, terms.amount, outcome, terms)


# --- stage 5: simulation -------------------------------------------------------------


def _initial_balances(ctx: Context, book: E.Book, idx: np.ndarray, age0: np.ndarray, rng) -> None:
    t = ctx.tables
    rev = t.revolving[book.product[idx]]
    mia = book.mia[idx]
    # Installment: scheduled balance allowing for missed installments.
    sched = E.scheduled_balance(
        book.principal[idx], book.rate_m[idx], book.payment[idx], np.maximum(age0 - mia, 0)
    )
    inst_arr = np.minimum(mia * book.payment[idx], sched)
    # Revolving: utilisation around the grade target; delinquent cards carry a balance.
    noise = np.exp(rng.normal(0.0, 0.35, len(idx)))
    util = np.clip(t.g_util[book.grade[idx]] * noise, 0.0, 1.0) * (rng.random(len(idx)) < 0.8)
    util = np.where(mia > 0, np.maximum(util, 0.3), util)
    rbal = util * book.limit[idx]
    rarr = np.minimum(mia * t.min_pct[book.product[idx]] * rbal, rbal)
    book.bal[idx] = np.round(np.where(rev, rbal, sched), 2)
    book.arrears[idx] = np.round(np.where(rev, rarr, inst_arr), 2)
    book.inst[idx] = E.next_installment(t, book, idx)


def _build_book(ctx: Context, subj: Subjects, pre: Terms, age0: np.ndarray, new: Terms,
                new_month: np.ndarray, links: P.Links, rng) -> E.Book:
    t = ctx.tables
    product = np.concatenate([pre.product, new.product]).astype(np.int16)
    s_idx = np.concatenate([pre.subject, new.subject])
    grade = subj.grade[s_idx]
    amount = np.concatenate([pre.amount, new.amount])
    tenor = np.concatenate([pre.tenor, new.tenor]).astype(np.int32)
    rate_m = np.concatenate([pre.rate, new.rate]) / 12.0
    rev = t.revolving[product]
    principal = np.where(rev, 0.0, amount)
    n, n_pre = len(product), len(pre.product)
    joint_grade = np.where(links.joint >= 0, subj.grade[np.maximum(links.joint, 0)], -1)
    roll_mult, cure_mult = P.blended_multipliers(t.g_roll, t.g_cure, grade, joint_grade)
    book = E.Book(
        product=product,
        grade=grade,
        open_abs=np.concatenate([-age0, new_month]).astype(np.int32),
        limit=np.where(rev, amount, 0.0),
        principal=principal,
        tenor=np.where(rev, 0, tenor).astype(np.int32),
        rate_m=rate_m,
        payment=np.round(np.where(rev, 0.0, E.annuity(principal, rate_m, tenor)), 2),
        state=np.zeros(n, dtype=np.int8),
        mia=np.zeros(n, dtype=np.int32),
        bal=np.zeros(n),
        arrears=np.zeros(n),
        inst=np.zeros(n),
        active=np.ones(n, dtype=bool),
        close_month=np.full(n, -1, dtype=np.int32),
        close_reason=np.full(n, E.CR_NONE, dtype=np.int8),
        roll_mult=roll_mult,
        cure_mult=cure_mult,
        guaranteed=links.guarantor >= 0,
    )
    # Pre-window accounts start in a state drawn from the engine's own age profile, using
    # the grade closest to the account's (possibly joint-blended) risk.
    pre_idx = np.arange(n_pre)
    init_grade = P.nearest_grade(t.g_roll, roll_mult[:n_pre])
    probs = ctx.init_table[product[:n_pre], init_grade, age0]
    state = _choice_rows(rng, probs)
    extra = rng.integers(0, t.writeoff_after, n_pre)
    mia = np.where(state <= E.D5, state, 0) + np.where(state == E.D5, extra, 0)
    book.state[pre_idx] = state
    book.mia[pre_idx] = mia
    _initial_balances(ctx, book, pre_idx, age0, rng)
    return book


def _simulate(ctx: Context, book: E.Book, rng) -> E.Rows:
    t, months = ctx.tables, ctx.cfg.months
    parts: list[E.Rows] = []
    for m in range(months):
        step_idx = np.flatnonzero(book.active & (book.open_abs < m))
        if len(step_idx):
            parts.append(E.step(t, book, step_idx, m, float(ctx.macro[m]), rng))
        new_idx = np.flatnonzero(book.open_abs == m)
        if len(new_idx):
            parts.append(E.open_rows(t, book, new_idx, m, rng))
    fields = E.Rows.__dataclass_fields__
    rows = E.Rows(**{f: np.concatenate([getattr(p, f) for p in parts]) for f in fields})
    order = np.lexsort((rows.month, rows.account))
    return E.Rows(**{f: getattr(rows, f)[order] for f in fields})


# --- frames --------------------------------------------------------------------------


def _enum(values, categories) -> pl.Series:
    return pl.Series(np.asarray(categories, dtype=object)[values]).cast(pl.Enum(list(categories)))


def generate_chunk(cfg: Config, chunk: int, ctx: Context | None = None) -> dict[str, pl.DataFrame]:
    """Generate one chunk of subjects and everything that hangs off them."""
    ctx = ctx or make_context(cfg)
    prof, t = cfg.profile, ctx.tables
    lo, hi = cfg.chunk_bounds(chunk)
    n = hi - lo
    seed_seq = np.random.SeedSequence(cfg.seed).spawn(cfg.n_chunks)[chunk]
    rng = np.random.default_rng(seed_seq)

    subj = _subjects(ctx, n, rng)
    pre, age0 = _pre_window(ctx, subj, rng)
    pre_lender = rng.integers(1, prof.lenders + 1, len(pre.product)).astype(np.int16)
    inq = _inquiries(ctx, subj, rng)

    # Approved inquiries open accounts later the same month (open_date >= inquiry_date).
    ok = np.flatnonzero(inq.outcome == 0)
    new = Terms(**{f: getattr(inq.terms, f)[ok] for f in Terms.__dataclass_fields__})
    haircut = np.where(t.revolving[new.product], 1.0, rng.uniform(0.7, 1.0, len(ok)))
    round_to = np.array([prof.products[p].round_to for p in t.products])[new.product]
    new.amount = np.maximum(np.round(new.amount * haircut / round_to), 1.0) * round_to
    open_day = inq.day[ok] + np.floor(rng.random(len(ok)) * (29 - inq.day[ok])).astype(np.int32)

    # Linked parties use their own random stream, independent of the main one.
    party_rng = np.random.default_rng(seed_seq.spawn(1)[0])
    start_year = ctx.start.astype("datetime64[Y]").astype(int) + 1970
    links = P.select(
        prof, t.products, t.grades,
        subj_age=(start_year - subj.birth_year).astype(np.int64),
        subj_region=subj.region.astype(np.int64),
        subj_grade=subj.grade.astype(np.int64),
        subj_max_history=subj.max_history,
        acc_subject=np.concatenate([pre.subject, new.subject]),
        acc_product=np.concatenate([pre.product, new.product]).astype(np.int64),
        acc_age0=np.concatenate([age0, np.zeros(len(ok), dtype=np.int32)]),
        rng=party_rng,
    )
    book = _build_book(ctx, subj, pre, age0, new, inq.month[ok], links, rng)
    rows = _simulate(ctx, book, rng)

    # ids
    subject_ids = make_ids(cfg.seed, "subject", chunk, n)
    inquiry_ids = make_ids(cfg.seed, "inquiry", chunk, len(inq.subject))
    account_ids = make_ids(cfg.seed, "account", chunk, book.size)
    lender_codes = np.array([f"L{i:04d}" for i in range(prof.lenders + 1)], dtype=object)

    inquiry_date = _month_dates(ctx, inq.month, inq.day)
    n_pre = len(pre.product)
    pre_open = _month_dates(ctx, -age0, rng.integers(1, 29, n_pre))
    open_date = np.concatenate([pre_open, _month_dates(ctx, inq.month[ok], open_day)])
    acc_subject = np.concatenate([pre.subject, new.subject])
    acc_rate = np.concatenate([pre.rate, new.rate])
    acc_amount = np.concatenate([pre.amount, new.amount])
    rev = t.revolving[book.product]
    closed = book.close_month >= 0

    # created_month: first month the subject appears in the data.
    first = np.full(n, np.datetime64("NaT", "D"))
    seen = pl.DataFrame({"s": np.concatenate([acc_subject, inq.subject]),
                         "d": np.concatenate([open_date, inquiry_date])})
    seen = seen.group_by("s").agg(pl.col("d").min()).sort("s")
    first[seen["s"].to_numpy()] = seen["d"].to_numpy()
    start_day = ctx.start.astype("datetime64[D]")
    first = np.where(np.isnat(first), start_day, first).astype("datetime64[M]").astype("datetime64[D]")

    band_names = list(prof.population.income_bands)
    subject = pl.DataFrame({
        "subject_id": subject_ids,
        "birth_year": subj.birth_year,
        "region": [f"R{r:02d}" for r in subj.region],
        "income_band": _enum(subj.band, band_names),
        "risk_grade": _enum(subj.grade, t.grades),
        "created_month": first,
    })

    inquiry = pl.DataFrame({
        "inquiry_id": inquiry_ids,
        "subject_id": subject_ids[inq.subject],
        "inquiry_date": inquiry_date,
        "lender_id": lender_codes[inq.lender],
        "product_type": _enum(inq.product, t.products),
        "requested_amount": inq.requested,
        "outcome": _enum(inq.outcome, OUTCOMES),
    })

    acc_inquiry = pl.concat([pl.Series([None] * n_pre, dtype=pl.String), pl.Series(inquiry_ids[ok])])
    acc_lender = np.concatenate([pre_lender, inq.lender[ok]])
    tenor = np.where(rev, 0, book.tenor)
    secured = np.array([prof.products[p].secured for p in t.products])[book.product]
    reason = np.where(closed, book.close_reason, 0)
    close_date = _month_end(ctx, np.maximum(book.close_month, 0))
    account = pl.DataFrame({
        "account_id": account_ids,
        "subject_id": subject_ids[acc_subject],
        "inquiry_id": acc_inquiry,
        "lender_id": lender_codes[acc_lender],
        "product_type": _enum(book.product, t.products),
        "open_date": open_date,
        "credit_limit": pl.Series(np.where(rev, acc_amount, np.nan)).fill_nan(None),
        "principal": pl.Series(np.where(rev, np.nan, acc_amount)).fill_nan(None),
        "tenor_months": pl.Series(tenor, dtype=pl.Int16).set(pl.Series(rev), None),
        "interest_rate": acc_rate,
        "secured": secured,
        "close_date": pl.Series(close_date).set(pl.Series(~closed), None),
        "close_reason": _enum(reason, E.CLOSE_REASONS).set(pl.Series(~closed), None),
    })

    account_month = pl.DataFrame({
        "account_id": account_ids[rows.account],
        "as_of_month": _month_dates(ctx, rows.month.astype(np.int32)),
        "balance": rows.balance,
        "amount_due": rows.amount_due,
        "amount_paid": rows.amount_paid,
        "dpd_bucket": _enum(rows.dpd, E.DPD_BUCKETS),
        "months_in_arrears": rows.mia,
        "status": _enum(rows.status, E.STATUSES),
    })
    # account_party: one primary row per account, plus joint / guarantor rows.
    acc_all = np.arange(book.size)
    has_j, has_g = links.joint >= 0, links.guarantor >= 0
    p_acc = np.concatenate([acc_all, acc_all[has_j], acc_all[has_g]])
    p_subj = np.concatenate([acc_subject, links.joint[has_j], links.guarantor[has_g]])
    p_role = np.concatenate([
        np.full(book.size, P.ROLE_PRIMARY), np.full(has_j.sum(), P.ROLE_JOINT),
        np.full(has_g.sum(), P.ROLE_GUARANTOR),
    ]).astype(np.int8)
    order = np.lexsort((p_role, p_acc))
    p_acc, p_subj, p_role = p_acc[order], p_subj[order], p_role[order]
    account_party = pl.DataFrame({
        "account_id": account_ids[p_acc],
        "subject_id": subject_ids[p_subj],
        "role": _enum(p_role, P.ROLES),
        "start_date": open_date[p_acc],
    })
    return {"subject": subject, "inquiry": inquiry, "account": account, "account_month": account_month,
            "account_party": account_party}
