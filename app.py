"""
Streamlit dashboard for the King County GIS Open Data quality audit.
Reads data/catalog.parquet (full portal metadata) and
data/audit_results.parquet (rule-based check results for the curated
subset) - both produced by the scripts in data/. Run:

    streamlit run app.py
"""

import json
import os

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CATALOG_PATH = os.path.join(DATA_DIR, "catalog.parquet")
RESULTS_PATH = os.path.join(DATA_DIR, "audit_results.parquet")

CHECK_LABELS = {
    "metadata_completeness": "Metadata completeness",
    "freshness": "Freshness",
    "geometry_validity": "Geometry validity",
    "crs_sanity": "CRS / extent sanity",
    "attribute_null_rates": "Attribute null rates",
    "hub_link_alive": "Hub link alive",
}


@st.cache_data
def load_data():
    catalog = pd.read_parquet(CATALOG_PATH)
    results = pd.read_parquet(RESULTS_PATH)
    results["checks"] = results["checks_json"].apply(json.loads)
    return catalog, results


def check_label(check_name: str) -> str:
    base = check_name.split("[")[0]
    return CHECK_LABELS.get(base, base)


st.set_page_config(page_title="King County GIS Data Quality Audit", layout="wide")
st.title("King County GIS Open Data - Quality Audit")
st.caption(
    "A rule-based audit of a curated subset of King County's published GIS "
    "layers. See the About tab for exactly what's checked and why."
)

catalog, results = load_data()
per_dataset = results[~results["is_family_check"]].copy()
family_checks = results[results["is_family_check"]].copy()

catalog_cols = catalog[["title", "description", "license", "modified", "keywords", "contact_email"]]
detail = per_dataset.merge(catalog_cols, on="title", how="left")

tab_scorecard, tab_detail, tab_about = st.tabs(["Scorecard", "Dataset Detail", "About"])

with tab_scorecard:
    col1, col2, col3 = st.columns(3)
    col1.metric("Datasets audited", len(per_dataset))
    col2.metric("Clean (0 issues flagged)", int((per_dataset["issue_count"] == 0).sum()))
    col3.metric("With at least 1 issue flagged", int((per_dataset["issue_count"] > 0).sum()))

    st.subheader("Datasets ranked by issues flagged")
    scorecard_table = per_dataset[
        ["title", "geometry_type", "sample_record_count", "issue_count"]
    ].sort_values("issue_count", ascending=False).reset_index(drop=True)
    scorecard_table.columns = ["Dataset", "Geometry type", "Sample size", "Issues flagged"]
    st.dataframe(scorecard_table, use_container_width=True, hide_index=True)

    st.subheader("How often each check fails")
    fail_counts = {}
    for checks in per_dataset["checks"]:
        for c in checks:
            if not c["passed"]:
                label = check_label(c["check"])
                fail_counts[label] = fail_counts.get(label, 0) + 1
    if fail_counts:
        labels = sorted(fail_counts, key=fail_counts.get)
        counts = [fail_counts[l] for l in labels]

        # Streamlit here defaults to its dark theme - match the chart to it
        # instead of leaving matplotlib's default white card, per the
        # dataviz skill's "dark mode is selected, not automatic" guidance.
        bg = "#0e1117"
        ink = "#c9d1d9"
        fig, ax = plt.subplots(figsize=(6, max(2, 0.4 * len(labels))))
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.barh(labels, counts, color="#2a78d6")
        ax.set_xlabel("Datasets flagged", color=ink)
        ax.tick_params(colors=ink)
        for spine in ax.spines.values():
            spine.set_color(ink)
        ax.spines[["top", "right"]].set_visible(False)
        st.pyplot(fig)
    else:
        st.write("No checks failed across the audited subset.")

    if not family_checks.empty:
        st.subheader("Cross-dataset family consistency")
        for _, row in family_checks.iterrows():
            check = row["checks"][0]
            status = "PASS" if check["passed"] else "FAIL"
            st.write(f"**[{status}] {row['title']}** - {check['detail']}")

with tab_detail:
    selected_title = st.selectbox("Choose a dataset", detail["title"].tolist())
    row = detail[detail["title"] == selected_title].iloc[0]

    st.subheader(selected_title)
    if row.get("resolution_note"):
        st.info(row["resolution_note"])

    meta_col, link_col = st.columns([2, 1])
    with meta_col:
        st.write(row.get("description") or "_No description published._")
        st.write(f"**Geometry type:** {row['geometry_type']}")
        st.write(f"**Field count:** {int(row['field_count'])}")
        st.write(f"**Sampled records checked:** {int(row['sample_record_count'])}")
        st.write(f"**Last modified (catalog metadata):** {row.get('modified')}")
    with link_col:
        st.markdown(f"[Open on King County Hub]({row['hub_url']})")
        st.markdown(f"[Raw FeatureServer layer]({row['layer_url']})")
        if row.get("license"):
            st.caption(row["license"][:300])

    st.divider()
    st.subheader("Checks")
    for c in row["checks"]:
        status = "PASS" if c["passed"] else "FAIL"
        st.write(f"**[{status}] {check_label(c['check'])}** - {c['detail']}")

with tab_about:
    st.header("Methodology")
    st.markdown(
        """
This tool audits a **curated subset** of King County's GIS Open Data
portal (`gis-kingcounty.opendata.arcgis.com`) - not the full catalog of
several hundred datasets. The subset was chosen to span different geometry
types (polygon/line/point), publishing patterns (a single layer vs. a
tiled family of layers), and a mix of infrastructure, hazard, and
environmental data.

**Where the data comes from:**
- Portal-wide metadata: King County's own
  [DCAT-US 1.1 catalog feed](https://gis-kingcounty.opendata.arcgis.com/api/feed/dcat-us/1.1.json)
  (`data/fetch_catalog.py`) - a standard, publicly documented open-data
  format, no authentication required.
- Per-dataset checks: each target dataset's own ArcGIS FeatureServer REST
  endpoint (`data/audit_datasets.py`), throttled to 1 request/second and
  limited to a **bounded sample** (up to 500 records, or the service's own
  `maxRecordCount` if smaller) - never a full download.

**Checks run (all deterministic, no LLM involved - see `data/checks.py`):**
"""
    )
    for name, label in CHECK_LABELS.items():
        st.markdown(f"- **{label}**")
    st.markdown(
        """
**Known limitations:**
- This is a curated subset (18 datasets as of the last audit run), not the
  full portal - a real production version of this tool would need to scale
  the checks engine and add a review workflow before running unattended
  across everything King County publishes.
- Data-level checks run on a bounded sample, not the full dataset - a
  cleaner sample than the true population is possible for very large layers.
- Freshness and null-rate thresholds are simple fixed heuristics (documented
  in `data/checks.py`), not an official data-quality standard - they surface
  candidates worth a human look, not certified findings.
- The CRS/extent sanity check is a rough King County bounding-box proxy, not
  an exact spatial-containment check.

**Sources & links:**
- [King County GIS Open Data Portal](https://gis-kingcounty.opendata.arcgis.com/)
- [DCAT-US catalog feed](https://gis-kingcounty.opendata.arcgis.com/api/feed/dcat-us/1.1.json)
- [King County GIS terms of use](https://kingcounty.gov/en/dept/kcit/data-information-services/gis-center/about/terms-conditions-copyrights)
"""
    )
