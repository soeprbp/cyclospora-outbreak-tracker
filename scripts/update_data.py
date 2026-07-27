#!/usr/bin/env python3
"""Fetch, validate, and atomically publish official Cyclospora snapshots."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "outbreak.json"
URLS = {
    "mdhhs": "https://www.michigan.gov/mdhhs/keep-mi-healthy/infectious-diseases/infectious-disease-outbreaks",
    "idph": "https://dph.illinois.gov/",
    "idoh": "https://secure.in.gov/health/idepd/diseases-and-conditions-resource-page/cyclosporiasis",
    "nysdoh": "https://www.health.ny.gov/diseases/communicable/cyclosporiasis/index",
    "widhs": "https://www.dhs.wisconsin.gov/outbreaks/index.htm",
    "cdc": "https://www.cdc.gov/cyclosporiasis/php/surveillance/index.html",
    "fda": "https://www.fda.gov/food/outbreaks-foodborne-illness/investigations-foodborne-illness-outbreaks",
    "nndss": "https://data.cdc.gov/resource/x9gk-5huc.json?$where=year%3D%272026%27%20and%20label%3D%27Cyclosporiasis%27&$limit=5000",
}
HEADERS = {
    # Michigan's CDN rejects generic script user agents.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def text_content(raw: str) -> str:
    raw = re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", raw, flags=re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def number(pattern: str, text: str, name: str) -> int:
    match = re.search(pattern, text, re.I)
    if not match:
        raise ValueError(f"missing {name}")
    return int(match.group(1).replace(",", ""))


def source_date(pattern: str, text: str, name: str) -> str:
    match = re.search(pattern, text, re.I)
    if not match:
        raise ValueError(f"missing {name} date")
    parsed = datetime.strptime(match.group(1).replace("Sept.", "Sep."), "%B %d, %Y")
    if parsed.date() > datetime.now(timezone.utc).date():
        raise ValueError(f"future {name} date")
    return parsed.date().isoformat()


def parse_mdhhs(raw: str) -> dict:
    text = text_content(raw)
    section = text[text.find("MDHHS is investigating an outbreak of cyclosporiasis") :]
    if not section:
        raise ValueError("missing MDHHS Cyclospora section")
    cases = number(r"Total Cases:\s*([\d,]+)", section, "MDHHS cases")
    hospitalized = number(r"(?:To date,|As of [A-Z][a-z]+ \d{1,2}, \d{4},)\s*([\d,]+)\s+reported cases indicated they had been hospitalized", section, "MDHHS hospitalizations")
    if cases < 100 or hospitalized > cases:
        raise ValueError("implausible MDHHS values")
    return {"official_as_of": source_date(r"Last updated:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", section, "MDHHS"), "cases": cases, "hospitalizations": hospitalized}


def numeric_source_date(pattern: str, text: str, name: str) -> str:
    match = re.search(pattern, text, re.I)
    if not match:
        raise ValueError(f"missing {name} date")
    parsed = datetime.strptime(match.group(1), "%m/%d/%y")
    if parsed.date() > datetime.now(timezone.utc).date():
        raise ValueError(f"future {name} date")
    return parsed.date().isoformat()


def parse_idph(raw: str) -> dict:
    text = text_content(raw)
    section = text[text.find("Cyclospora In Illinois:") :]
    if not section:
        raise ValueError("missing Illinois Cyclospora section")
    cases = number(r"([\d,]+)\s+Confirmed and Probable Cases", section, "IDPH cases")
    domestic = number(r"([\d,]+)\s+Domestically Acquired", section, "IDPH domestic cases")
    if cases < 1 or domestic > cases:
        raise ValueError("implausible IDPH values")
    return {
        "official_as_of": numeric_source_date(r"Data is as of\s+(\d{1,2}/\d{1,2}/\d{2})", section, "IDPH"),
        "cases": cases,
        "domestic_cases": domestic,
    }


def parse_idoh(raw: str) -> dict:
    text = text_content(raw)
    section = text[text.find("Indiana Case Counts") :]
    if not section:
        raise ValueError("missing Indiana Cyclospora section")
    cases = number(r"Total Cases:\s*([\d,]+)", section, "IDOH cases")
    match = re.search(r"Last updated:\s*([A-Z][a-z]+\s+\d{1,2})", section, re.I)
    if not match:
        raise ValueError("missing IDOH date")
    parsed = datetime.strptime(f"{match.group(1)}, {datetime.now(timezone.utc).year}", "%B %d, %Y")
    if cases < 1 or parsed.date() > datetime.now(timezone.utc).date():
        raise ValueError("implausible IDOH values")
    return {"official_as_of": parsed.date().isoformat(), "cases": cases}


def parse_nysdoh(raw: str) -> dict:
    text = text_content(raw)
    cases = number(r"Total Cases for 2026,\s*\d{1,2}/\d{1,2}/2026\s*-\s*\d{1,2}/\d{1,2}/2026:\s*([\d,]+)", text, "NYSDOH cases")
    period_end = re.search(r"Total Cases for 2026,\s*\d{1,2}/\d{1,2}/2026\s*-\s*(\d{1,2}/\d{1,2}/2026)", text, re.I)
    if not period_end:
        raise ValueError("missing NYSDOH period")
    parsed = datetime.strptime(period_end.group(1), "%m/%d/%Y")
    if cases < 1 or parsed.date() > datetime.now(timezone.utc).date():
        raise ValueError("implausible NYSDOH values")
    return {"official_as_of": parsed.date().isoformat(), "cases": cases}


def parse_widhs(raw: str) -> dict:
    text = text_content(raw)
    section = text[text.find("2026 Cyclospora season") :]
    if not section:
        raise ValueError("missing Wisconsin Cyclospora section")
    cases = number(r"there have been\s+([\d,]+)\s+cases of cyclosporiasis", section, "WIDHS cases")
    hospital_match = re.search(r"including\s+([\w,]+)\s+hospitalizations", section, re.I)
    if not hospital_match:
        raise ValueError("missing WIDHS hospitalizations")
    token = hospital_match.group(1).lower()
    words = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    hospitalized = int(token.replace(",", "")) if token.replace(",", "").isdigit() else words.get(token, -1)
    if cases < 1 or hospitalized > cases:
        raise ValueError("implausible WIDHS values")
    return {
        "official_as_of": source_date(r"Updated\s+([A-Z][a-z]+\s+\d{1,2},\s+2026)", section, "WIDHS"),
        "cases": cases,
        "hospitalizations": hospitalized,
    }


def parse_cdc(raw: str) -> dict:
    text = text_content(raw)
    start = text.find("2026 fast facts")
    end = text.find("Overview", start)
    section = text[start:end]
    if start >= 0 and section:
        result = {
            "official_as_of": source_date(r"As of\s+([A-Z][a-z]+ \d{1,2}, \d{4})", section, "CDC"),
            "cases": number(r"U\.S\. cases reported to CDC:\s*([\d,]+)", section, "CDC cases"),
            "hospitalizations": number(r"Hospitalizations:\s*([\d,]+)", section, "CDC hospitalizations"),
            "deaths": number(r"Deaths:\s*([\d,]+)", section, "CDC deaths"),
            "states": number(r"States reporting cases:\s*([\d,]+)", section, "CDC states"),
        }
    else:
        start = text.find("Cases acquired in the U.S.")
        end = text.find("Cases acquired outside the U.S.", start)
        section = text[start:end]
        if start < 0 or not section:
            raise ValueError("missing CDC domestic case section")
        result = {
            "official_as_of": source_date(r"May 1\s*[-–]\s*([A-Z][a-z]+ \d{1,2}, \d{4})", section, "CDC"),
            "cases": number(r"Cases\s+([\d,]+)", section, "CDC cases"),
            "hospitalizations": number(r"Hospitalizations\s+([\d,]+)", section, "CDC hospitalizations"),
            "deaths": number(r"Deaths\s+([\d,]+)", section, "CDC deaths"),
            "states": number(r"States reporting cases\s+([\d,]+)", section, "CDC states"),
        }
    if result["cases"] < 1 or result["hospitalizations"] > result["cases"] or result["deaths"] > result["cases"] or not 1 <= result["states"] <= 56:
        raise ValueError("implausible CDC values")
    return result


def parse_fda(raw: str) -> dict:
    text = text_content(raw)
    active = text[text.find("Active Investigations") : text.find("Closed Investigations")]
    refs = []
    for match in re.finditer(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{4})\s+Cyclospora\s+(.{0,180}?)(?=\d{1,2}/\d{1,2}/\d{4}|$)", active, re.I):
        body = match.group(3)
        count = re.search(r"(?:Not Yet Identified\s+)?([\d,]+)\s+Active\s+(Ongoing|Ended)", body, re.I)
        refs.append({"reference": match.group(2), "date_posted": datetime.strptime(match.group(1), "%m/%d/%Y").date().isoformat(), "cases": int(count.group(1).replace(",", "")) if count else None, "status": count.group(2).lower() if count else "active"})
    if not refs:
        raise ValueError("no active FDA Cyclospora investigations found")
    return {"official_as_of": max(x["date_posted"] for x in refs), "investigations": refs}


NNDSS_JURISDICTIONS = {
    "Connecticut":"CT", "Maine":"ME", "Massachusetts":"MA", "New Hampshire":"NH", "Rhode Island":"RI", "Vermont":"VT",
    "New Jersey":"NJ", "Pennsylvania":"PA", "Illinois":"IL", "Indiana":"IN", "Michigan":"MI", "Ohio":"OH", "Wisconsin":"WI",
    "Iowa":"IA", "Kansas":"KS", "Minnesota":"MN", "Missouri":"MO", "Nebraska":"NE", "North Dakota":"ND", "South Dakota":"SD",
    "Delaware":"DE", "District of Columbia":"DC", "Florida":"FL", "Georgia":"GA", "Maryland":"MD", "North Carolina":"NC", "South Carolina":"SC", "Virginia":"VA", "West Virginia":"WV",
    "Alabama":"AL", "Kentucky":"KY", "Mississippi":"MS", "Tennessee":"TN", "Arkansas":"AR", "Louisiana":"LA", "Oklahoma":"OK", "Texas":"TX",
    "Arizona":"AZ", "Colorado":"CO", "Idaho":"ID", "Montana":"MT", "Nevada":"NV", "New Mexico":"NM", "Utah":"UT", "Wyoming":"WY",
    "Alaska":"AK", "California":"CA", "Hawaii":"HI", "Oregon":"OR", "Washington":"WA",
}


def parse_nndss(raw: str) -> dict:
    """Parse the cumulative-YTD jurisdiction column from a NNDSS box table."""
    if raw.lstrip().startswith("["):
        records = json.loads(raw)
        latest_week = max(int(x["week"]) for x in records)
        records = [x for x in records if int(x["week"]) == latest_week]
        rows, ny_state, nyc, total = {}, None, None, None
        flags = {"-": 0, "N": "not-reportable", "U": "unavailable", "NC": "insufficient"}
        for record in records:
            name = record["states"]
            value = int(float(record["m3"])) if record.get("m3") else flags.get(record.get("m3_flag", ""))
            if name == "New York": ny_state = value
            elif name == "New York City": nyc = value
            elif name == "U.S. Residents": total = value
            elif name in NNDSS_JURISDICTIONS and value is not None:
                rows[NNDSS_JURISDICTIONS[name]] = {"cases": value} if isinstance(value, int) else {"status": value}
        if isinstance(ny_state, int) and isinstance(nyc, int):
            rows["NY"] = {"cases": ny_state + nyc, "components": {"state_excluding_nyc": ny_state, "nyc": nyc}}
        first_sunday = date(2026, 1, 1) + timedelta(days=(6 - date(2026, 1, 1).weekday()) % 7)
        official_as_of = (first_sunday + timedelta(days=6 + 7 * (latest_week - 1))).isoformat()
        if len(rows) < 45 or not isinstance(total, int):
            raise ValueError("incomplete NNDSS API response")
        return {"official_as_of": official_as_of, "reporting_period": "cumulative YTD 2026", "jurisdictions": rows, "us_residents_total": total}
    text = html.unescape(raw).replace("\r", "")
    date_match = re.search(r"week ending\s+(\d{4}-\d{2}-\d{2}|[A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text, re.I)
    if not date_match:
        raise ValueError("missing NNDSS week-ending date")
    token = date_match.group(1)
    official_as_of = token if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token) else datetime.strptime(token, "%B %d, %Y").date().isoformat()
    rows = {}
    ny_state = nyc = None
    for line in text.splitlines():
        cells = [x.strip() for x in line.strip(" |+").split("|")]
        if len(cells) < 2:
            continue
        name, value = cells[0], cells[-1].replace(",", "")
        parsed = int(value) if value.isdigit() else ({"-":0, "N":"not-reportable", "U":"unavailable", "NC":"insufficient"}.get(value.upper()))
        if parsed is None:
            continue
        if name == "New York State (excluding NYC)": ny_state = parsed
        elif name == "New York City": nyc = parsed
        elif name in NNDSS_JURISDICTIONS: rows[NNDSS_JURISDICTIONS[name]] = {"cases": parsed} if isinstance(parsed, int) else {"status": parsed}
    if isinstance(ny_state, int) and isinstance(nyc, int):
        rows["NY"] = {"cases": ny_state + nyc, "components": {"state_excluding_nyc": ny_state, "nyc": nyc}}
    total_match = re.search(r"U\.S\. residents total\s*\|\s*([\d,]+)", text, re.I)
    if len(rows) < 45 or not total_match:
        raise ValueError("incomplete NNDSS jurisdiction table")
    return {"official_as_of": official_as_of, "reporting_period": "cumulative YTD 2026", "jurisdictions": rows, "us_residents_total": int(total_match.group(1).replace(",", ""))}


PARSERS = {
    "mdhhs": parse_mdhhs,
    "idph": parse_idph,
    "idoh": parse_idoh,
    "nysdoh": parse_nysdoh,
    "widhs": parse_widhs,
    "cdc": parse_cdc,
    "fda": parse_fda,
    "nndss": parse_nndss,
}

STATE_SOURCES = {
    "mdhhs": ("MI", "Michigan MDHHS", "state outbreak reports; may include probable and confirmed cases"),
    "idph": ("IL", "Illinois IDPH", "confirmed and probable cases; includes domestic, travel-associated, and unknown travel"),
    "idoh": ("IN", "Indiana IDOH", "all reported cases since May 1, 2026"),
    "nysdoh": ("NY", "New York NYSDOH", "all reported 2026 cases, year to date"),
    "widhs": ("WI", "Wisconsin DHS", "all reported cases during the 2026 Cyclospora season"),
}


def build_state_data(sources: dict) -> dict:
    state_data = {}
    if "nndss" in sources:
        for code, value in sources["nndss"]["jurisdictions"].items():
            if "cases" in value:
                state_data[code] = {**value, "comparable_cases": value["cases"], "official_as_of": sources["nndss"]["official_as_of"], "source": "CDC NNDSS"}
    for source_key, (code, label, scope) in STATE_SOURCES.items():
        source = sources.get(source_key)
        current = state_data.get(code)
        if not source or (current and source["official_as_of"] < current["official_as_of"]):
            continue
        comparable = current.get("comparable_cases") if current else None
        state_data[code] = {
            **({"comparable_cases": comparable} if comparable is not None else {}),
            "cases": source["cases"],
            "official_as_of": source["official_as_of"],
            "source": label,
            "scope": scope,
        }
    return state_data


def fetch(url: str) -> str:
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:
            last_error = exc
            time.sleep(2**attempt)
    # Some agency CDNs reject urllib's TLS/client fingerprint while serving the
    # same public URL to curl. URLs come only from the trusted URLS registry.
    try:
        completed = subprocess.run(
            ["curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "30", url],
            check=True, capture_output=True, text=True, timeout=35,
        )
        return completed.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"fetch failed: {last_error}; curl fallback failed: {exc}") from exc


def load_previous() -> dict:
    return json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {"sources": {}}


def validate_against_previous(name: str, fresh: dict, previous: dict) -> None:
    old = previous.get("sources", {}).get(name, {}).get("cases")
    if old and fresh.get("cases", old) < old * 0.9:
        raise ValueError(f"{name} case count fell more than 10%")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, help="parse local <source>.html fixtures")
    args = parser.parse_args()
    previous = load_previous()
    sources, errors = {}, {}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for name, url in URLS.items():
        try:
            raw = (args.fixtures / f"{name}.html").read_text(encoding="utf-8") if args.fixtures else fetch(url)
            value = PARSERS[name](raw)
            validate_against_previous(name, value, previous)
            sources[name] = {"source_url": url, "fetched_at": now, "validation_status": "valid", **value}
        except Exception as exc:
            errors[name] = str(exc)
            if name in previous.get("sources", {}):
                sources[name] = previous["sources"][name]
                sources[name]["validation_status"] = "last-known-good"
    if not sources or (not args.fixtures and "mdhhs" not in sources and "cdc" not in sources):
        raise SystemExit(f"no usable primary data: {errors}")
    # Preserve the published document byte-for-byte when authoritative values and
    # source dates did not change. This prevents empty hourly commits.
    def substantive(value: dict) -> dict:
        return {k: v for k, v in value.items() if k not in {"fetched_at", "validation_status"}}
    state_data = build_state_data(sources)
    unchanged = (
        set(sources) == set(previous.get("sources", {}))
        and all(substantive(sources[k]) == substantive(previous["sources"][k]) for k in sources)
        and state_data == previous.get("state_data", {})
    )
    if unchanged:
        print(json.dumps({"updated": None, "unchanged": True, "errors": errors}))
        return 0
    document = {"schema_version": 2, "generated_at": now, "sources": sources, "state_data": state_data, "errors": errors}
    OUTPUT.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUTPUT.parent, prefix=".outbreak-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, OUTPUT)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    print(json.dumps({"updated": str(OUTPUT), "errors": errors}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
