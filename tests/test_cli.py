import json

from click.testing import CliRunner

from creforge.cli import main


def test_generate_then_validate(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out"
    res = runner.invoke(main, ["generate", "-n", "800", "-m", "12", "-s", "3", "-o", str(out),
                               "--chunk-size", "400"])
    assert res.exit_code == 0, res.output
    assert "account_month" in res.output and "account_party" in res.output
    res = runner.invoke(main, ["validate", str(out), "--json", "--report", str(tmp_path / "r.md")])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["integrity_ok"] is True
    assert (tmp_path / "r.md").read_text().startswith("# creforge validation report")


def test_profiles_commands():
    runner = CliRunner()
    assert "stressed" in runner.invoke(main, ["profiles", "list"]).output
    shown = runner.invoke(main, ["profiles", "show", "stressed"])
    assert shown.exit_code == 0 and "points" in shown.output


def test_rejects_bad_months():
    res = CliRunner().invoke(main, ["generate", "-m", "6", "-o", "x"])
    assert res.exit_code != 0


def test_friendly_error_for_non_dataset(tmp_path):
    res = CliRunner().invoke(main, ["validate", str(tmp_path)])
    assert res.exit_code == 1
    assert "is not a creforge dataset" in res.output
    assert "Traceback" not in res.output


def test_friendly_error_for_unknown_profile(tmp_path):
    res = CliRunner().invoke(main, ["generate", "-p", "nope", "-o", str(tmp_path / "o")])
    assert res.exit_code == 1
    assert "unknown profile" in res.output


def test_friendly_error_for_invalid_profile(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("extends: baseline\nlenders: 0\n")
    res = CliRunner().invoke(main, ["generate", "-p", str(bad), "-o", str(tmp_path / "o")])
    assert res.exit_code == 1
    assert "invalid configuration" in res.output


def test_decimal_flag(tmp_path):
    res = CliRunner().invoke(main, ["generate", "-n", "200", "-m", "12", "--money", "decimal",
                                    "-o", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert json.loads((tmp_path / "manifest.json").read_text())["config"]["money"] == "decimal"
