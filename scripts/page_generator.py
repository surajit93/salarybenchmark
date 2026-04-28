#!/usr/bin/env python3
"""
Page generator for BenchmarkSelf salary pages.
Reads data/master.json + data/config.json and renders static pages from templates/salary.html.
"""

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "master.json"
CONFIG_FILE = ROOT / "data" / "config.json"
INDEX_FILE = ROOT / "data" / "page_index.json"
TMPL_DIR = ROOT / "templates"
OUT_DIR = ROOT / "output"
TMP_OUT_DIR = ROOT / "output.__build_tmp__"

REQUIRED_DATA_FIELDS = ("p10", "p25", "p50", "p75", "p90")
DEFAULT_CONFIG_VALUES = {
    "dataset_last_updated": "2023-12-31",
    "homepage_intro": "Compare your salary, savings, and net worth anonymously.",
}


def load_json_file(path: Path, label: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"ERROR: Unable to load {label} ({path}): {exc}")
        raise


def normalize_domain(domain: str) -> str:
    return (domain or "").rstrip("/")


def normalize_canonical(path: str) -> str:
    stripped = "/" + path.strip("/")
    if stripped == "/":
        return "/"
    return f"{stripped}/"


def minify_html_light(html: str) -> str:
    html = re.sub(r"[ \t]+\n", "\n", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip() + "\n"


def build_salary_data_json(salary: dict) -> str:
    out = {"national": salary["national"]}
    for state_id, state_data in salary["by_state"].items():
        out[state_id] = {k: v for k, v in state_data.items() if k not in ("name", "abbr", "cost_index")}
    return json.dumps(out)


def estimate_percentile_for_salary(salary_value: int, data: dict) -> int:
    points = [
        (0, 0),
        (data["p10"], 10),
        (data["p25"], 25),
        (data["p50"], 50),
        (data["p75"], 75),
        (data["p90"], 90),
        (max(data["p90"] * 2, data["p90"] + 1), 99),
    ]
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        if salary_value <= x1:
            if x1 == x0:
                return y1
            pct = y0 + (salary_value - x0) / (x1 - x0) * (y1 - y0)
            return max(1, min(99, round(pct)))
    return 99


def percentile_range_label(bucket_mid: int, data: dict) -> str:
    center = estimate_percentile_for_salary(bucket_mid, data)
    low = max(1, center - 10)
    high = min(99, center + 10)
    return f"{low}th–{high}th"


def unique_intro(bucket_mid: int) -> str:
    if bucket_mid < 50000:
        return "This benchmark helps you see how this income level compares for your age group."
    if bucket_mid < 90000:
        return "Use this comparison to check whether your current income is above or below your peers."
    return "Higher earners can use this page to benchmark progress against similar professionals."


def build_related_pages(buckets: list, age_bands: list, states: list, bucket: dict, age_band: dict, state_id=None):
    related = []
    bands = [b["id"] for b in age_bands]
    idx = bands.index(age_band["id"]) if age_band["id"] in bands else 0

    for offset in (-1, 1):
        if 0 <= idx + offset < len(age_bands):
            b = age_bands[idx + offset]
            related.append(
                {
                    "url": normalize_canonical(f"/salary-percentile/{bucket['id']}/age/{b['id']}/"),
                    "label": f"{bucket['label']} · {b['label']}",
                    "type": "Same salary, different age",
                }
            )

    b_idx = next((i for i, b in enumerate(buckets) if b["id"] == bucket["id"]), 0)
    for offset in (-1, 1):
        if 0 <= b_idx + offset < len(buckets):
            bk = buckets[b_idx + offset]
            related.append(
                {
                    "url": normalize_canonical(f"/salary-percentile/{bk['id']}/age/{age_band['id']}/"),
                    "label": f"{bk['label']} · {age_band['label']}",
                    "type": "Different salary, same age",
                }
            )

    for s in states[:3]:
        if s["id"] != state_id:
            related.append(
                {
                    "url": normalize_canonical(
                        f"/salary-percentile/{bucket['id']}/age/{age_band['id']}/state/{s['id']}/"
                    ),
                    "label": f"{bucket['label']} in {s['name']}",
                    "type": "Same salary, different state",
                }
            )

    dedup = []
    seen = set()
    for item in related:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        dedup.append(item)
    return dedup[:5]


def validate_template_context(ctx: dict):
    required = ("bucket_label", "age_band_label", "state_name", "percentile_range")
    missing = [k for k in required if k not in ctx]
    data = ctx.get("data") or {}
    data_missing = [k for k in REQUIRED_DATA_FIELDS if k not in data]
    if missing or data_missing:
        raise ValueError(f"Missing template fields: {missing}; missing data fields: {data_missing}")


def build_sitemap_xml(all_paths: list, domain: str) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in sorted(set(all_paths)):
        norm = normalize_canonical(url)
        lines.append(
            f"  <url><loc>{domain}{norm}</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>"
        )
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def build_root_index(config: dict, buckets: list, age_bands: list, states: list) -> str:
    key_salary_links = "\n".join(
        f'<li><a href="/salary-percentile/{b["id"]}/age/{age_bands[2]["id"]}/">{b["label"]} at {age_bands[2]["label"]}</a></li>'
        for b in buckets[:5]
    )
    state_links = "\n".join(
        f'<li><a href="/salary-percentile/65k-80k/age/31-35/state/{s["id"]}/">Salary percentile in {s["name"]}</a></li>'
        for s in states[:5]
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>BenchmarkSelf</title></head><body>"
        "<h1>Benchmark Your Financial Position</h1>"
        f"<p>{config.get('homepage_intro', DEFAULT_CONFIG_VALUES['homepage_intro'])}</p>"
        "<h2>Start with salary benchmarks</h2><ul>"
        f"{key_salary_links}</ul>"
        "<h2>Browse by category</h2><ul>"
        "<li><a href='/salary-by-age/'>Salary by age</a></li>"
        "<li><a href='/salary-by-state/'>Salary by state</a></li>"
        "<li><a href='/best-salary-ranges/'>Best salary ranges</a></li>"
        "</ul><h2>Popular state pages</h2><ul>"
        f"{state_links}</ul>"
        "</body></html>"
    )


def build_crawl_entry_pages(buckets: list, age_bands: list, states: list):
    age_links = "".join(
        f"<li><a href='/salary-percentile/65k-80k/age/{a['id']}/'>{a['label']} salary percentile</a></li>" for a in age_bands
    )
    state_links = "".join(
        f"<li><a href='/salary-percentile/65k-80k/age/31-35/state/{s['id']}/'>{s['name']} salary percentile</a></li>" for s in states
    )
    bucket_links = "".join(
        f"<li><a href='/salary-percentile/{b['id']}/age/31-35/'>{b['label']} benchmark</a></li>" for b in buckets
    )
    return {
        "/salary-by-age/": f"<h1>Salary by Age</h1><p>Browse salary percentile pages by age band.</p><ul>{age_links}</ul>",
        "/salary-by-state/": f"<h1>Salary by State</h1><p>Browse state salary benchmark pages.</p><ul>{state_links}</ul>",
        "/best-salary-ranges/": f"<h1>Best Salary Ranges</h1><p>Explore salary ranges and where they rank.</p><ul>{bucket_links}</ul>",
    }


def build_404_page() -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Page Not Found</title></head><body><h1>404 — Page not found</h1>"
        "<p>The page you requested does not exist.</p><p><a href='/'>Return to homepage</a></p></body></html>\n"
    )


def write_file(root: Path, canonical_or_file: str, content: str):
    if canonical_or_file.endswith(".xml") or canonical_or_file.endswith(".txt") or canonical_or_file.endswith(".html"):
        path = root / canonical_or_file.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return

    page_dir = root / canonical_or_file.strip("/")
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "index.html").write_text(content, encoding="utf-8")


def main():
    data = load_json_file(DATA_FILE, "master.json")
    config = load_json_file(CONFIG_FILE, "config.json")

    for k, v in DEFAULT_CONFIG_VALUES.items():
        config.setdefault(k, v)

    if INDEX_FILE.exists():
        page_index = load_json_file(INDEX_FILE, "page_index.json")
    else:
        page_index = {"config_version": None, "last_build": None, "pages": {}}

    if page_index.get("config_version") != config.get("version"):
        page_index["pages"] = {}

    salary = data["salary"]
    buckets = salary["buckets"]
    age_bands = salary["age_bands"]
    states = [{"id": k, "name": v["name"], "abbr": v["abbr"]} for k, v in salary["by_state"].items()]

    env = Environment(loader=FileSystemLoader(str(TMPL_DIR)), undefined=StrictUndefined)
    template = env.get_template("salary.html")
    salary_data_json = build_salary_data_json(salary)

    phase1_buckets = ["50k-65k", "65k-80k", "80k-100k", "100k-130k", "40k-50k"]
    phase1_ages = ["26-30", "31-35", "36-40", "41-45"]
    phase1_states = ["california", "texas", "new-york", "florida", "washington"]
    state_subset = phase1_states[:3]
    bucket_subset = ["65k-80k", "80k-100k"]
    age_subset = phase1_ages

    rendered_pages = {}
    skipped = []

    def queue_page(bucket: dict, age_band: dict, state_id=None):
        state_obj = salary["by_state"].get(state_id) if state_id else None
        state_name = state_obj["name"] if state_obj else None

        if state_id and state_obj and age_band["id"] in state_obj:
            page_data = state_obj[age_band["id"]]
        else:
            page_data = salary["national"].get(age_band["id"])

        if not isinstance(page_data, dict):
            skipped.append(f"skip {bucket['id']} {age_band['id']} {state_id or 'national'}: missing salary data")
            return

        cost_index = page_data.get("cost_index")
        if cost_index is None:
            cost_index = state_obj.get("cost_index", 1.0) if state_obj else None

        canonical = normalize_canonical(
            f"/salary-percentile/{bucket['id']}/age/{age_band['id']}/state/{state_id}/"
            if state_id
            else f"/salary-percentile/{bucket['id']}/age/{age_band['id']}/"
        )

        state_str = f" in {state_name}" if state_name else ""
        meta_title = f"Is ${bucket['mid']:,} a Good Salary at Age {age_band['label']}{state_str}? | BenchmarkSelf"
        meta_desc = (
            f"Find out where {bucket['label']} ranks for people aged {age_band['label']}{state_str}. "
            f"Median salary for this group is ${page_data['p50']:,}. See your exact percentile instantly — no signup."
        )

        related_pages = build_related_pages(buckets, age_bands, states, bucket, age_band, state_id) or []
        percentile_range = percentile_range_label(bucket["mid"], page_data)

        ctx = {
            "meta_title": meta_title,
            "meta_description": meta_desc,
            "canonical_url": canonical,
            "bucket_label": bucket["label"],
            "bucket_id": bucket["id"],
            "bucket_mid": bucket["mid"],
            "age_band_label": age_band["label"],
            "age_band_id": age_band["id"],
            "state_id": state_id,
            "state_name": state_name,
            "cost_index": cost_index,
            "percentile_range": percentile_range,
            "data": page_data,
            "age_bands": age_bands,
            "states": states,
            "related_pages": related_pages,
            "salary_data_json": salary_data_json,
            "config": config,
            "dataset_version": config.get("version", "unknown"),
            "dataset_last_updated": config.get("dataset_last_updated"),
            "intro_sentence": unique_intro(bucket["mid"]),
        }

        try:
            validate_template_context(ctx)
        except Exception as exc:
            skipped.append(f"skip {canonical}: {exc}")
            return

        html = minify_html_light(template.render(**ctx))
        rendered_pages[canonical] = html

    count = 0
    for bucket_id in phase1_buckets:
        bucket = next(b for b in buckets if b["id"] == bucket_id)
        for age_id in phase1_ages:
            age_band = next(b for b in age_bands if b["id"] == age_id)
            queue_page(bucket, age_band)
            count += 1

    for state_id in state_subset:
        for bucket_id in bucket_subset:
            bucket = next(b for b in buckets if b["id"] == bucket_id)
            for age_id in age_subset:
                if count >= 50:
                    break
                age_band = next(b for b in age_bands if b["id"] == age_id)
                queue_page(bucket, age_band, state_id)
                count += 1

    if skipped:
        for msg in skipped:
            print(f"WARN: {msg}")

    if len(rendered_pages) == 0:
        print("ERROR: No pages rendered. Stopping build.")
        raise RuntimeError("Build failed before file writes")

    if TMP_OUT_DIR.exists():
        shutil.rmtree(TMP_OUT_DIR)
    TMP_OUT_DIR.mkdir(parents=True, exist_ok=True)

    for canonical, html in sorted(rendered_pages.items()):
        write_file(TMP_OUT_DIR, canonical, html)
        page_index["pages"][canonical] = {"generated_at": datetime.utcnow().isoformat()}

    crawl_pages = build_crawl_entry_pages(buckets, age_bands, states)
    for canonical, body in crawl_pages.items():
        write_file(
            TMP_OUT_DIR,
            canonical,
            minify_html_light(f"<!doctype html><html><head><meta charset='utf-8'><title>BenchmarkSelf</title></head><body>{body}</body></html>"),
        )

    root_index = minify_html_light(build_root_index(config, buckets, age_bands, states))
    write_file(TMP_OUT_DIR, "/index.html", root_index)
    write_file(TMP_OUT_DIR, "/404.html", build_404_page())

    domain = normalize_domain(config["domain"])
    robots_content = f"User-agent: *\nAllow: /\nSitemap: {domain}/sitemap.xml\n"
    write_file(TMP_OUT_DIR, "/robots.txt", robots_content)

    existing_paths = list(page_index.get("pages", {}).keys())
    all_paths = list(rendered_pages.keys()) + list(crawl_pages.keys()) + ["/"] + existing_paths
    sitemap_xml = build_sitemap_xml(all_paths, domain)
    write_file(TMP_OUT_DIR, "/sitemap.xml", sitemap_xml)

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    TMP_OUT_DIR.rename(OUT_DIR)

    page_index["config_version"] = config.get("version")
    page_index["last_build"] = datetime.utcnow().isoformat()
    INDEX_FILE.write_text(json.dumps(page_index, indent=2), encoding="utf-8")

    print(f"✓ Generated {len(rendered_pages)} salary pages")
    print(f"✓ Added {len(crawl_pages)} crawl entry pages + index + 404")
    print(f"✓ Sitemap contains {len(set(all_paths))} canonical URLs")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BUILD FAILED: {exc}")
        sys.exit(1)
