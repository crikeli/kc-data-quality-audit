# King County GIS Data Quality Audit

A rule-based data-quality audit of a curated subset of King County's
published GIS layers (`gis-kingcounty.opendata.arcgis.com`), presented as a
Streamlit dashboard. Built as a portfolio piece specifically aimed at King
County's own GIS operations - not a map of King County's data, but an audit
of it: staleness, geometry validity, metadata completeness, and schema
consistency across related dataset families.

## Status: live on GitHub Pages, refreshed daily

**Live site: https://crikeli.github.io/kc-data-quality-audit/**

Runs entirely against King County's public, unauthenticated APIs - no
permission requirements, unlike the King County Assessor photo pipeline
used in a sibling project (`seattle-house-styles`). Currently audits an
18-dataset curated subset (see "Known limitations" below for why not the
full portal). A scheduled GitHub Actions workflow re-runs the audit daily
and republishes the static site - see "How it's deployed" below.

There's also a Streamlit version (`app.py`) with the same three views, kept
for local interactive use - GitHub Pages can only serve static files, so it
can't host `app.py` directly (see "How it's deployed").

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

**Presenting it.** Two front ends share the same underlying data:
- `app.py` (Streamlit) - for local interactive use.
- `data/build_static_site.py` - renders the same three views (Scorecard,
  Dataset Detail, About) as a single self-contained static HTML page with
  a small hand-written JS tab/selector, no framework - this is what's
  actually deployed to GitHub Pages, since Pages can't run a Python server.

## How it's deployed

GitHub Pages serves static files only - it can't run Streamlit, which
needs a live Python process. So the deployed site is the static HTML from
`build_static_site.py`, published from the `docs/` folder on `main` (same
pattern as the sibling `seattle-crime-hotspots` repo).

`.github/workflows/refresh-audit.yml` runs daily (and on-demand via
`workflow_dispatch`, and on pushes that touch `data/`): it re-runs
`fetch_catalog.py` + `audit_datasets.py --force` + `build_static_site.py`,
then commits `docs/index.html` if it changed. This is a **daily batch
refresh, not a live per-visit check** - genuinely live status (each visitor's
browser querying King County directly) is possible in principle since
ArcGIS REST services are typically CORS-open, but was deliberately not
built: it would mean re-running checks from every page view instead of one
controlled batch a day, and porting the sampled geometry/null-rate checks
from Python/Shapely to JavaScript. The "Last audited" timestamp on the page
is honest about which of these this is.

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
.github/workflows/
  refresh-audit.yml     # daily cron: re-audits and republishes docs/index.html
data/
  fetch_catalog.py      # pulls the full King County DCAT-US catalog feed
  target_datasets.py    # curated ~18-dataset audit list + family groupings
  checks.py             # the six rule-based checks (no LLM)
  audit_datasets.py     # throttled, resumable audit runner
  build_static_site.py  # renders docs/index.html (the deployed site)
  catalog.parquet       # gitignored, regenerate via fetch_catalog.py
  audit_results.parquet # gitignored, regenerate via audit_datasets.py
docs/
  index.html            # the deployed static site (served by GitHub Pages)
app.py                   # Streamlit dashboard (local use only)
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
streamlit run app.py            # interactive local dashboard, or:
python data/build_static_site.py  # regenerate docs/index.html
```

## Stack

pandas, GeoPandas, Shapely, requests, Streamlit, matplotlib.
