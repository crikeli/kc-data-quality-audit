# King County GIS Data Quality Audit

A rule-based data-quality audit of a curated subset of King County's
published GIS layers (`gis-kingcounty.opendata.arcgis.com`), presented as a
Streamlit dashboard. Built as a portfolio piece specifically aimed at King
County's own GIS operations - not a map of King County's data, but an audit
of it: staleness, geometry validity, metadata completeness, and schema
consistency across related dataset families.

## Status: working local prototype

Runs entirely against King County's public, unauthenticated APIs - no
permission requirements, unlike the King County Assessor photo pipeline
used in a sibling project (`seattle-house-styles`). Currently audits an
18-dataset curated subset (see "Known limitations" below for why not the
full portal).

## How it works

**Finding the datasets.** King County's Open Data portal publishes a
standard [DCAT-US 1.1 catalog feed](https://gis-kingcounty.opendata.arcgis.com/api/feed/dcat-us/1.1.json)
listing every dataset's metadata - title, description, last-modified date,
license, contact, and a link to its ArcGIS FeatureServer REST endpoint
(`data/fetch_catalog.py`). As of this writing that feed returns all 531
datasets in one response.

**Picking what to audit.** `data/target_datasets.py` curates ~18 datasets
from that catalog, chosen to span geometry types (polygon/line/point) and
publishing patterns - including a known-good control case (the parcels
dataset also used in `seattle-house-styles`) and a *family* of related
datasets (four of King County's tiled 2021 tree-canopy point layers), to
test whether related layers actually share a consistent schema.

**Running the checks.** `data/audit_datasets.py` hits each target
dataset's FeatureServer endpoint - one metadata request, one bounded sample
query (up to 500 records), one link check - throttled to 1 request/second.
`data/checks.py` runs six deterministic checks per dataset, no LLM
involved:
- Metadata completeness (description, license, contact, tags)
- Freshness (last-edit age against a fixed threshold)
- Geometry validity (null / self-intersecting geometries, via Shapely)
- CRS / extent sanity (sample bounds vs. an expected King County range)
- Attribute null rates (fields exceeding a null-rate threshold)
- Hub link liveness

...plus one cross-dataset check run once per family: do related layers
actually share the same field names? (They didn't, in the tree-canopy
family - see Known limitations.)

**Presenting it.** `app.py` (Streamlit) has three tabs: a portal-wide
**Scorecard** (ranked table + issue-frequency chart), a **Dataset Detail**
drill-down with links back to the real King County Hub page for
independent verification, and an **About** tab with the full methodology.

## Known limitations

- This audits a curated 18-dataset subset, not King County's full ~530+
  dataset catalog - a production version would need the checks engine
  hardened against far more schema variety, plus a review workflow, before
  running unattended at full scale.
- Checks run on a bounded sample (500 records or the layer's own
  `maxRecordCount`), not the full dataset.
- Freshness and null-rate thresholds are simple fixed heuristics (see
  `data/checks.py`), not a certified data-quality standard - they surface
  candidates worth a human look.
- The CRS/extent check is a rough King County bounding-box proxy, not exact
  spatial containment.
- One real finding worth calling out: auditing King County's 2021
  tree-canopy tiles surfaced that the family's own "overview" entry (`Tree
  Canopy 2021 - Tree Points`) has a different schema (`Shape__Area`,
  `Shape__Length`, `TreeTile`) than its actual tile layers (`Height`,
  `Intensity`, `NDVI`) - exactly the kind of cross-dataset drift this tool
  is meant to catch.

## Repo layout

```
data/
  fetch_catalog.py      # pulls the full King County DCAT-US catalog feed
  target_datasets.py    # curated ~18-dataset audit list + family groupings
  checks.py             # the six rule-based checks (no LLM)
  audit_datasets.py     # throttled, resumable audit runner
  catalog.parquet       # gitignored, regenerate via fetch_catalog.py
  audit_results.parquet # gitignored, regenerate via audit_datasets.py
app.py                   # Streamlit dashboard
environment.yml
```

## Setup

```bash
conda env create -f environment.yml
conda activate kc-data-quality-audit
```

```bash
python data/fetch_catalog.py
python data/audit_datasets.py
streamlit run app.py
```

## Stack

pandas, GeoPandas, Shapely, requests, Streamlit, matplotlib.
