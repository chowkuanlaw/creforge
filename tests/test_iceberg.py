import pytest
from click.testing import CliRunner

import creforge as cf
from creforge.cli import main

pyiceberg = pytest.importorskip("pyiceberg")
from pyiceberg.catalog import load_catalog  # noqa: E402


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_local_iceberg_load_and_replace(tmp_path, fmt):
    data = tmp_path / "data"
    cfg = cf.Config.from_profile("baseline", subjects=80, months=12, seed=4, chunk_size=40)
    manifest = cf.write_dataset(cfg, data, format=fmt)
    root = tmp_path / "iceberg"
    root.mkdir()
    catalog_uri = f"sqlite:///{root / 'catalog.db'}"
    warehouse = (root / "warehouse").as_uri()

    counts = cf.load_iceberg(data, catalog_uri, warehouse)
    assert counts == manifest["row_counts"]
    catalog = load_catalog("creforge", type="sql", uri=catalog_uri, warehouse=warehouse)
    assert {name: catalog.load_table(f"creforge.{name}").scan().to_arrow().num_rows
            for name in counts} == counts
    loaded_ids = set(catalog.load_table("creforge.subject").scan().to_arrow()["subject_id"].to_pylist())
    source_ids = {subject_id for chunk in cf.DiskDataset.open(data).iter_chunks()
                  for subject_id in chunk["subject"]["subject_id"]}
    assert loaded_ids == source_ids

    with pytest.raises(ValueError, match="already exist"):
        cf.load_iceberg(data, catalog_uri, warehouse)
    result = CliRunner().invoke(main, ["load", "iceberg", str(data), "--catalog-uri", catalog_uri,
                                       "--warehouse", warehouse, "--replace"])
    assert result.exit_code == 0, result.output
    assert {name: catalog.load_table(f"creforge.{name}").scan().to_arrow().num_rows
            for name in counts} == counts
