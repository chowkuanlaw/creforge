"""Keep the documented contract honest: docs/data-dictionary.md vs actual output."""

import re
from pathlib import Path

import creforge as cf
from creforge.generator import SCHEMA_VERSION, TABLES

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _documented_columns() -> dict[str, list[str]]:
    text = (DOCS / "data-dictionary.md").read_text(encoding="utf-8")
    tables: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        heading = re.match(r"^## `([\w.]+)`", line)
        if heading:
            current = heading.group(1)
            tables[current] = []
            continue
        row = re.match(r"^\| `(\w+)` \|", line)
        if row and current in TABLES:
            tables[current].append(row.group(1))
    return {t: cols for t, cols in tables.items() if t in TABLES}


def test_data_dictionary_matches_output(small_dataset):
    documented = _documented_columns()
    assert set(documented) == set(TABLES)
    for table in TABLES:
        assert small_dataset.table(table).columns == documented[table], table


def test_data_dictionary_states_current_schema_version():
    text = (DOCS / "data-dictionary.md").read_text(encoding="utf-8")
    assert f"**schema version {SCHEMA_VERSION}**" in text


def test_public_api_matches_stability_doc():
    text = (DOCS / "stability.md").read_text(encoding="utf-8")
    section = text.split("## Public Python API")[1].split("##")[0]
    documented = set(re.findall(r"`(\w+)`", section)) - {"creforge"}
    assert set(cf.__all__) == documented
