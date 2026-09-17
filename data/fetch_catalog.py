"""
Pull King County's GIS Open Data catalog (every published dataset's metadata)
from their standard DCAT-US 1.1 feed - one lightweight request, no auth,
no per-dataset traffic. This is the catalog we pick a curated audit subset
from in target_datasets.py; it is NOT itself the data-quality audit.

Endpoint confirmed 2026-09-16: as of that date the feed returns the full
catalog (531 datasets) in a single response with no pagination parameter -
if King County's portal grows enough that this stops being true, re-check
the feed's own `describedBy`/`conformsTo` schema link for a page parameter
before assuming this script is still complete.

Usage:
    python fetch_catalog.py
"""

import html
import os
import re

import pandas as pd
import requests

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(DATA_DIR, "catalog.parquet")
FEED_URL = "https://gis-kingcounty.opendata.arcgis.com/api/feed/dcat-us/1.1.json"

TAG_PATTERN = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(TAG_PATTERN.sub("", text)).strip()


def find_rest_url(distribution: list[dict]) -> str | None:
    for dist in distribution or []:
        fmt = (dist.get("format") or "").lower()
        if "rest api" in fmt:
            return dist.get("accessURL") or dist.get("downloadURL")
    return None


def parse_dataset(raw: dict) -> dict:
    spatial = raw.get("spatial")
    # Usually an envelope dict ({"coordinates": [...], "type": "envelope"}),
    # but some entries publish spatial as a plain bbox/WKT string instead.
    coords = spatial.get("coordinates") if isinstance(spatial, dict) else None
    contact = raw.get("contactPoint") or {}
    return {
        "identifier": raw.get("identifier"),
        "title": raw.get("title"),
        "description": strip_html(raw.get("description")),
        "keywords": raw.get("keyword") or [],
        "issued": raw.get("issued"),
        "modified": raw.get("modified"),
        "publisher": (raw.get("publisher") or {}).get("name"),
        "contact_email": contact.get("hasEmail"),
        "license": strip_html(raw.get("license")),
        "hub_url": raw.get("landingPage"),
        "rest_url": find_rest_url(raw.get("distribution")),
        "bbox": coords if isinstance(coords, list) else None,
    }


def main() -> None:
    resp = requests.get(FEED_URL, timeout=60)
    resp.raise_for_status()
    feed = resp.json()
    datasets = feed.get("dataset", [])

    rows = [parse_dataset(ds) for ds in datasets]
    df = pd.DataFrame(rows)
    df.to_parquet(CATALOG_PATH, index=False)

    with_rest = df["rest_url"].notna().sum()
    print(f"Fetched {len(df)} datasets from the King County GIS catalog.")
    print(f"{with_rest}/{len(df)} have a REST API endpoint (audit-able).")
    print(f"Wrote {CATALOG_PATH}")


if __name__ == "__main__":
    main()
