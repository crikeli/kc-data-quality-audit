"""
Rule-based, deterministic data-quality checks. No LLM calls, no black box -
every check here is a small, independently readable function you can point
to and explain.

Each check_* function returns a dict:
    {"check": "<name>", "passed": bool, "detail": "<human-readable finding>"}
so audit_datasets.py can collect them uniformly and app.py can render them
without knowing anything about the underlying logic.

Thresholds below (freshness age, null-rate cutoff, King County bbox) are
deliberately simple and are documented as such in the app's About tab -
they are heuristics for surfacing candidates worth a human look, not a
certified data-quality standard.
"""

from __future__ import annotations

from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd

FRESHNESS_THRESHOLD_DAYS = 365 * 3
NULL_RATE_FLAG_THRESHOLD = 0.20
# Rough King County extent in lon/lat (WGS84), used as a sanity bound - not
# an exact boundary. See About tab for why this is a proxy, not a precise
# spatial containment check.
KC_BBOX = (-122.6, 47.0, -121.0, 47.9)


def check_metadata_completeness(catalog_row: pd.Series) -> dict:
    missing = []
    if not (catalog_row.get("description") or "").strip():
        missing.append("description")
    if not (catalog_row.get("license") or "").strip():
        missing.append("license")
    if not (catalog_row.get("contact_email") or "").strip():
        missing.append("contact email")
    keywords = catalog_row.get("keywords")
    if keywords is None or len(keywords) == 0:
        missing.append("keyword tags")

    passed = len(missing) == 0
    detail = (
        "All core metadata fields present."
        if passed
        else f"Missing: {', '.join(missing)}."
    )
    return {"check": "metadata_completeness", "passed": passed, "detail": detail}


def check_freshness(catalog_row: pd.Series) -> dict:
    modified_raw = catalog_row.get("modified")
    if not modified_raw:
        return {
            "check": "freshness",
            "passed": False,
            "detail": "No 'modified' timestamp published.",
        }

    modified = pd.to_datetime(modified_raw, utc=True, errors="coerce")
    if pd.isna(modified):
        return {
            "check": "freshness",
            "passed": False,
            "detail": f"Unparseable 'modified' timestamp: {modified_raw!r}.",
        }

    age_days = (datetime.now(timezone.utc) - modified.to_pydatetime()).days
    passed = age_days <= FRESHNESS_THRESHOLD_DAYS
    detail = (
        f"Last modified {age_days} days ago "
        f"({'within' if passed else 'beyond'} the {FRESHNESS_THRESHOLD_DAYS}-day "
        "heuristic threshold used here)."
    )
    return {"check": "freshness", "passed": passed, "detail": detail}


def check_geometry_validity(gdf: gpd.GeoDataFrame) -> dict:
    if gdf is None or len(gdf) == 0:
        return {
            "check": "geometry_validity",
            "passed": False,
            "detail": "No sample records returned to check.",
        }

    total = len(gdf)
    null_geom = gdf.geometry.isna().sum()
    non_null = gdf[gdf.geometry.notna()]
    invalid_geom = (~non_null.geometry.is_valid).sum() if len(non_null) else 0

    null_pct = null_geom / total
    invalid_pct = (invalid_geom / len(non_null)) if len(non_null) else 0.0
    passed = null_pct == 0 and invalid_pct == 0
    detail = (
        f"{null_geom}/{total} sampled records have null geometry "
        f"({null_pct:.0%}); {invalid_geom}/{max(len(non_null), 1)} of the "
        f"remaining have invalid (e.g. self-intersecting) geometry "
        f"({invalid_pct:.0%})."
    )
    return {"check": "geometry_validity", "passed": passed, "detail": detail}


def check_crs_sanity(gdf: gpd.GeoDataFrame) -> dict:
    if gdf is None or len(gdf) == 0 or gdf.geometry.notna().sum() == 0:
        return {
            "check": "crs_sanity",
            "passed": False,
            "detail": "No geometry available to check extent against.",
        }

    minx, miny, maxx, maxy = gdf.total_bounds
    kc_minx, kc_miny, kc_maxx, kc_maxy = KC_BBOX
    within = (
        minx >= kc_minx - 0.5
        and maxx <= kc_maxx + 0.5
        and miny >= kc_miny - 0.5
        and maxy <= kc_maxy + 0.5
    )
    detail = (
        f"Sample extent [{minx:.3f}, {miny:.3f}, {maxx:.3f}, {maxy:.3f}] "
        f"{'falls within' if within else 'falls OUTSIDE'} the expected King "
        "County lon/lat range (a proxy sanity check, not exact containment)."
    )
    return {"check": "crs_sanity", "passed": within, "detail": detail}


def check_null_rates(gdf: gpd.GeoDataFrame) -> dict:
    if gdf is None or len(gdf) == 0:
        return {
            "check": "attribute_null_rates",
            "passed": False,
            "detail": "No sample records returned to check.",
        }

    attr_cols = [c for c in gdf.columns if c != gdf.geometry.name]
    if not attr_cols:
        return {
            "check": "attribute_null_rates",
            "passed": True,
            "detail": "No attribute fields beyond geometry.",
        }

    null_rates = gdf[attr_cols].isna().mean()
    flagged = null_rates[null_rates > NULL_RATE_FLAG_THRESHOLD].sort_values(ascending=False)
    passed = flagged.empty
    if passed:
        detail = f"No attribute field exceeds the {NULL_RATE_FLAG_THRESHOLD:.0%} null-rate threshold."
    else:
        worst = ", ".join(f"{col} ({rate:.0%})" for col, rate in flagged.head(5).items())
        detail = f"Fields above the {NULL_RATE_FLAG_THRESHOLD:.0%} null-rate threshold: {worst}."
    return {"check": "attribute_null_rates", "passed": passed, "detail": detail}


def check_dead_link(url: str | None, status_code: int | None, error: str | None) -> dict:
    if not url:
        return {"check": "hub_link_alive", "passed": False, "detail": "No Hub URL published."}
    if error:
        return {
            "check": "hub_link_alive",
            "passed": False,
            "detail": f"Request to {url} failed: {error}",
        }
    passed = status_code is not None and status_code < 400
    detail = f"{url} returned HTTP {status_code}."
    return {"check": "hub_link_alive", "passed": passed, "detail": detail}


def check_family_schema_consistency(family_name: str, field_sets: dict[str, set[str]]) -> dict:
    """field_sets: {dataset_title: set(field_names)} for one family (see
    target_datasets.FAMILIES). Run once per family, not once per dataset."""
    if len(field_sets) < 2:
        return {
            "check": f"family_schema_consistency[{family_name}]",
            "passed": True,
            "detail": "Fewer than 2 members resolved; nothing to compare.",
        }

    titles = list(field_sets.keys())
    common = set.intersection(*field_sets.values())
    mismatches = []
    for title, fields in field_sets.items():
        extra = fields - common
        if extra:
            mismatches.append(f"{title} has extra/differently-named fields: {sorted(extra)}")

    passed = len(mismatches) == 0
    detail = (
        f"All {len(titles)} members of '{family_name}' share the same field names."
        if passed
        else " | ".join(mismatches)
    )
    return {"check": f"family_schema_consistency[{family_name}]", "passed": passed, "detail": detail}
