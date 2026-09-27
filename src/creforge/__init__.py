"""creforge: PII-safe synthetic credit bureau data from explicit behavioural rules."""

__version__ = "0.5.0"

from .config import Config, Profile, list_profiles, load_profile  # noqa: E402
from .dataset import Dataset, DiskDataset, generate, write_dataset  # noqa: E402
from .faults import (  # noqa: E402
    ScoreReport,
    inject,
    list_fault_profiles,
    load_fault_profile,
    row_key,
    score,
)
from .validate import Report, validate  # noqa: E402

__all__ = [
    "Config",
    "Dataset",
    "DiskDataset",
    "Profile",
    "Report",
    "ScoreReport",
    "__version__",
    "generate",
    "inject",
    "list_fault_profiles",
    "list_profiles",
    "load_fault_profile",
    "load_profile",
    "row_key",
    "score",
    "validate",
    "write_dataset",
]
