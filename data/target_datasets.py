"""
Curated subset of King County's GIS catalog to audit - ~15-20 datasets
chosen to span geometry types (polygon/line/point), publishing patterns
(single layer vs. a tiled family), and dataset "weight" (a well-known
control case, hazard layers, infrastructure points/lines).

Titles are matched exactly against data/catalog.parquet (built by
fetch_catalog.py). Exact strings as published in the catalog on 2026-09-16 -
if King County renames a dataset, resolve_targets() will raise rather than
silently skip it, so a rename gets noticed instead of shrinking the audit.
"""

import os

import pandas as pd

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(DATA_DIR, "catalog.parquet")

TARGET_TITLES = [
    # Control case - already used and verified in seattle-house-styles.
    "Parcels with Address Property and Ownership Information (Public)",
    # Land use / zoning - polygon
    "Zoning for Unincorporated King County",
    "Comprehensive Plan Land Use",
    # Hazard layers - polygon
    "FEMA High Risk Flood Area (100-year Floodplain)",
    "Potential landslide hazard areas 50-foot buffer 2016",
    "Wetlands defined from Critical Area Ordinance surveys in King County",
    # Hydrology / infrastructure - line
    "Sensitive Area Ordinance Streams",
    "Rivers and Streams in King County",
    "Roadlog Centerlines owned and maintained by King County Road Services",
    # Points
    "King County Metro Stops",
    "Fire Station Locations in King County",
    # Environmental / planning - polygon
    "Tree Point Index",
    "Watershed boundaries derived from terrain data - King County only",
    "LCI Opportunity Areas",
    # Tree canopy family - one overview + a few tiles, to test cross-dataset
    # schema consistency within a family of related layers.
    "Tree Canopy 2021 - Tree Points",
    "t20r11tree point",
    "t20r10tree point",
    "t20r09tree point",
]

# Datasets that belong to the same "family" for the cross-dataset field-name
# consistency check (see checks.py: check_family_schema_consistency).
FAMILIES = {
    "tree_canopy_tiles": [
        "Tree Canopy 2021 - Tree Points",
        "t20r11tree point",
        "t20r10tree point",
        "t20r09tree point",
    ],
}


def resolve_targets() -> pd.DataFrame:
    """Look up TARGET_TITLES in the fetched catalog. Raises if any title
    isn't found, so a King County rename surfaces immediately instead of
    quietly shrinking the audited set."""
    catalog = pd.read_parquet(CATALOG_PATH)
    catalog_by_title = catalog.set_index("title")

    missing = [t for t in TARGET_TITLES if t not in catalog_by_title.index]
    if missing:
        raise ValueError(
            "These target dataset titles were not found in catalog.parquet "
            f"(renamed or removed upstream?): {missing}\n"
            "Re-run fetch_catalog.py, then check the Hub site for the new title."
        )

    return catalog_by_title.loc[TARGET_TITLES].reset_index()
