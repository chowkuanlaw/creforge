"""Frozen output formats (docs/stability.md): names and types may only be added to.

A failure here means a documented format changed. If the change is intended, it needs a
major version (removals, renames, type changes) or a minor version (additions); then
regenerate with CREFORGE_UPDATE_SNAPSHOTS=1.
"""

import json
import os
from pathlib import Path

import polars as pl
import pytest

import creforge as cf

SNAPSHOT = Path(__file__).parent / "snapshots" / "contracts.json"
UPDATE = os.environ.get("CREFORGE_UPDATE_SNAPSHOTS") == "1"


def _schema(df: pl.DataFrame) -> dict[str, str]:
    # Enum categories come from the profile, so record the kind of type only.
    return {c: ("Enum" if isinstance(t, pl.Enum) else str(t)) for c, t in df.schema.items()}


def _keys(d, prefix=""):
    """Dotted key paths of nested dicts (dict values whose keys are data are not descended)."""
    out = []
    for k, v in d.items():
        out.append(prefix + k)
        if isinstance(v, dict) and k not in {"tables", "by_type", "causes", "metrics", "mismatched_values"}:
            out += _keys(v, prefix + k + ".")
    return sorted(out)


@pytest.fixture(scope="module")
def current(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("contracts")
    clean = tmp / "clean"
    cf.write_dataset(cf.Config.from_profile("baseline", subjects=300, months=24, seed=4), clean)
    tables = {t: _schema(df) for t, df in next(cf.DiskDataset.open(clean).iter_chunks()).items()}
    cf.inject(clean, tmp / "dirty", faults="light", seed=1)
    answer = pl.read_parquet(tmp / "dirty" / "faults.parquet")
    cf.submissions(clean, tmp / "inbox", issues="nasty", seed=1)
    log = pl.read_parquet(tmp / "inbox" / "submissions.parquet")
    record = pl.read_parquet(tmp / "inbox" / log["file"][0])
    issues = pl.read_parquet(tmp / "inbox" / "issues.parquet")
    feats = cf.features(clean, "2023-12", include_truth=True)
    findings = answer.select("table", "row_key", "column")
    rebuilt = {"account": next(cf.DiskDataset.open(clean).iter_chunks())["account"],
               "account_month": pl.DataFrame()}
    return {
        "tables": tables,
        "faults.parquet": _schema(answer),
        "submission_record": _schema(record),
        "submissions.parquet": _schema(log),
        "issues.parquet": _schema(issues),
        "inbox.json": sorted(json.loads((tmp / "inbox" / "inbox.json").read_text())),
        "features": _schema(feats),
        "validate --json": _keys(cf.validate(clean).to_dict()),
        "score --json": _keys(cf.score(answer, findings).to_dict()),
        "reconcile --json": _keys(cf.reconcile(clean, {**rebuilt, "account_month": next(
            cf.DiskDataset.open(clean).iter_chunks())["account_month"]}).to_dict()),
    }


def test_formats_unchanged(current):
    if UPDATE or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8")
    frozen = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for name, before in frozen.items():
        now = current[name]
        if isinstance(before, list):
            assert set(before) <= set(now), f"{name}: removed {sorted(set(before) - set(now))}"
        elif name == "tables":
            for table, cols in before.items():
                assert list(cols.items()) == list(now[table].items())[:len(cols)], f"table {table} changed"
        else:
            assert list(before.items()) == list(now.items())[:len(before)], f"{name} changed"
