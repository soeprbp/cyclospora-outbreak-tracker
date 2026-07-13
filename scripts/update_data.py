#!/usr/bin/env python3
"""Fetch, validate, and atomically publish official Cyclospora snapshots."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "outbreak.json"
URLS = {
    "mdhhs": "https://www.michigan.gov/mdhhs/keep-mi-healthy/infectious-diseases/infectious-disease-outbreaks",
    "cdc": "https://www.cdc.gov/cyclosporiasis/php/surveillance/index.html",
    "fda": "https://www.fda.gov/food/outbreaks-foodborne-illness/investigations-foodborne-illness-outbreaks",
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
    hospitalized = number(r"To date,\s*([\d,]+)\s+reported cases indicated they had been hospitalized", section, "MDHHS hospitalizations")
    if cases < 100 or hospitalized > cases:
        raise ValueError("implausible MDHHS values")
    return {"official_as_of": source_date(r"Last updated:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", section, "MDHHS"), "cases": cases, "hospitalizations": hospitalized}


def parse_cdc(raw: str) -> dict:
    text = text_content(raw)
    start = text.find("2026 fast facts")
    end = text.find("Overview", start)
    section = text[start:end]
    if start < 0 or not section:
        raise ValueError("missing CDC fast facts")
    result = {
        "official_as_of": source_date(r"As of\s+([A-Z][a-z]+ \d{1,2}, \d{4})", section, "CDC"),
        "cases": number(r"U\.S\. cases reported to CDC:\s*([\d,]+)", section, "CDC cases"),
        "hospitalizations": number(r"Hospitalizations:\s*([\d,]+)", section, "CDC hospitalizations"),
        "deaths": number(r"Deaths:\s*([\d,]+)", section, "CDC deaths"),
        "states": number(r"States reporting cases:\s*([\d,]+)", section, "CDC states"),
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


PARSERS = {"mdhhs": parse_mdhhs, "cdc": parse_cdc, "fda": parse_fda}


def fetch(url: str) -> str:
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed: {last_error}")


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
    unchanged = (
        set(sources) == set(previous.get("sources", {}))
        and all(substantive(sources[k]) == substantive(previous["sources"][k]) for k in sources)
    )
    if unchanged:
        print(json.dumps({"updated": None, "unchanged": True, "errors": errors}))
        return 0
    state_data = {}
    if "mdhhs" in sources:
        state_data["MI"] = {"cases": sources["mdhhs"]["cases"], "official_as_of": sources["mdhhs"]["official_as_of"], "source": "mdhhs"}
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
