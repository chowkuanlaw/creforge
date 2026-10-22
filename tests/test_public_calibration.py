"""Public-source provenance and comparisons use controlled CSV responses in CI."""

import io
import json
from datetime import date

import pytest
from click.testing import CliRunner

import creforge as cf
from creforge import public_calibration as pc
from creforge.cli import main


def _csv(series, values=(1.0, 2.0, 3.0)):
    return (f"observation_date,{series}\n"
            f"2015-01-01,{values[0]}\n2015-04-01,{values[1]}\n"
            f"2015-07-01,{values[2]}\n").encode()


def _mock_fred(monkeypatch):
    seen = []

    def download(request, timeout):
        series = request.full_url.rsplit("=", 1)[1]
        seen.append((series, timeout))
        return io.BytesIO(_csv(series))

    monkeypatch.setattr(pc, "urlopen", download)
    return seen


def test_snapshot_fetch_comparison_and_cli(tmp_path, monkeypatch):
    seen = _mock_fred(monkeypatch)
    reference_path = tmp_path / "reference"
    reference = cf.fetch_public_reference(
        reference_path, start=date(2015, 1, 1), end=date(2015, 7, 1)
    )
    assert {series for series, _ in seen} == set(pc.SERIES_IDS)
    assert all(timeout == 30 for _, timeout in seen)
    assert reference.values["DRCCLACBS"] == [0.01, 0.02, 0.03]
    assert cf.load_public_reference(reference_path) == reference
    manifest = json.loads((reference_path / "manifest.json").read_text())
    assert manifest["source"] == pc.SOURCE
    assert manifest["series"]["DRCCLACBS"]["url"] == pc.FRED_URL.format(series="DRCCLACBS")

    data = cf.generate(cf.Config.from_profile(subjects=500, months=12, seed=7))
    comparison = cf.compare_public_reference(data, reference_path)
    card = next(row for row in comparison.comparisons
                if row.product == "credit_card" and row.metric == "dpd30_share")
    assert card.series == "DRCCLACBS" and card.quarters == 3
    assert card.reference_min == 0.01 and card.reference_median == 0.02
    assert card.reference_max == 0.03
    assert card.generated == cf.validate(data).metrics["products"]["credit_card"]["dpd30_share"]
    assert card.median_gap_percentage_points == pytest.approx((card.generated - 0.02) * 100)
    assert card.target == data.config.profile.targets["credit_card"].dpd30_share
    assert len(comparison.comparisons) == 8  # no invented overdraft or BNPL public series
    assert "balance" in comparison.to_markdown()

    dataset_path = tmp_path / "dataset"
    data.write(dataset_path)
    markdown, output_json = tmp_path / "report.md", tmp_path / "report.json"
    result = CliRunner().invoke(main, ["calibration", "report", str(dataset_path),
                                       "--reference", str(reference_path),
                                       "--out", str(markdown), "--json-out", str(output_json)])
    assert result.exit_code == 0, result.output
    assert "DRCCLACBS" in markdown.read_text()
    payload = json.loads(output_json.read_text())
    assert payload["hashes"] == reference.hashes
    assert payload["reference_start"] == "2015-01-01"

    cli_reference = tmp_path / "from_cli"
    result = CliRunner().invoke(main, ["calibration", "fetch", "--out", str(cli_reference),
                                       "--start", "2015-01-01", "--end", "2015-07-01"])
    assert result.exit_code == 0, result.output
    assert cf.load_public_reference(cli_reference).values == reference.values


def test_reference_rejects_tampering_and_incomplete_quarters(tmp_path, monkeypatch):
    _mock_fred(monkeypatch)
    path = tmp_path / "reference"
    cf.fetch_public_reference(path, start=date(2015, 1, 1), end=date(2015, 7, 1))
    with pytest.raises(ValueError, match="already exists"):
        cf.fetch_public_reference(path, start=date(2015, 1, 1), end=date(2015, 7, 1))
    (path / "DRCCLACBS.csv").write_bytes(_csv("DRCCLACBS", (1.0, 2.0, 4.0)))
    with pytest.raises(ValueError, match="checksum mismatch"):
        cf.load_public_reference(path)

    def incomplete(request, timeout):
        series = request.full_url.rsplit("=", 1)[1]
        return io.BytesIO(_csv(series, (1.0, 2.0, 3.0)).replace(b"2015-04-01,2.0\n", b""))

    monkeypatch.setattr(pc, "urlopen", incomplete)
    missing_path = tmp_path / "missing"
    with pytest.raises(ValueError, match="missing reference quarters"):
        cf.fetch_public_reference(missing_path, start=date(2015, 1, 1), end=date(2015, 7, 1))
    assert not missing_path.exists()
