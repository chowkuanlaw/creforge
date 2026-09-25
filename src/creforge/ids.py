"""Deterministic, non-sequential, collision-free identifiers.

An identifier is ``prefix + 12 Crockford-base32 characters`` encoding a keyed
bijection of a 60-bit integer. Because the mapping is a bijection, distinct inputs
can never collide, and because it is keyed by the run seed, ids reveal nothing about
generation order and differ between seeds.
"""

from __future__ import annotations

import numpy as np

_ALPHABET = np.frombuffer(b"0123456789ABCDEFGHJKMNPQRSTVWXYZ", dtype=np.uint8)
_BITS = 60
_MASK = np.uint64((1 << _BITS) - 1)
_ROUNDS = (
    (0x9E3779B97F4A7C15, 29),
    (0xBF58476D1CE4E5B9, 31),
    (0x94D049BB133111EB, 30),
)

TABLE_KEYS = {"subject": 1, "inquiry": 2, "account": 3}
PREFIXES = {"subject": "S", "inquiry": "Q", "account": "A"}


def table_key(seed: int, table: str) -> int:
    """Derive the permutation key for one table from the run seed."""
    ss = np.random.SeedSequence([seed, 0x1D5, TABLE_KEYS[table]])
    return int(ss.generate_state(1, dtype=np.uint64)[0]) & int(_MASK)


def permute(x: np.ndarray, key: int) -> np.ndarray:
    """Keyed bijection on [0, 2**60). Every step (xor, odd multiply, xorshift, add) is invertible."""
    k = np.uint64(key) & _MASK
    x = (np.asarray(x, dtype=np.uint64) & _MASK) ^ k
    with np.errstate(over="ignore"):
        for mult, shift in _ROUNDS:
            x = (x * np.uint64(mult)) & _MASK
            x ^= x >> np.uint64(shift)
            x = (x + k) & _MASK
    return x


def encode(values: np.ndarray, prefix: str) -> np.ndarray:
    """Encode 60-bit integers as fixed-width ``prefix + 12`` base32 strings."""
    values = np.asarray(values, dtype=np.uint64)
    shifts = np.arange(_BITS - 5, -1, -5, dtype=np.uint64)
    digits = (values[:, None] >> shifts[None, :]) & np.uint64(31)
    chars = _ALPHABET[digits.astype(np.intp)]
    pre = np.frombuffer(prefix.encode("ascii"), dtype=np.uint8)
    full = np.concatenate([np.broadcast_to(pre, (len(values), len(pre))), chars], axis=1)
    width = full.shape[1]
    return np.ascontiguousarray(full).view(f"S{width}").ravel().astype(f"U{width}")


def make_ids(seed: int, table: str, chunk: int, n: int) -> np.ndarray:
    """Ids for ``n`` rows of ``table`` in ``chunk``; unique across all chunks of a run."""
    if n >= 1 << 32 or chunk >= 1 << (_BITS - 32):
        raise ValueError("chunk or row count out of range for id space")
    raw = (np.uint64(chunk) << np.uint64(32)) | np.arange(n, dtype=np.uint64)
    return encode(permute(raw, table_key(seed, table)), PREFIXES[table])
