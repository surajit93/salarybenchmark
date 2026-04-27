#!/usr/bin/env python3
"""
generate_pages.py
Reads master.json → generates static HTML pages using Jinja2 template.
Phase 1: 50 seed pages (controlled indexing ramp)
Phase 2+: Expand by uncommenting additional combinations
"""

import json
import os
import re
import math
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "master.json"
TMPL_DIR  = ROOT / "templates"
OUT_DIR   = ROOT / "output"

# ── Load data ──────────────────────────────────────────────────────────────
with open(DATA_FILE) as f:
    DATA = json.load(f)

SALARY      = DATA["salary"]
BUCKETS     = SALARY["buckets"]
AGE_BANDS   = SALARY["age_bands"]
STATES      = [
    {"id": k, "name": v["name"], "abbr": v["abbr"]}
    for k, v in SALARY["by_state"].items()
]
RELATED_CALC = DATA["related_calculators"]

# ── Jinja2 env ─────────────────────────────────────────────────────────────
env = Environment(loader=FileSystemLoader(str(TMPL_DIR)))
template = env.get_template("salary.html")

# ── Embed full salary data as JSON for JS calculator ──────────────────────
def build_salary_data_json():
    out = {"national": SALARY["national"]}
    for state_id, state_data in SALARY["by_state"].items():
        out[state_id] = {k: v for k, v in state_data.items()
                         if k not in ("name", "abbr", "cost_index")}
    return json.dumps(out)

SALARY_DATA_JSON = build_salary_data_json()

# ── Related pages builder ──────────────────────────────────────────────────
def build_related_pages(bucket, age_band, state_id=None):
    related = []

    # Adjacent age bands
    bands = [b["id"] for b in AGE_BANDS]
    idx = bands.index(age_band["id"]) if age_band in AGE_BANDS else \
          next((i for i, b in enumerate(AGE_BANDS) if b["id"] == age_band["id"]), 0)

    for offset in [-1, 1]:
        if 0 <= idx + offset < len(AGE_BANDS):
            b = AGE_BANDS[idx + offset]
            related.append({
                "url": f"/salary-percentile/{bucket['id']}/age/{b['id']}/",
                "label": f"{bucket['label']} · {b['label']}",
                "type": "Same salary, different age"
            })

    # Adjacent salary buckets
    b_ids = [b["id"] for b in BUCKETS]
    b_idx = next((i for i, b in enumerate(BUCKETS) if b["id"] == bucket["id"]), 0)
    for offset in [-1, 1]:
        if 0 <= b_idx + offset < len(BUCKETS):
            bk = BUCKETS[b_idx + offset]
            related.append({
                "url": f"/salary-percentile/{bk['id']}/age/{age_band['id']}/",
                "label": f"{bk['label']} · {age_band['label']}",
                "type": "Different salary, same age"
            })

    # State variants (top 3 states)
    for s in STATES[:3]:
        if s["id"] != state_id:
            related.append({
                "url": f"/salary-percentile/{bucket['id']}/age/{age_band['id']}/state/{s['id']}/",
                "label": f"{bucket['label']} in {s['name']}",
                "type": "Same salary, different state"
            })

    return related[:8]  # Max 8 related links per page

# ── Page generator ─────────────────────────────────────────────────────────
def generate_page(bucket, age_band, state_id=None):
    """Generate one static HTML page."""

    state_obj  = SALARY["by_state"].get(state_id) if state_id else None
    state_name = state_obj["name"] if state_obj else None
    cost_index = state_obj.get("cost_index", 1.0) if state_obj else 1.0

    # Pick data
    if state_id and state_obj and age_band["id"] in state_obj:
        data = state_obj[age_band["id"]]
    else:
        data = SALARY["national"][age_band["id"]]

    # Canonical URL
    if state_id:
        canonical = f"/salary-percentile/{bucket['id']}/age/{age_band['id']}/state/{state_id}/"
    else:
        canonical = f"/salary-percentile/{bucket['id']}/age/{age_band['id']}/"

    # Meta
    state_str   = f" in {state_name}" if state_name else ""
    meta_title  = f"Salary Percentile: {bucket['label']} at Age {age_band['label']}{state_str} | BenchmarkSelf"
    meta_desc   = (
        f"Find out where {bucket['label']} ranks for people aged {age_band['label']}{state_str}. "
        f"Median salary for this group is ${data['p50']:,}. "
        f"See your exact percentile instantly — no signup."
    )

    related = build_related_pages(bucket, age_band, state_id)

    html = template.render(
        meta_title        = meta_title,
        meta_description  = meta_desc,
        canonical_url     = canonical,
        bucket_label      = bucket["label"],
        bucket_id         = bucket["id"],
        bucket_mid        = bucket["mid"],
        age_band_label    = age_band["label"],
        age_band_id       = age_band["id"],
        state_id          = state_id,
        state_name        = state_name,
        cost_index        = cost_index,
        data              = type("D", (), data),  # dict → object for template dot access
        age_bands         = AGE_BANDS,
        states            = STATES,
        related_pages     = related,
        salary_data_json  = SALARY_DATA_JSON,
    )

    # Write file
    out_path = OUT_DIR / canonical.strip("/")
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "index.html").write_text(html, encoding="utf-8")
    return canonical

# ── PHASE 1: First 50 pages (controlled ramp) ─────────────────────────────
# Strategy: highest-volume buckets × peak earning age bands × top 5 states
# This generates pages most likely to rank fast and build domain trust.

PHASE1_BUCKETS = [
    "50k-65k", "65k-80k", "80k-100k", "100k-130k", "40k-50k"
]

PHASE1_AGES = [
    "26-30", "31-35", "36-40", "41-45"
]

PHASE1_STATES = [
    "california", "texas", "new-york", "florida", "washington"
]

def generate_phase1():
    pages = []
    count = 0

    # Tier 1: National pages (no state) — 20 pages
    # These rank fastest, no state competition
    for bucket_id in PHASE1_BUCKETS:
        bucket = next(b for b in BUCKETS if b["id"] == bucket_id)
        for age_id in PHASE1_AGES:
            age_band = next(b for b in AGE_BANDS if b["id"] == age_id)
            url = generate_page(bucket, age_band)
            pages.append(url)
            count += 1
            print(f"[{count:03d}] {url}")

    # Tier 2: Top 3 states × top 2 buckets × top 5 ages — 30 pages
    STATE_SUBSET   = PHASE1_STATES[:3]
    BUCKET_SUBSET  = ["65k-80k", "80k-100k"]
    AGE_SUBSET     = PHASE1_AGES[:5]

    for state_id in STATE_SUBSET:
        for bucket_id in BUCKET_SUBSET:
            bucket = next(b for b in BUCKETS if b["id"] == bucket_id)
            for age_id in AGE_SUBSET:
                if count >= 50:
                    break
                age_band = next(b for b in AGE_BANDS if b["id"] == age_id)
                url = generate_page(bucket, age_band, state_id)
                pages.append(url)
                count += 1
                print(f"[{count:03d}] {url}")

    return pages

# ── PHASE 2 (Uncomment at day 30) ────────────────────────────────────────
# def generate_phase2():
#     """Expand to ~500 pages. Run after phase 1 is indexed."""
#     for bucket in BUCKETS:
#         for age_band in AGE_BANDS:
#             generate_page(bucket, age_band)               # national
#             for state_id in [s["id"] for s in STATES]:
#                 generate_page(bucket, age_band, state_id) # per state

# ── Sitemap ────────────────────────────────────────────────────────────────
def build_sitemap(pages):
    base = "https://benchmarkself.dev"
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in pages:
        lines.append(f"  <url><loc>{base}{url}</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>")
    lines.append("</urlset>")
    sitemap_path = OUT_DIR / "sitemap.xml"
    sitemap_path.write_text("\n".join(lines))
    print(f"\nSitemap → {sitemap_path} ({len(pages)} URLs)")

# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("── BenchmarkSelf Page Generator ──────────────────")
    print("Phase: 1 (50 seed pages)\n")

    pages = generate_phase1()
    build_sitemap(pages)

    print(f"\n✓ Generated {len(pages)} pages → {OUT_DIR}")
    print("✓ Sitemap written")
    print("\nNext: git push → Cloudflare Pages auto-deploys")
