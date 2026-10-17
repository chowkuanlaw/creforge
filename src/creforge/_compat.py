"""Deprecation helpers: old names keep working, with a warning, until the next major version."""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)


def renamed_kwarg(old: str, new: str, removed_in: str = "2.0") -> Callable[[F], F]:
    """Accept keyword ``old`` as an alias of ``new``, with a DeprecationWarning."""

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if old in kwargs:
                if new in kwargs:
                    raise TypeError(f"{fn.__name__}() got both {old!r} and {new!r}; use {new!r}")
                warnings.warn(f"{fn.__name__}({old}=...) is deprecated; use {new}=... "
                              f"(the old name is removed in creforge {removed_in})",
                              DeprecationWarning, stacklevel=2)
                kwargs[new] = kwargs.pop(old)
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate
