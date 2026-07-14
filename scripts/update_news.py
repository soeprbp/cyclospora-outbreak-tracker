#!/usr/bin/env python3
"""Discover, validate, and atomically publish recent Cyclospora news."""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "news.json"
OUTBREAK = ROOT / "data" / "outbreak.json"
SEARCH_URL = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
    "q": "Cyclospora outbreak when:14d", "hl": "en-US", "gl": "US", "ceid": "US:en"
})
HEADERS = {"User-Agent": "Mozilla/5.0 CyclosporaTracker/1.0", "Accept": "application/rss+xml,application/xml,text/xml"}
OFFICIAL_HOSTS = {"cdc.gov", "www.cdc.gov", "stacks.cdc.gov", "fda.gov", "www.fda.gov"}
MAJOR_HOSTS = {
    "apnews.com", "reuters.com", "www.reuters.com", "washingtonpost.com",
    "www.washingtonpost.com", "axios.com", "www.axios.com", "cbsnews.com",
    "www.cbsnews.com", "npr.org", "www.npr.org", "abcnews.go.com",
    "nbcnews.com", "www.nbcnews.com", "usatoday.com", "www.usatoday.com",
}
AGGREGATOR_HOSTS = {"news.google.com"}
MAX_AGE_DAYS = 45


def clean(value: str, limit: int) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return value[:limit].rstrip()


def canonical_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme != "https":
        raise ValueError("news URL must use HTTPS")
    return urllib.parse.urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/") or "/", "", ""))


def allowed_url(value: str) -> bool:
    host = urllib.parse.urlsplit(value).hostname or ""
    return host in OFFICIAL_HOSTS or host in MAJOR_HOSTS or host.endswith(".gov")


def stable_id(source: str, url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    digest = hashlib.sha256(canonical_url(url).encode()).hexdigest()[:12]
    return f"{slug}-{digest}"


def validate(item: dict, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    result = dict(item)
    result["url"] = canonical_url(result.get("url", ""))
    publisher_url = result.get("publisher_url")
    if publisher_url:
        result["publisher_url"] = canonical_url(publisher_url)
    trust_url = result.get("publisher_url") or result["url"]
    link_host = urllib.parse.urlsplit(result["url"]).hostname or ""
    if (
        not allowed_url(trust_url)
        or (link_host in AGGREGATOR_HOSTS and not publisher_url)
        or (link_host not in AGGREGATOR_HOSTS and not allowed_url(result["url"]))
    ):
        raise ValueError("news host is not allowlisted")
    result["title"] = clean(result.get("title", ""), 180)
    result["summary"] = clean(result.get("summary", ""), 500)
    result["source"] = clean(result.get("source", ""), 80)
    if not result["title"] or not result["summary"] or not result["source"]:
        raise ValueError("missing news text")
    if "cyclosp" not in (result["title"] + " " + result["summary"]).lower():
        raise ValueError("not Cyclospora-relevant")
    published = date.fromisoformat(result["published_at"])
    if published > today + timedelta(days=1) or published < today - timedelta(days=MAX_AGE_DAYS):
        raise ValueError("news date outside accepted window")
    result["id"] = result.get("id") or stable_id(result["source"], result["url"])
    trust_host = urllib.parse.urlsplit(trust_url).hostname or ""
    result["kind"] = "official" if trust_host.endswith(".gov") else "major-media"
    return result


def parse_rss(raw: str) -> list[dict]:
    root = ET.fromstring(raw)
    items = []
    for node in root.findall(".//item"):
        link = clean(node.findtext("link") or "", 1000)
        title = clean(node.findtext("title") or "", 180)
        description = clean(node.findtext("description") or "", 500)
        source_node = node.find("source")
        source = clean(source_node.text if source_node is not None and source_node.text else urllib.parse.urlsplit(link).hostname or "", 80)
        publisher_url = source_node.get("url") if source_node is not None else None
        if publisher_url and urllib.parse.urlsplit(link).hostname in AGGREGATOR_HOSTS:
            suffix = f" - {source}"
            if title.casefold().endswith(suffix.casefold()):
                title = title[:-len(suffix)].rstrip()
            description = f"Recent Cyclospora coverage from {source}. Open the article for the full report."
        if not description or description.casefold().startswith(title.casefold()) or description.casefold() == source.casefold():
            description = f"Recent Cyclospora coverage from {source}. Open the article for the full report."
        try:
            published = email.utils.parsedate_to_datetime(node.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            continue
        items.append({"title": title, "summary": description, "url": link, "publisher_url": publisher_url, "source": source, "published_at": published})
    return items


def outbreak_items(snapshot: dict) -> list[dict]:
    items = []
    sources = snapshot.get("sources", {})
    cdc = sources.get("cdc")
    if cdc:
        items.append({"id": "cdc-surveillance", "published_at": cdc["official_as_of"], "source": "CDC", "title": f"CDC reports {cdc['cases']:,} domestically acquired Cyclospora cases", "summary": f"CDC's national surveillance snapshot reports {cdc['hospitalizations']:,} hospitalizations, {cdc['deaths']:,} deaths, and cases across {cdc['states']} states.", "url": cdc["source_url"]})
    fda = sources.get("fda")
    if fda:
        for investigation in fda.get("investigations", []):
            ref = investigation["reference"]
            count = investigation.get("cases")
            count_text = f" reports {count:,} cases and" if isinstance(count, int) else ""
            items.append({"id": f"fda-core-{ref}", "published_at": investigation["date_posted"], "source": "FDA", "title": f"FDA opens Cyclospora investigation {ref}", "summary": f"FDA's CORE table{count_text} lists investigation {ref} as {investigation.get('status', 'active')}; the agency table should be checked for current traceback and product details.", "url": fda["source_url"]})
    nndss = sources.get("nndss")
    if nndss:
        items.append({"id": "cdc-nndss", "published_at": nndss["official_as_of"], "source": "CDC NNDSS", "title": f"NNDSS table reports {nndss['us_residents_total']:,} U.S. Cyclospora cases", "summary": "The provisional cumulative jurisdiction table supplies comparable state values; reporting availability varies by jurisdiction.", "url": nndss["source_url"]})
    return items


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def load(path: Path, default: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rss-fixture", type=Path)
    parser.add_argument("--today", type=date.fromisoformat)
    args = parser.parse_args()
    today = args.today or datetime.now(timezone.utc).date()
    previous = load(OUTPUT, {"items": []})
    candidates = outbreak_items(load(OUTBREAK, {"sources": {}}))
    errors = []
    try:
        raw = args.rss_fixture.read_text(encoding="utf-8") if args.rss_fixture else fetch(SEARCH_URL)
        candidates.extend(parse_rss(raw))
    except Exception as exc:
        errors.append(f"discovery: {exc}")
    valid = []
    rejected = 0
    for candidate in candidates:
        try:
            valid.append(validate(candidate, today))
        except Exception as exc:
            rejected += 1
    # Stable IDs update in place; canonical URL prevents syndicated duplicates.
    merged = {item["id"]: item for item in previous.get("items", [])}
    urls = {item.get("url"): key for key, item in merged.items() if not re.match(r"^(cdc-|fda-core-)", key)}
    for item in valid:
        existing = merged.get(item["id"])
        if existing and existing.get("published_at", "") > item["published_at"]:
            continue
        # Canonical URL is a secondary dedupe only for discovery items. Several
        # semantic events (notably FDA CORE references) intentionally share a URL.
        duplicate = urls.get(item["url"]) if item["id"] == stable_id(item["source"], item["url"]) else None
        if duplicate and duplicate != item["id"]:
            merged.pop(duplicate, None)
        merged[item["id"]] = item
        if item["id"] == stable_id(item["source"], item["url"]):
            urls[item["url"]] = item["id"]
    cutoff = today - timedelta(days=MAX_AGE_DAYS)
    ranked = sorted((x for x in merged.values() if date.fromisoformat(x["published_at"]) >= cutoff), key=lambda x: (x["published_at"], x["kind"] == "official"), reverse=True)
    items, media_sources = [], set()
    for item in ranked:
        source_key = item["source"].casefold()
        if item["kind"] == "major-media" and source_key in media_sources:
            continue
        items.append(item)
        if item["kind"] == "major-media":
            media_sources.add(source_key)
        if len(items) == 12:
            break
    if not items:
        raise SystemExit("no valid news and no last-known-good items")
    semantic = {"schema_version": 1, "items": items}
    if {k: previous.get(k) for k in semantic} == semantic:
        print(json.dumps({"updated": None, "unchanged": True, "errors": errors}))
        return 0
    document = {**semantic, "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "errors": errors, "rejected_candidates": rejected}
    OUTPUT.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUTPUT.parent, prefix=".news-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, OUTPUT)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    print(json.dumps({"updated": str(OUTPUT), "items": len(items), "errors": errors}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
