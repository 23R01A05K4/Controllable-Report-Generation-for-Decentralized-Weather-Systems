"""Data cleaning, station analytics, anomaly detection, and grounded reporting."""

from __future__ import annotations

import csv
import io
import calendar
import os
import statistics
import tempfile
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


GHCN_COLUMNS = [
    "STATION",
    "DATE",
    "ELEMENT",
    "DATA_VALUE",
    "M_FLAG",
    "Q_FLAG",
    "S_FLAG",
    "OBS_TIME",
]
SUPPORTED_ELEMENTS = {"PRCP", "SNOW", "SNWD", "TMAX", "TMIN", "TAVG"}
ELEMENT_LABELS = {
    "PRCP": ("precipitation", "mm"),
    "SNOW": ("snowfall", "mm"),
    "SNWD": ("snow depth", "mm"),
    "TMAX": ("maximum temperature", "°C"),
    "TMIN": ("minimum temperature", "°C"),
    "TAVG": ("average temperature", "°C"),
}
PLAUSIBLE_RANGES = {
    "PRCP": (0, 5000),
    "SNOW": (0, 10000),
    "SNWD": (0, 10000),
    "TMAX": (-900, 700),
    "TMIN": (-900, 700),
    "TAVG": (-900, 700),
}
GHCN_DAILY_BASE_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily"
MAX_STATION_FILE_BYTES = 8 * 1024 * 1024
MAX_STATION_INVENTORY_BYTES = 15 * 1024 * 1024
PUBLIC_DATA_DIR = Path(__file__).resolve().parent / "data" / "public"


class DatasetError(ValueError):
    """Raised when an uploaded dataset cannot be analyzed."""


def _download_public_file(url: str, destination: Path, max_bytes: int) -> bytes:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "F32-Weather-Reporting-MVP/1.0"},
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise DatasetError("NOAA source file exceeds the local download size limit.")
            content = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DatasetError(f"Could not retrieve public NOAA data: {error}") from error
    if len(content) > max_bytes:
        raise DatasetError("NOAA source file exceeds the local download size limit.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary_file:
        temporary_file.write(content)
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, destination)
    return content


def _load_public_file(
    url: str,
    destination: Path,
    max_bytes: int,
    refresh: bool = False,
) -> bytes:
    if not refresh and destination.is_file() and destination.stat().st_size <= max_bytes:
        return destination.read_bytes()
    return _download_public_file(url, destination, max_bytes)


def load_noaa_station_data(
    station_id: str,
    start_year: int,
    end_year: int,
) -> tuple[str, str, dict[str, str]]:
    """Download/cache a bounded period from NOAA GHCN-Daily for one station."""
    station_id = station_id.strip().upper()
    if len(station_id) != 11 or not station_id.isalnum():
        raise DatasetError("Station ID must contain exactly 11 letters or digits.")
    current_year = date.today().year
    if (
        isinstance(start_year, bool)
        or isinstance(end_year, bool)
        or not isinstance(start_year, int)
        or not isinstance(end_year, int)
        or start_year < 1763
        or end_year > current_year
        or start_year > end_year
        or end_year - start_year > 4
    ):
        raise DatasetError("Choose a valid year range of no more than five years ending no later than this year.")

    station_path = PUBLIC_DATA_DIR / "stations" / f"{station_id}.dly"
    station_url = f"{GHCN_DAILY_BASE_URL}/all/{station_id}.dly"
    station_content = _load_public_file(
        station_url,
        station_path,
        MAX_STATION_FILE_BYTES,
        refresh=True,
    )
    inventory_path = PUBLIC_DATA_DIR / "ghcnd-stations.txt"
    inventory_url = f"{GHCN_DAILY_BASE_URL}/ghcnd-stations.txt"
    inventory_content = _load_public_file(
        inventory_url,
        inventory_path,
        MAX_STATION_INVENTORY_BYTES,
    )
    metadata = {}
    for line in inventory_content.decode("ascii", errors="replace").splitlines():
        if line[:11] == station_id:
            metadata = {
                "STATION": station_id,
                "LATITUDE": line[12:20].strip(),
                "LONGITUDE": line[21:30].strip(),
                "ELEVATION": line[31:37].strip(),
                "STATE": line[38:40].strip(),
                "NAME": line[41:71].strip(),
            }
            break
    if not metadata:
        raise DatasetError(f"NOAA station inventory does not contain station {station_id}.")

    observations = parse_ghcn_dly(station_content.decode("ascii", errors="replace"), start_year, end_year)
    if not observations:
        raise DatasetError(f"NOAA station {station_id} has no supported readings in the selected years.")
    stations_csv = io.StringIO()
    writer = csv.DictWriter(stations_csv, fieldnames=metadata.keys(), lineterminator="\n")
    writer.writeheader()
    writer.writerow(metadata)
    return observations, stations_csv.getvalue(), {
        "station_id": station_id,
        "station_name": metadata["NAME"],
        "start_year": start_year,
        "end_year": end_year,
        "source": station_url,
        "station_file": str(station_path.relative_to(PUBLIC_DATA_DIR.parent)).replace("\\", "/"),
        "inventory_file": str(inventory_path.relative_to(PUBLIC_DATA_DIR.parent)).replace("\\", "/"),
    }


def parse_ghcn_dly(content: str, start_year: int, end_year: int) -> str:
    """Convert fixed-width NOAA GHCN-Daily station records into the CSV schema."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(GHCN_COLUMNS)
    for line in content.splitlines():
        if len(line) < 21:
            continue
        station_id = line[:11].strip()
        try:
            year = int(line[11:15])
            month = int(line[15:17])
        except ValueError:
            continue
        element = line[17:21].strip()
        if (
            not station_id
            or year < start_year
            or year > end_year
            or element not in SUPPORTED_ELEMENTS
            or not 1 <= month <= 12
        ):
            continue
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            start = 21 + (day - 1) * 8
            value_text = line[start : start + 5]
            if len(value_text) != 5:
                continue
            try:
                value = int(value_text)
            except ValueError:
                continue
            if value == -9999:
                continue
            date_text = f"{year:04d}{month:02d}{day:02d}"
            writer.writerow(
                [
                    station_id,
                    date_text,
                    element,
                    value,
                    line[start + 5 : start + 6].strip(),
                    line[start + 6 : start + 7].strip(),
                    line[start + 7 : start + 8].strip(),
                    "",
                ]
            )
    return output.getvalue()


def _read_csv(content: str, expected_columns: list[str]) -> list[dict[str, str]]:
    if not content.strip():
        raise DatasetError("The observations CSV is empty.")
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        raise DatasetError("The observations CSV is empty.")
    first = [cell.strip().upper() for cell in rows[0]]
    if "STATION" in first and "DATE" in first:
        columns = first
        data_rows = rows[1:]
    else:
        columns = expected_columns
        data_rows = rows

    result = []
    for line_number, row in enumerate(data_rows, start=2 if columns == first else 1):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != len(columns):
            raise DatasetError(
                f"CSV row {line_number} has {len(row)} columns; expected {len(columns)}."
            )
        result.append(
            {column: value.strip() for column, value in zip(columns, row)}
        )
    return result


def _parse_station_metadata(content: str) -> dict[str, dict[str, str]]:
    if not content.strip():
        return {}
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise DatasetError("Station metadata CSV needs a header row.")
    fields = {name.strip().upper(): name for name in reader.fieldnames}
    if "STATION" not in fields:
        raise DatasetError("Station metadata CSV needs a STATION column.")
    metadata = {}
    for row in reader:
        station_id = (row.get(fields["STATION"]) or "").strip()
        if station_id:
            metadata[station_id] = {
                key: (row.get(original) or "").strip()
                for key, original in fields.items()
            }
    return metadata


def _format_value(element: str, raw_value: int) -> str:
    label, unit = ELEMENT_LABELS[element]
    value = raw_value / 10 if element in {"PRCP", "TMAX", "TMIN", "TAVG"} else raw_value
    precision = 1 if element in {"PRCP", "TMAX", "TMIN", "TAVG"} else 0
    return f"{value:.{precision}f} {unit}"


def _make_fact(
    fact_id: str,
    category: str,
    text: str,
    source_ids: list[str],
    **details: Any,
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "category": category,
        "text": text,
        "source_observation_ids": source_ids,
        **details,
    }


def analyze_dataset(observations_csv: str, station_csv: str = "") -> dict[str, Any]:
    """Parse GHCN-Daily CSV, quality-check observations, and derive traceable facts."""
    raw_rows = _read_csv(observations_csv.lstrip("\ufeff"), GHCN_COLUMNS)
    metadata = _parse_station_metadata(station_csv)
    if not raw_rows:
        raise DatasetError("The observations CSV contains no data rows.")

    valid = []
    rejected_quality = 0
    rejected_missing = 0
    rejected_range = 0
    ignored_elements = 0
    for index, row in enumerate(raw_rows, start=1):
        station_id = row.get("STATION", "").strip()
        element = row.get("ELEMENT", "").strip().upper()
        date_text = row.get("DATE", "").strip()
        if element not in SUPPORTED_ELEMENTS:
            ignored_elements += 1
            continue
        if row.get("Q_FLAG", "").strip():
            rejected_quality += 1
            continue
        try:
            value = int(row.get("DATA_VALUE", "").strip())
            observed_date = datetime.strptime(date_text, "%Y%m%d").date()
        except (TypeError, ValueError):
            rejected_missing += 1
            continue
        if value == -9999:
            rejected_missing += 1
            continue
        low, high = PLAUSIBLE_RANGES[element]
        if not station_id or not low <= value <= high:
            rejected_range += 1
            continue
        valid.append(
            {
                "id": f"O{index:06d}",
                "station_id": station_id,
                "date": observed_date.isoformat(),
                "element": element,
                "raw_value": value,
                "value": value / 10
                if element in {"PRCP", "TMAX", "TMIN", "TAVG"}
                else value,
                "unit": ELEMENT_LABELS[element][1],
            }
        )
    if not valid:
        raise DatasetError(
            "No valid supported observations found. Check the GHCN-Daily CSV format, "
            "quality flags, and values."
        )

    valid.sort(key=lambda item: (item["date"], item["station_id"], item["element"]))
    by_station_element: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_element: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in valid:
        by_station_element[(observation["station_id"], observation["element"])].append(
            observation
        )
        by_station[observation["station_id"]].append(observation)
        by_element[observation["element"]].append(observation)

    station_summaries = []
    facts = []
    next_fact_id = 1
    for (station_id, element), values in sorted(by_station_element.items()):
        numeric = [item["value"] for item in values]
        label, unit = ELEMENT_LABELS[element]
        station_name = metadata.get(station_id, {}).get("NAME") or station_id
        summary = {
            "station_id": station_id,
            "station_name": station_name,
            "element": element,
            "element_label": label,
            "unit": unit,
            "observation_count": len(values),
            "mean": round(statistics.mean(numeric), 2),
            "minimum": round(min(numeric), 2),
            "maximum": round(max(numeric), 2),
            "latest_date": values[-1]["date"],
            "latest_value": round(values[-1]["value"], 2),
        }
        station_summaries.append(summary)
        fact_id = f"F{next_fact_id:04d}"
        next_fact_id += 1
        facts.append(
            _make_fact(
                fact_id,
                "station",
                f"{station_name} recorded a mean {label} of "
                f"{_format_value(element, round(summary['mean'] * (10 if element in {'PRCP', 'TMAX', 'TMIN', 'TAVG'} else 1)))} "
                f"across {len(values)} valid observations.",
                [item["id"] for item in values],
                station_id=station_id,
                element=element,
                value=summary["mean"],
                unit=unit,
            )
        )

    anomalies = []
    for (station_id, element), values in sorted(by_station_element.items()):
        numbers = [item["value"] for item in values]
        if len(numbers) < 4:
            continue
        median = statistics.median(numbers)
        deviations = [abs(value - median) for value in numbers]
        mad = statistics.median(deviations)
        if mad > 0:
            scores = [0.6745 * (value - median) / mad for value in numbers]
        else:
            mean = statistics.mean(numbers)
            standard_deviation = statistics.pstdev(numbers)
            if standard_deviation == 0:
                continue
            scores = [(value - mean) / standard_deviation for value in numbers]
        station_name = metadata.get(station_id, {}).get("NAME") or station_id
        label, _ = ELEMENT_LABELS[element]
        for observation, score in zip(values, scores):
            if abs(score) < 2.5:
                continue
            anomaly = {
                "observation_id": observation["id"],
                "station_id": station_id,
                "station_name": station_name,
                "date": observation["date"],
                "element": element,
                "element_label": label,
                "value": observation["value"],
                "unit": observation["unit"],
                "score": round(score, 2),
                "direction": "higher" if score > 0 else "lower",
            }
            anomalies.append(anomaly)
            fact_id = f"F{next_fact_id:04d}"
            next_fact_id += 1
            facts.append(
                _make_fact(
                    fact_id,
                    "anomaly",
                    f"{station_name} had an unusually {anomaly['direction']} "
                    f"{label} reading of {_format_value(element, observation['raw_value'])} "
                    f"on {observation['date']} (robust score {anomaly['score']}).",
                    [observation["id"]],
                    station_id=station_id,
                    element=element,
                    value=observation["value"],
                    unit=observation["unit"],
                    date=observation["date"],
                    score=anomaly["score"],
                )
            )

    trends = []
    for element, values in sorted(by_element.items()):
        daily: dict[str, list[float]] = defaultdict(list)
        for observation in values:
            daily[observation["date"]].append(observation["value"])
        dates = sorted(daily)
        means = [statistics.mean(daily[date]) for date in dates]
        if len(dates) < 2:
            continue
        half = max(1, len(means) // 2)
        change = statistics.mean(means[-half:]) - statistics.mean(means[:half])
        label, unit = ELEMENT_LABELS[element]
        trend = {
            "element": element,
            "element_label": label,
            "unit": unit,
            "start_date": dates[0],
            "end_date": dates[-1],
            "change": round(change, 2),
            "direction": "increased" if change > 0 else "decreased" if change < 0 else "was stable",
            "observation_count": len(values),
        }
        trends.append(trend)
        fact_id = f"F{next_fact_id:04d}"
        next_fact_id += 1
        facts.append(
            _make_fact(
                fact_id,
                "trend",
                f"Across the selected stations, mean {label} {trend['direction']} "
                f"by {abs(trend['change']):g} {unit} between the first and second halves "
                f"of the observed period ({trend['start_date']} to {trend['end_date']}).",
                [item["id"] for item in values],
                element=element,
                value=trend["change"],
                unit=unit,
            )
        )

    all_dates = [item["date"] for item in valid]
    station_count = len(by_station)
    summary = {
        "observation_count": len(valid),
        "station_count": station_count,
        "date_start": min(all_dates),
        "date_end": max(all_dates),
        "elements": dict(sorted(Counter(item["element"] for item in valid).items())),
        "anomaly_count": len(anomalies),
    }
    overview_id = f"F{next_fact_id:04d}"
    facts.insert(
        0,
        _make_fact(
            overview_id,
            "overview",
            f"The dataset contains {len(valid)} valid observations from {station_count} "
            f"stations between {summary['date_start']} and {summary['date_end']}.",
            [item["id"] for item in valid],
        ),
    )
    return {
        "summary": summary,
        "quality": {
            "input_rows": len(raw_rows),
            "valid_rows": len(valid),
            "rejected_quality_flag": rejected_quality,
            "rejected_missing_or_invalid_date": rejected_missing,
            "rejected_out_of_range_or_missing_station": rejected_range,
            "ignored_unsupported_element": ignored_elements,
        },
        "station_summaries": station_summaries,
        "anomalies": anomalies,
        "trends": trends,
        "facts": facts,
        "observations": valid,
        "stations": [
            {
                "station_id": station_id,
                "station_name": metadata.get(station_id, {}).get("NAME") or station_id,
                "latitude": metadata.get(station_id, {}).get("LATITUDE", ""),
                "longitude": metadata.get(station_id, {}).get("LONGITUDE", ""),
            }
            for station_id in sorted(by_station)
        ],
    }


def generate_report(
    analysis: dict[str, Any],
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a controllable report exclusively from structured, provenance-linked facts."""
    controls = controls or {}
    audience = controls.get("audience", "executive")
    depth = controls.get("depth", "standard")
    tone = controls.get("tone", "neutral")
    length = controls.get("length", "standard")
    allowed = {
        "audience": {"analyst", "executive", "public"},
        "depth": {"concise", "standard", "detailed"},
        "tone": {"neutral", "formal", "plain"},
        "length": {"short", "standard", "detailed"},
    }
    defaults = {"audience": "executive", "depth": "standard", "tone": "neutral", "length": "standard"}
    for key, values in allowed.items():
        value = controls.get(key, defaults[key])
        if not isinstance(value, str) or value not in values:
            raise DatasetError(f"Unsupported report control: {key}.")

    topics = controls.get("topics", ["overview", "anomalies", "trends", "stations"])
    if not isinstance(topics, list) or any(
        not isinstance(topic, str)
        or topic not in {"overview", "anomalies", "trends", "stations"}
        for topic in topics
    ):
        raise DatasetError("Topics must be selected from overview, anomalies, trends, and stations.")
    if not topics:
        raise DatasetError("Select at least one report topic.")

    facts_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in analysis["facts"]:
        facts_by_category[fact["category"]].append(fact)
    chosen = []
    topic_category = {
        "overview": "overview",
        "anomalies": "anomaly",
        "trends": "trend",
        "stations": "station",
    }
    for topic in topics:
        chosen.extend(facts_by_category[topic_category[topic]])

    limits = {"short": 4, "standard": 12, "detailed": 100}
    report_facts = chosen[: limits[length]]
    if audience == "public" or tone == "plain":
        intro = "Here is a plain-language summary of the weather observations provided."
    elif tone == "formal":
        intro = "This report presents quality-checked station observations and their supporting evidence."
    elif audience == "analyst" or depth == "detailed":
        intro = "This report summarizes quality-checked station observations and their supporting evidence."
    elif depth == "concise":
        intro = "This concise summary highlights the main observed station conditions."
    else:
        intro = "This executive summary highlights observed station conditions and notable patterns."

    checks = [
        {
            "fact_id": fact["id"],
            "status": "verified"
            if fact["text"] and fact["source_observation_ids"]
            and all(source_id in {item["id"] for item in analysis["observations"]} for source_id in fact["source_observation_ids"])
            else "failed",
            "source_observation_ids": fact["source_observation_ids"],
        }
        for fact in report_facts
    ]
    verified = all(check["status"] == "verified" for check in checks)
    report_lines = [
        "# Weather Station Executive Summary",
        "",
        intro,
        "",
    ]
    for fact, check in zip(report_facts, checks):
        report_lines.append(f"- {fact['text']} [{fact['id']}]")
        report_lines.append(
            f"  - Provenance: {', '.join(check['source_observation_ids'])}"
        )
    report_lines.extend(
        [
            "",
            "---",
            f"Audience: {audience.title()} | Depth: {depth.title()} | Tone: {tone.title()} | Length: {length.title()}",
            f"Verification: {'All included statements are linked to valid source observations.' if verified else 'One or more statements could not be verified.'}",
            "Scope: descriptive analysis of supplied observations only; this is not a forecast.",
        ]
    )
    return {
        "title": "Weather Station Executive Summary",
        "intro": intro,
        "audience": audience,
        "depth": depth,
        "tone": tone,
        "length": length,
        "topics": topics,
        "claims": report_facts,
        "verification": {"verified": verified, "checks": checks},
        "markdown": "\n".join(report_lines),
  }
