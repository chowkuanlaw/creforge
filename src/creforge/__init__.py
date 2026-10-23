"""creforge: PII-safe synthetic credit bureau data from explicit behavioural rules."""

__version__ = "1.3.0"

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
from .features import feature_columns, features  # noqa: E402
from .iceberg import load_iceberg  # noqa: E402
from .identities import identities  # noqa: E402
from .load import load_duckdb  # noqa: E402
from .public_calibration import (  # noqa: E402
    PublicCalibrationReport,
    compare_public_reference,
    fetch_public_reference,
    load_public_reference,
)
from .reconcile import ReconcileReport, reconcile  # noqa: E402
from .scenario_comparison import ScenarioComparisonReport, compare_scenario  # noqa: E402
from .scenarios import Scenario, ScenarioConfig, apply_scenario  # noqa: E402
from .submissions import IssueProfile, list_issue_profiles, load_issue_profile, submissions  # noqa: E402
from .validate import Report, validate  # noqa: E402

__all__ = [
    "Config",
    "Dataset",
    "DiskDataset",
    "IssueProfile",
    "Profile",
    "PublicCalibrationReport",
    "ReconcileReport",
    "Report",
    "ScoreReport",
    "Scenario",
    "ScenarioComparisonReport",
    "ScenarioConfig",
    "__version__",
    "apply_scenario",
    "compare_public_reference",
    "compare_scenario",
    "copy_script",
    "ddl",
    "feature_columns",
    "features",
    "fetch_public_reference",
    "generate",
    "identities",
    "inject",
    "list_fault_profiles",
    "list_issue_profiles",
    "list_profiles",
    "load_duckdb",
    "load_iceberg",
    "load_public_reference",
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
