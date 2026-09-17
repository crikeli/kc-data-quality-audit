"""
Renders docs/index.html - a self-contained static site (no server, no
Streamlit) for GitHub Pages - from catalog.parquet and audit_results.parquet.
Meant to be run after fetch_catalog.py + audit_datasets.py, either locally
or by the scheduled GitHub Actions workflow (.github/workflows/refresh-audit.yml)
that refreshes the audit daily and republishes this page.

Usage:
    python build_static_site.py
"""

import html as html_lib
import json
import os
from datetime import datetime, timezone

import pandas as pd

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(DATA_DIR)
CATALOG_PATH = os.path.join(DATA_DIR, "catalog.parquet")
RESULTS_PATH = os.path.join(DATA_DIR, "audit_results.parquet")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "docs", "index.html")

CHECK_LABELS = {
    "metadata_completeness": "Metadata completeness",
    "freshness": "Freshness",
    "geometry_validity": "Geometry validity",
    "crs_sanity": "CRS / extent sanity",
    "attribute_null_rates": "Attribute null rates",
    "hub_link_alive": "Hub link alive",
}


def esc(text) -> str:
    return html_lib.escape(str(text)) if text is not None else ""


def check_label(check_name: str) -> str:
    base = check_name.split("[")[0]
    return CHECK_LABELS.get(base, base)


def render_scorecard(per_dataset: pd.DataFrame, family_checks: pd.DataFrame) -> str:
    total = len(per_dataset)
    clean = int((per_dataset["issue_count"] == 0).sum())
    with_issues = int((per_dataset["issue_count"] > 0).sum())

    rows_html = ""
    for _, row in per_dataset.sort_values("issue_count", ascending=False).iterrows():
        rows_html += f"""
        <tr>
          <td>{esc(row['title'])}</td>
          <td>{esc(row['geometry_type'])}</td>
          <td>{int(row['sample_record_count'])}</td>
          <td>{int(row['issue_count'])}</td>
        </tr>"""

    fail_counts: dict[str, int] = {}
    for checks in per_dataset["checks"]:
        for c in checks:
            if not c["passed"]:
                label = check_label(c["check"])
                fail_counts[label] = fail_counts.get(label, 0) + 1

    max_count = max(fail_counts.values()) if fail_counts else 1
    bars_html = ""
    for label, count in sorted(fail_counts.items(), key=lambda kv: kv[1]):
        pct = round(100 * count / max_count)
        bars_html += f"""
        <div class="bar-row">
          <div class="bar-label">{esc(label)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
          <div class="bar-value">{count}</div>
        </div>"""
    if not bars_html:
        bars_html = "<p>No checks failed across the audited subset.</p>"

    family_html = ""
    for _, row in family_checks.iterrows():
        check = row["checks"][0]
        status = "pass" if check["passed"] else "fail"
        family_html += f"""
        <p><span class="badge {status}">{status.upper()}</span>
        <strong>{esc(row['title'])}</strong> - {esc(check['detail'])}</p>"""

    return f"""
    <div class="metrics">
      <div class="metric"><div class="metric-value">{total}</div><div class="metric-label">Datasets audited</div></div>
      <div class="metric"><div class="metric-value">{clean}</div><div class="metric-label">Clean (0 issues flagged)</div></div>
      <div class="metric"><div class="metric-value">{with_issues}</div><div class="metric-label">With at least 1 issue flagged</div></div>
    </div>

    <h2>Datasets ranked by issues flagged</h2>
    <div class="table-wrap">
    <table>
      <thead><tr><th>Dataset</th><th>Geometry type</th><th>Sample size</th><th>Issues flagged</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>

    <h2>How often each check fails</h2>
    <div class="bar-chart">{bars_html}</div>

    <h2>Cross-dataset family consistency</h2>
    {family_html}
    """


def render_dataset_detail(per_dataset: pd.DataFrame, catalog: pd.DataFrame) -> tuple[str, str]:
    catalog_cols = catalog[["title", "description", "license", "modified", "contact_email"]]
    detail = per_dataset.merge(catalog_cols, on="title", how="left")

    options_html = ""
    panels_html = ""
    for i, (_, row) in enumerate(detail.iterrows()):
        slug = f"ds-{i}"
        selected = " selected" if i == 0 else ""
        hidden = "" if i == 0 else " hidden"
        options_html += f'<option value="{slug}"{selected}>{esc(row["title"])}</option>'

        note_html = f'<p class="note">{esc(row["resolution_note"])}</p>' if row.get("resolution_note") else ""
        checks_html = ""
        for c in row["checks"]:
            status = "pass" if c["passed"] else "fail"
            checks_html += f"""
            <p><span class="badge {status}">{status.upper()}</span>
            <strong>{esc(check_label(c['check']))}</strong> - {esc(c['detail'])}</p>"""

        panels_html += f"""
        <div class="detail-panel" id="{slug}"{hidden}>
          <h3>{esc(row['title'])}</h3>
          {note_html}
          <div class="detail-grid">
            <div>
              <p>{esc(row.get('description') or 'No description published.')}</p>
              <p><strong>Geometry type:</strong> {esc(row['geometry_type'])}</p>
              <p><strong>Field count:</strong> {int(row['field_count'])}</p>
              <p><strong>Sampled records checked:</strong> {int(row['sample_record_count'])}</p>
              <p><strong>Last modified (catalog metadata):</strong> {esc(row.get('modified'))}</p>
            </div>
            <div>
              <p><a href="{esc(row['hub_url'])}" target="_blank" rel="noopener">Open on King County Hub</a></p>
              <p><a href="{esc(row['layer_url'])}" target="_blank" rel="noopener">Raw FeatureServer layer</a></p>
            </div>
          </div>
          <h4>Checks</h4>
          {checks_html}
        </div>"""

    return options_html, panels_html


ABOUT_HTML = """
<h2>Methodology</h2>
<p>This tool audits a <strong>curated subset</strong> of King County's GIS
Open Data portal (<code>gis-kingcounty.opendata.arcgis.com</code>) - not
the full catalog of several hundred datasets. The subset spans different
geometry types (polygon/line/point), publishing patterns (a single layer
vs. a tiled family of layers), and a mix of infrastructure, hazard, and
environmental data.</p>

<p><strong>Where the data comes from:</strong></p>
<ul>
  <li>Portal-wide metadata: King County's own
    <a href="https://gis-kingcounty.opendata.arcgis.com/api/feed/dcat-us/1.1.json" target="_blank" rel="noopener">DCAT-US 1.1 catalog feed</a>
    (<code>data/fetch_catalog.py</code>) - a standard, publicly documented
    open-data format, no authentication required.</li>
  <li>Per-dataset checks: each target dataset's own ArcGIS FeatureServer
    REST endpoint (<code>data/audit_datasets.py</code>), throttled to 1
    request/second and limited to a <strong>bounded sample</strong> (up to
    500 records, or the service's own <code>maxRecordCount</code> if
    smaller) - never a full download.</li>
</ul>

<p><strong>Checks run</strong> (all deterministic, no LLM involved - see
<code>data/checks.py</code>): Metadata completeness, Freshness, Geometry
validity, CRS / extent sanity, Attribute null rates, Hub link alive.</p>

<p><strong>How this page stays current:</strong> a scheduled GitHub Actions
workflow (<code>.github/workflows/refresh-audit.yml</code>) re-runs the
fetch and audit scripts daily and republishes this page - see the "Last
audited" timestamp at the top.</p>

<p><strong>Known limitations:</strong></p>
<ul>
  <li>This is a curated subset (~18 datasets), not the full portal - a
    production version would need the checks engine hardened against far
    more schema variety before running unattended across everything King
    County publishes.</li>
  <li>Data-level checks run on a bounded sample, not the full dataset.</li>
  <li>Freshness and null-rate thresholds are simple fixed heuristics, not
    an official data-quality standard - they surface candidates worth a
    human look, not certified findings.</li>
  <li>The CRS/extent sanity check is a rough King County bounding-box
    proxy, not an exact spatial-containment check.</li>
</ul>

<p><strong>Sources & links:</strong></p>
<ul>
  <li><a href="https://gis-kingcounty.opendata.arcgis.com/" target="_blank" rel="noopener">King County GIS Open Data Portal</a></li>
  <li><a href="https://gis-kingcounty.opendata.arcgis.com/api/feed/dcat-us/1.1.json" target="_blank" rel="noopener">DCAT-US catalog feed</a></li>
  <li><a href="https://kingcounty.gov/en/dept/kcit/data-information-services/gis-center/about/terms-conditions-copyrights" target="_blank" rel="noopener">King County GIS terms of use</a></li>
  <li><a href="https://github.com/crikeli/kc-data-quality-audit" target="_blank" rel="noopener">Source code on GitHub</a></li>
</ul>
"""

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>King County GIS Open Data - Quality Audit</title>
<style>
  :root {{
    --bg: #ffffff; --ink: #1a1a1a; --muted: #5b5f66; --border: #e2e4e8;
    --surface: #f6f7f9; --accent: #2a78d6; --pass: #1baf7a; --fail: #e34948;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0e1117; --ink: #e6e8eb; --muted: #9aa1ab; --border: #2a2e35;
      --surface: #161a21; --accent: #4f9bf0; --pass: #2fd191; --fail: #ff6b66;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px clamp(16px, 4vw, 48px) 64px; background: var(--bg);
    color: var(--ink); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  h1 {{ margin-bottom: 4px; }}
  .caption {{ color: var(--muted); margin-top: 0; }}
  .tabs {{ display: flex; gap: 8px; border-bottom: 1px solid var(--border); margin: 20px 0 24px; flex-wrap: wrap; }}
  .tab-btn {{
    background: none; border: none; padding: 10px 4px; margin-right: 20px; font-size: 15px;
    color: var(--muted); cursor: pointer; border-bottom: 2px solid transparent;
  }}
  .tab-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
  .metrics {{ display: flex; gap: 40px; flex-wrap: wrap; margin-bottom: 24px; }}
  .metric-value {{ font-size: 32px; font-weight: 700; }}
  .metric-label {{ color: var(--muted); font-size: 13px; }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 480px; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 14px; }}
  th {{ color: var(--muted); font-weight: 600; background: var(--surface); }}
  .bar-chart {{ max-width: 640px; }}
  .bar-row {{ display: grid; grid-template-columns: 160px 1fr 32px; align-items: center; gap: 10px; margin-bottom: 10px; }}
  .bar-label {{ font-size: 13px; color: var(--muted); text-align: right; }}
  .bar-track {{ background: var(--surface); border-radius: 3px; height: 18px; overflow: hidden; }}
  .bar-fill {{ background: var(--accent); height: 100%; border-radius: 3px 0 0 3px; }}
  .bar-value {{ font-size: 13px; color: var(--muted); }}
  select {{
    font-size: 14px; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--surface); color: var(--ink); width: 100%; max-width: 520px; margin-bottom: 20px;
  }}
  .detail-grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 24px; }}
  @media (max-width: 640px) {{ .detail-grid {{ grid-template-columns: 1fr; }} .bar-row {{ grid-template-columns: 100px 1fr 28px; }} }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: 0.03em; padding: 2px 6px; border-radius: 4px; margin-right: 6px; }}
  .badge.pass {{ background: color-mix(in srgb, var(--pass) 20%, transparent); color: var(--pass); }}
  .badge.fail {{ background: color-mix(in srgb, var(--fail) 20%, transparent); color: var(--fail); }}
  .note {{ background: var(--surface); border-radius: 6px; padding: 10px 14px; font-size: 14px; }}
  code {{ background: var(--surface); padding: 1px 5px; border-radius: 4px; font-size: 0.9em; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
  <h1>King County GIS Open Data - Quality Audit</h1>
  <p class="caption">A rule-based audit of a curated subset of King County's published GIS layers.
  Last audited: {last_audited}. See the About tab for exactly what's checked and why.</p>

  <div class="tabs">
    <button class="tab-btn active" data-tab="scorecard">Scorecard</button>
    <button class="tab-btn" data-tab="detail">Dataset Detail</button>
    <button class="tab-btn" data-tab="about">About</button>
  </div>

  <div class="tab-panel active" id="tab-scorecard">{scorecard_html}</div>

  <div class="tab-panel" id="tab-detail">
    <label for="dataset-select"><strong>Choose a dataset</strong></label><br>
    <select id="dataset-select">{dataset_options}</select>
    {dataset_panels}
  </div>

  <div class="tab-panel" id="tab-about">{about_html}</div>

<script>
  document.querySelectorAll(".tab-btn").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      document.querySelectorAll(".tab-btn").forEach(function (b) {{ b.classList.remove("active"); }});
      document.querySelectorAll(".tab-panel").forEach(function (p) {{ p.classList.remove("active"); }});
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    }});
  }});
  document.getElementById("dataset-select").addEventListener("change", function (e) {{
    document.querySelectorAll(".detail-panel").forEach(function (p) {{ p.hidden = true; }});
    document.getElementById(e.target.value).hidden = false;
  }});
</script>
</body>
</html>
"""


def main() -> None:
    catalog = pd.read_parquet(CATALOG_PATH)
    results = pd.read_parquet(RESULTS_PATH)
    results["checks"] = results["checks_json"].apply(json.loads)
    per_dataset = results[~results["is_family_check"]].copy()
    family_checks = results[results["is_family_check"]].copy()

    scorecard_html = render_scorecard(per_dataset, family_checks)
    dataset_options, dataset_panels = render_dataset_detail(per_dataset, catalog)

    page = PAGE_TEMPLATE.format(
        last_audited=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        scorecard_html=scorecard_html,
        dataset_options=dataset_options,
        dataset_panels=dataset_panels,
        about_html=ABOUT_HTML,
    )

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(page)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
