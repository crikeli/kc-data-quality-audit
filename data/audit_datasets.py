"""
Run the rule-based data-quality checks (checks.py) against the curated
dataset list (target_datasets.py). Throttled to 1 request/second - same
politeness convention already used in seattle-house-styles/data/fetch_kc_photos.py -
and resumable: re-running skips datasets already present in
audit_results.parquet unless --force is passed.

For each target dataset this makes at most 3 requests: layer metadata
(?f=json), a bounded sample query (f=geojson, capped at 500 records or the
service's own maxRecordCount, whichever is smaller), and a HEAD request on
its Hub landing page. No full-dataset downloads.

Usage:
    python audit_datasets.py [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from checks import (
    check_crs_sanity,
    check_dead_link,
    check_family_schema_consistency,
    check_freshness,
    check_geometry_validity,
    check_metadata_completeness,
    check_null_rates,
)
from target_datasets import FAMILIES, resolve_targets

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(DATA_DIR, "audit_results.parquet")

REQUEST_DELAY_SECONDS = 1.0
SAMPLE_SIZE_CAP = 500
LAYER_SUFFIX_PATTERN = re.compile(r"/FeatureServer/(\d+)$")
SERVICE_ROOT_PATTERN = re.compile(r"/FeatureServer$")


def _json_default(obj):
    """checks.py mixes in numpy scalars (from gdf.total_bounds, pandas
    boolean reductions, etc.) - json.dumps doesn't know those, so coerce
    anything numpy-shaped to a native Python type."""
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def resolve_layer_url(rest_url: str) -> tuple[str, str]:
    """Returns (layer_url, note). Most catalog entries already point at a
    specific layer (.../FeatureServer/N). A few (e.g. a multi-tile service's
    overview entry) point at the bare service root - for those, fall back to
    that service's first sublayer and say so, rather than guessing."""
    if LAYER_SUFFIX_PATTERN.search(rest_url):
        return rest_url, ""

    if SERVICE_ROOT_PATTERN.search(rest_url):
        resp = requests.get(rest_url, params={"f": "json"}, timeout=30)
        resp.raise_for_status()
        layers = resp.json().get("layers", [])
        if not layers:
            raise ValueError(f"Service root {rest_url} has no layers.")
        first_id = layers[0]["id"]
        note = (
            f"Catalog entry points at the service root ({len(layers)} layers); "
            f"audited sublayer {first_id} as a representative sample, not the "
            "whole multi-tile family."
        )
        return f"{rest_url}/{first_id}", note

    raise ValueError(f"Unrecognized REST URL shape: {rest_url}")


def fetch_layer_meta(layer_url: str) -> dict:
    resp = requests.get(layer_url, params={"f": "json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_sample_gdf(layer_url: str, max_record_count: int | None) -> gpd.GeoDataFrame:
    record_count = min(SAMPLE_SIZE_CAP, max_record_count or SAMPLE_SIZE_CAP)
    resp = requests.get(
        f"{layer_url}/query",
        params={
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
            "resultRecordCount": record_count,
        },
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        raise ValueError(f"ArcGIS query error: {payload['error']}")
    features = payload.get("features", [])
    # Built directly from the parsed GeoJSON features rather than
    # gpd.read_file(BytesIO(...)) - pyogrio's GDAL GeoJSON driver rejected
    # in-memory bytes ("URL rejected: No host present") for some of these
    # endpoints, so parse the JSON ourselves and hand geopandas plain dicts.
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")


def audit_one(catalog_row: pd.Series) -> dict:
    title = catalog_row["title"]
    layer_url, resolution_note = resolve_layer_url(catalog_row["rest_url"])
    time.sleep(REQUEST_DELAY_SECONDS)

    layer_meta = fetch_layer_meta(layer_url)
    time.sleep(REQUEST_DELAY_SECONDS)

    field_names = sorted(f["name"] for f in layer_meta.get("fields", []))
    gdf = fetch_sample_gdf(layer_url, layer_meta.get("maxRecordCount"))
    time.sleep(REQUEST_DELAY_SECONDS)

    link_error = None
    status_code = None
    try:
        head_resp = requests.head(catalog_row["hub_url"], timeout=15, allow_redirects=True)
        status_code = head_resp.status_code
    except requests.RequestException as exc:
        link_error = str(exc)
    time.sleep(REQUEST_DELAY_SECONDS)

    checks = [
        check_metadata_completeness(catalog_row),
        check_freshness(catalog_row),
        check_geometry_validity(gdf),
        check_crs_sanity(gdf),
        check_null_rates(gdf),
        check_dead_link(catalog_row["hub_url"], status_code, link_error),
    ]
    issue_count = sum(1 for c in checks if not c["passed"])

    return {
        "title": title,
        "hub_url": catalog_row["hub_url"],
        "layer_url": layer_url,
        "geometry_type": layer_meta.get("geometryType"),
        "field_count": len(field_names),
        "field_names": field_names,
        "sample_record_count": len(gdf),
        "resolution_note": resolution_note,
        "checks_json": json.dumps(checks, default=_json_default),
        "issue_count": issue_count,
        "is_family_check": False,
    }


def run_family_checks(field_sets_by_title: dict[str, set]) -> list[dict]:
    rows = []
    for family_name, members in FAMILIES.items():
        field_sets = {t: field_sets_by_title[t] for t in members if t in field_sets_by_title}
        result = check_family_schema_consistency(family_name, field_sets)
        rows.append(
            {
                "title": f"FAMILY: {family_name}",
                "hub_url": None,
                "layer_url": None,
                "geometry_type": None,
                "field_count": None,
                "field_names": sorted(members),
                "sample_record_count": None,
                "resolution_note": "",
                "checks_json": json.dumps([result], default=_json_default),
                "issue_count": 0 if result["passed"] else 1,
                "is_family_check": True,
            }
        )
    return rows


def load_existing() -> pd.DataFrame:
    if os.path.exists(RESULTS_PATH):
        return pd.read_parquet(RESULTS_PATH)
    return pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-audit everything, ignoring checkpoints")
    args = parser.parse_args()

    targets = resolve_targets()
    existing = load_existing()
    done_titles = set(existing["title"]) if (not args.force and not existing.empty) else set()

    todo = targets[~targets["title"].isin(done_titles)]
    print(f"{len(targets)} target datasets, {len(done_titles)} already audited, {len(todo)} to run.")

    new_rows = []
    field_sets_by_title: dict[str, set] = {}
    for i, (_, row) in enumerate(todo.iterrows()):
        try:
            result = audit_one(row)
            field_sets_by_title[row["title"]] = set(result["field_names"])
            new_rows.append(result)
            print(f"  [{i + 1}/{len(todo)}] {row['title']}: {result['issue_count']} issue(s) flagged")
        except (requests.RequestException, ValueError) as exc:
            print(f"  [{i + 1}/{len(todo)}] {row['title']}: FAILED - {exc}")

    # Re-derive field sets for family members that were already audited in a
    # prior run, so the family check isn't penalized for a resumed run.
    if not existing.empty:
        for _, row in existing[~existing["is_family_check"]].iterrows():
            field_sets_by_title.setdefault(row["title"], set(row["field_names"]))

    family_rows = run_family_checks(field_sets_by_title)

    old_per_dataset = existing[~existing["is_family_check"]] if not existing.empty else pd.DataFrame()
    per_dataset = pd.concat([old_per_dataset, pd.DataFrame(new_rows)], ignore_index=True)
    if not per_dataset.empty:
        # New results win over stale ones for the same title (relevant on --force).
        per_dataset = per_dataset.drop_duplicates(subset="title", keep="last")

    # Family checks are cheap to recompute (no network calls) so they're
    # always rebuilt fresh from this run's field sets rather than merged.
    combined = pd.concat([per_dataset, pd.DataFrame(family_rows)], ignore_index=True)
    combined.to_parquet(RESULTS_PATH, index=False)
    print(f"\nWrote {RESULTS_PATH} ({len(combined)} rows).")


if __name__ == "__main__":
    main()
