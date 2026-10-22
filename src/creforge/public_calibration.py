"""Reproducible comparison with published US bank aggregate time series."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .dataset import Dataset, DiskDataset
from .validate import validate

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
SOURCE = "Federal Reserve Board, Charge-Off and Delinquency Rates on Loans and Leases at Commercial Banks"
SERIES = {
    "credit_card": {"dpd30_share": "DRCCLACBS", "annual_writeoff_rate": "CORCCACBS"},
    "mortgage": {"dpd30_share": "DRSFRMACBS", "annual_writeoff_rate": "CORSFRMACBS"},
    "personal_loan": {"dpd30_share": "DROCLACBS", "annual_writeoff_rate": "COROCLACBS"},
    "auto_loan": {"dpd30_share": "DROCLACBS", "annual_writeoff_rate": "COROCLACBS"},
}
SERIES_IDS = tuple(sorted({series for metrics in SERIES.values() for series in metrics.values()}))
DEFAULT_START = date(2015, 1, 1)
DEFAULT_END = date(2026, 4, 1)  # 2026 Q2


def _quarters(start: date, end: date) -> list[date]:
    if start > end or start.day != 1 or end.day != 1 or start.month not in (1, 4, 7, 10) \
            or end.month not in (1, 4, 7, 10):
        raise ValueError("reference dates must be ordered quarter starts (YYYY-01/04/07/10-01)")
    quarters = []
    for month in range(start.year * 12 + start.month - 1, end.year * 12 + end.month, 3):
        quarters.append(date(month // 12, month % 12 + 1, 1))
    return quarters


def _read_series(raw: bytes, series: str, quarters: list[date]) -> list[float]:
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        header = reader.fieldnames or []
        date_col = "DATE" if "DATE" in header else "observation_date"
        if date_col not in header or series not in header:
            raise ValueError(f"{series} CSV needs a DATE/observation_date and {series} column")
        selected: dict[date, float] = {}
        needed = set(quarters)
        for row in reader:
            try:
                observation = date.fromisoformat(row[date_col])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{series} has an invalid observation date") from exc
            if observation not in needed:
                continue
            if observation in selected:
                raise ValueError(f"{series} has duplicate quarter {observation}")
            try:
                value = float(row[series])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{series} has no numeric value for {observation}") from exc
            if not math.isfinite(value):
                raise ValueError(f"{series} has a non-finite value for {observation}")
            selected[observation] = value / 100.0  # FRED publishes percent, creforge uses fractions
    except (UnicodeError, csv.Error, KeyError, TypeError) as exc:
        raise ValueError(f"invalid {series} CSV: {exc}") from exc
    if set(selected) != needed:
        missing = sorted(needed - set(selected))
        raise ValueError(f"{series} is missing reference quarters: {missing}")
    return [selected[quarter] for quarter in quarters]


@dataclass(frozen=True)
class PublicReference:
    """A fixed, checksum-verified snapshot of the six published FRED series."""

    start: date
    end: date
    retrieved_at: str
    hashes: dict[str, str]
    values: dict[str, list[float]]


def fetch_public_reference(
    out: str | Path, *, start: date = DEFAULT_START, end: date = DEFAULT_END
) -> PublicReference:
    """Download official CSVs and save an immutable, auditable reference bundle.

    The output path must not exist. A failed download leaves no partial bundle there.
    """
    quarters = _quarters(start, end)
    out = Path(out)
    if out.exists():
        raise ValueError(f"reference path already exists: {out}")
    payloads = {}
    for series in SERIES_IDS:
        url = FRED_URL.format(series=series)
        try:
            with urlopen(Request(url, headers={"User-Agent": "creforge public calibration"}),
                         timeout=30) as response:
                raw = response.read()
        except (OSError, URLError) as exc:
            raise ValueError(f"could not download {series} from {url}: {exc}") from exc
        _read_series(raw, series, quarters)
        payloads[series] = raw

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".creforge-reference-", dir=out.parent) as staging:
        root = Path(staging)
        entries = {}
        for series, raw in payloads.items():
            (root / f"{series}.csv").write_bytes(raw)
            entries[series] = {"url": FRED_URL.format(series=series),
                               "sha256": hashlib.sha256(raw).hexdigest()}
        manifest = {
            "schema_version": 1,
            "source": SOURCE,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "series": entries,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        root.rename(out)
    return load_public_reference(out)


def load_public_reference(path: str | Path) -> PublicReference:
    """Verify the saved CSV checksums and exact quarterly coverage before using them."""
    root = Path(path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("source") != SOURCE
            or not isinstance(manifest.get("series"), dict)
            or set(manifest["series"]) != set(SERIES_IDS)):
        raise ValueError("unsupported or incomplete public reference manifest")
    try:
        start = date.fromisoformat(manifest["period"]["start"])
        end = date.fromisoformat(manifest["period"]["end"])
        retrieved_at = manifest["retrieved_at"]
        if datetime.fromisoformat(retrieved_at).tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid public reference period or retrieval time") from exc
    quarters = _quarters(start, end)
    hashes, values = {}, {}
    for series in SERIES_IDS:
        entry = manifest["series"][series]
        if not isinstance(entry, dict):
            raise ValueError(f"invalid reference entry for {series}")
        if entry.get("url") != FRED_URL.format(series=series):
            raise ValueError(f"unexpected source URL for {series}")
        raw = (root / f"{series}.csv").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError(f"reference checksum mismatch for {series}")
        hashes[series] = digest
        values[series] = _read_series(raw, series, quarters)
    return PublicReference(start, end, retrieved_at, hashes, values)


@dataclass(frozen=True)
class PublicComparison:
    """One product and metric compared with a published reference series."""

    product: str
    metric: str
    series: str
    quarters: int
    reference_min: float
    reference_median: float
    reference_max: float
    generated: float
    target: tuple[float, float] | None
    median_gap_percentage_points: float
    within_observed_range: bool


@dataclass(frozen=True)
class PublicCalibrationReport:
    """Descriptive comparison; differences are not a statistical pass/fail test."""

    profile: str
    config_sha256: str
    reference_start: date
    reference_end: date
    retrieved_at: str
    source: str
    hashes: dict[str, str]
    comparisons: list[PublicComparison]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# Public aggregate calibration comparison", "",
            f"- Profile: `{self.profile}`; config SHA-256: `{self.config_sha256}`",
            f"- Reference: {self.source} (via FRED), {self.reference_start} to {self.reference_end}",
            f"- Retrieved: {self.retrieved_at}; CSV SHA-256 values are in the JSON report", "",
            "| Product | Metric | FRED series | Quarters | Public min–median–max | "
            "Generated | Profile target | Gap to median (pp) |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for row in self.comparisons:
            target = (f"{row.target[0]:.2%}–{row.target[1]:.2%}"
                      if row.target is not None else "—")
            lines.append(
                f"| {row.product} | {row.metric} | [{row.series}]"
                f"(https://fred.stlouisfed.org/series/{row.series}) | {row.quarters} | "
                f"{row.reference_min:.2%}–{row.reference_median:.2%}–{row.reference_max:.2%} | "
                f"{row.generated:.2%} | {target} | {row.median_gap_percentage_points:+.2f} |"
            )
        lines += [
            "", "The comparison is descriptive. Federal Reserve delinquency rates are weighted by "
            "loan balances; creforge counts account-months. Federal Reserve charge-offs are net of "
            "recoveries and balance-based; creforge counts written-off accounts. The other-consumer "
            "series is a bank-only proxy for both auto and personal loans. No public product series "
            "is used for overdraft or BNPL.", "",
        ]
        return "\n".join(lines)


def compare_public_reference(
    dataset: Dataset | DiskDataset | str | Path, reference: PublicReference | str | Path
) -> PublicCalibrationReport:
    """Compare generated portfolio metrics with a verified reference snapshot."""
    data = DiskDataset.open(dataset) if isinstance(dataset, (str, Path)) else dataset
    snapshot = (load_public_reference(reference)
                if isinstance(reference, (str, Path)) else reference)
    report = validate(data)
    if not report.integrity_ok:
        raise ValueError("dataset must pass integrity validation before public comparison")
    rows = []
    for product, metrics in SERIES.items():
        if product not in data.config.profile.products:
            continue
        for metric, series in metrics.items():
            vals = snapshot.values[series]
            low, median, high = min(vals), statistics.median(vals), max(vals)
            generated = report.metrics["products"][product][metric]
            target_model = data.config.profile.targets.get(product)
            target = getattr(target_model, metric) if target_model is not None else None
            rows.append(PublicComparison(
                product, metric, series, len(vals), low, median, high, generated, target,
                (generated - median) * 100.0, low <= generated <= high,
            ))
    return PublicCalibrationReport(
        data.config.profile.name, data.config.sha256(), snapshot.start, snapshot.end,
        snapshot.retrieved_at, SOURCE, snapshot.hashes, rows,
    )
