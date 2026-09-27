"""creforge: PII-safe synthetic credit bureau data from explicit behavioural rules."""

__version__ = "0.7.0"

from .config import Config, Profile, list_profiles, load_profile  # noqa: E402
from .dataset import Dataset, DiskDataset, generate, write_dataset  # noqa: E402
from .ddl import copy_script, ddl  # noqa: E402
from .faults import (  # noqa: E402
    ScoreReport,
    inject,
    list_fault_profiles,
    load_fault_profile,
    row_key,
    score,
)
from .load import load_duckdb  # noqa: E402
from .reconcile import ReconcileReport, reconcile  # noqa: E402
from .submissions import IssueProfile, list_issue_profiles, load_issue_profile, submissions  # noqa: E402
from .validate import Report, validate  # noqa: E402

__all__ = [
    "Config",
    "Dataset",
    "DiskDataset",
    "IssueProfile",
    "Profile",
    "ReconcileReport",
    "Report",
    "ScoreReport",
    "__version__",
    "copy_script",
    "ddl",
    "generate",
    "inject",
    "list_fault_profiles",
    "list_issue_profiles",
    "list_profiles",
    "load_duckdb",
    "load_fault_profile",
    "load_issue_profile",
    "load_profile",
    "reconcile",
    "row_key",
    "score",
    "submissions",
    "validate",
    "write_dataset",
]
