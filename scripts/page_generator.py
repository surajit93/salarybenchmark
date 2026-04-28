#!/usr/bin/env python3
"""
Page generator for BenchmarkSelf salary pages.
Reads data/master.json + data/config.json and renders static pages from templates/salary.html.
"""

import json
import logging
import hashlib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

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
    "build": {
        "max_pages": 5000,
        "include_state_pages": True,
        "state_subset_size": None,
    },
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger("page_generator")


def load_json_file(path: Path, label: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"ERROR: Unable to load {label} ({path}): {exc}")
        raise


def normalize_domain(domain: str) -> str:
    return (domain or "").rstrip("/")


def validate_domain(domain: str) -> str:
    normalized = normalize_domain(domain)
    if not normalized:
        raise ValueError("config.domain is required")
    if not re.match(r"^https?://[a-z0-9.-]+(?::\d+)?$", normalized, re.IGNORECASE):
        raise ValueError(f"Invalid config.domain format: {domain!r}")
    return normalized


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
    out = {"national": salary.get("national", {})}
    for state_id, state_data in salary.get("by_state", {}).items():
        out[state_id] = {k: v for k, v in state_data.items() if k not in ("name", "abbr", "cost_index")}
    return json.dumps(out)


def estimate_percentile_for_salary(salary_value: int, data: dict) -> int:
    try:
        p10 = data["p10"]
        p25 = data["p25"]
        p50 = data["p50"]
        p75 = data["p75"]
        p90 = data["p90"]
    except KeyError:
        return 50  # safe fallback

    points = [
        (0, 0),
        (p10, 10),
        (p25, 25),
        (p50, 50),
        (p75, 75),
        (p90, 90),
        (max(p90 * 2, p90 + 1), 99),
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
    return f"{low}th–{high}th percentile"


def unique_intro(bucket_mid: int) -> str:
    return "Use this salary benchmark to compare where this income level lands for your current profile."


def unique_intro_for_context(bucket: dict, age_band: dict, state_name: str | None) -> str:
    bucket_mid = bucket["mid"]
    age_label = age_band["label"]
    state_fragment = f" in {state_name}" if state_name else " nationally"
    if bucket_mid < 50000 and age_band["id"] in ("22-25", "26-30"):
        return f"Early-career earners at {bucket['label']}{state_fragment} can use this page to set realistic income milestones for {age_label}."
    if bucket_mid < 50000:
        return f"This view shows how a {bucket['label']} salary compares for people in {age_label}{state_fragment}, including how close it is to the group median."
    if bucket_mid < 90000 and age_band["id"] in ("31-35", "36-40", "41-45"):
        return f"For mid-career professionals in {age_label}{state_fragment}, this benchmark highlights whether {bucket['label']} lands below, near, or above typical earnings."
    if bucket_mid < 90000:
        return f"Use this comparison to evaluate how a {bucket['label']} income ranks for {age_label}{state_fragment} and what range nearby earners typically make."
    if state_name:
        return f"Higher-income households earning around {bucket['label']} in {state_name} can use this page to benchmark percentile rank while accounting for regional salary patterns."
    return f"Higher earners in {age_label} can use this benchmark to track whether {bucket['label']} is keeping pace with national top-quartile and top-decile salaries."


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


def validate_page_data(page_data: dict, bucket: dict, age_band: dict, state_id: str | None = None) -> tuple[bool, str]:
    if not bucket.get("label"):
        return False, "bucket_label missing"
    if not age_band.get("label"):
        return False, "age_band missing"
    missing = [k for k in REQUIRED_DATA_FIELDS if k not in page_data]
    if missing:
        return False, f"missing percentile fields: {', '.join(missing)}"
    return True, ""


def expected_output_relative(canonical: str) -> str:
    norm = normalize_canonical(canonical)
    return "index.html" if norm == "/" else f"{norm.strip('/')}/index.html"


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
    if not age_bands:
        raise ValueError("age_bands cannot be empty")

    default_age = age_bands[2] if len(age_bands) > 2 else age_bands[0]
    key_salary_links = "\n".join(
        f'<li><a href="/salary-percentile/{b["id"]}/age/{default_age["id"]}/">{b["label"]} at {default_age["label"]}</a></li>'
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
    by_age_intro = (
        "<h1>Salary by Age</h1>"
        "<p>Salary growth is rarely linear, and age-based benchmarks help explain why. "
        "At earlier stages, income ranges are tighter and movement between percentiles can happen quickly. "
        "By the mid-30s and 40s, the spread between the median and top quartile becomes much wider. "
        "These pages let you compare your current salary with typical earnings for the same age band, "
        "then jump directly into percentile views for practical context. "
        "Use these age pages to identify whether your pay is near the median, ahead of peers, or lagging, "
        "and to set the next realistic target salary range for your career stage.</p>"
        "<p>Prefer browsing by location? Explore <a href='/salary-by-state/'>salary by state</a> to compare regional differences.</p>"
    )
    by_state_intro = (
        "<h1>Salary by State</h1>"
        "<p>Location has a major impact on salary benchmarks. Two people with similar roles and experience can sit in very different "
        "percentiles depending on local labor markets and cost-of-living pressure. These state entry pages help you compare earnings "
        "for the same age and salary band across major states, then drill into a detailed percentile result page for each region. "
        "If you are considering relocation or remote work, this is a practical way to evaluate pay competitiveness instead of relying "
        "on one national number. You can also compare your state view against national data to decide whether your compensation is keeping pace.</p>"
        "<p>Prefer browsing by life stage first? Start with <a href='/salary-by-age/'>salary by age</a> and then refine by state.</p>"
    )
    best_ranges_intro = (
        "<h1>Best Salary Ranges</h1>"
        "<p>There is no one universal “best” salary, but some ranges tend to offer a stronger balance between purchasing power and percentile rank "
        "for a wide set of age groups. This guide helps you compare major salary bands and quickly branch into the exact percentile pages for your "
        "age and state. If your compensation is below your target band, use these pages to estimate the next step that meaningfully improves your rank. "
        "If your salary is already in a higher band, the same pages help you track progress against top-quartile and top-decile thresholds rather than "
        "guessing from national averages alone. Start with your closest bracket, then check nearby ranges to understand how much additional income is "
        "typically needed to move up in percentile terms for your stage of career.</p>"
        "<p>For a location-first comparison, review <a href='/salary-by-state/'>salary by state</a>. For life-stage trends, start at "
        "<a href='/salary-by-age/'>salary by age</a>.</p>"
    )
    return {
        "/salary-by-age/": f"{by_age_intro}<ul>{age_links}</ul>",
        "/salary-by-state/": f"{by_state_intro}<ul>{state_links}</ul>",
        "/best-salary-ranges/": f"{best_ranges_intro}<ul>{bucket_links}</ul>",
        "/is-75000-good-salary/": (
            "<h1>Is $75,000 a Good Salary?</h1>"
            "<p>A $75,000 salary typically sits around the upper-middle range for many working-age groups, but the exact percentile depends "
            "on your age and state. Use this quick hub to jump into salary percentile pages closest to $75K and see whether that income is "
            "near the median, top quartile, or top decile for your profile.</p>"
            "<ul>"
            "<li><a href='/salary-percentile/65k-80k/age/31-35/'>$65K–$80K at age 31–35 (national)</a></li>"
            "<li><a href='/salary-percentile/65k-80k/age/36-40/'>$65K–$80K at age 36–40 (national)</a></li>"
            "<li><a href='/salary-by-state/'>Compare by state</a></li>"
            "</ul>"
        ),
        "/average-salary-age-30/": (
            "<h1>Average Salary at Age 30</h1>"
            "<p>Age 30 usually falls in the 26–30 salary band. This entry page helps you compare average and percentile benchmarks for this age range, "
            "then branch into nearby age groups if you want a wider comparison. Use the pages below to check where your current income stands.</p>"
            "<ul>"
            "<li><a href='/salary-percentile/50k-65k/age/26-30/'>$50K–$65K at age 26–30</a></li>"
            "<li><a href='/salary-percentile/65k-80k/age/26-30/'>$65K–$80K at age 26–30</a></li>"
            "<li><a href='/salary-by-age/'>Browse all age-based salary pages</a></li>"
            "</ul>"
        ),
        "/salary-percentile-calculator/": (
            "<h1>Salary Percentile Calculator</h1>"
            "<p>Use our salary percentile pages as a no-login calculator: pick the closest salary band, your age group, and optionally your state. "
            "Each page includes an interactive percentile estimate plus distribution markers for median, quartiles, and top 10% thresholds.</p>"
            "<ul>"
            "<li><a href='/salary-percentile/65k-80k/age/31-35/'>Start with $65K–$80K at age 31–35</a></li>"
            "<li><a href='/salary-by-age/'>Browse by age</a></li>"
            "<li><a href='/salary-by-state/'>Browse by state</a></li>"
            "</ul>"
        ),
    }


def build_404_page() -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Page Not Found</title></head><body>"
        "<nav><a href='/'>Home</a> · <a href='/salary-by-age/'>Salary by age</a> · <a href='/salary-by-state/'>Salary by state</a></nav>"
        "<h1>404 — Page not found</h1>"
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


def discover_existing_paths(output_root: Path) -> list[str]:
    urls = set()
    for file_path in output_root.rglob("*"):
        if not file_path.is_file():
            continue
        rel = "/" + str(file_path.relative_to(output_root)).replace("\\", "/")
        if rel in ("/index.html",):
            urls.add("/")
        elif rel.endswith("/index.html"):
            urls.add(normalize_canonical(rel[: -len("index.html")]))
        elif rel.endswith(".xml") or rel.endswith(".txt") or rel.endswith(".html"):
            urls.add(rel)
    return sorted(urls)


def main():
    dry_run = "--dry-run" in sys.argv
    data = load_json_file(DATA_FILE, "master.json")
    config = load_json_file(CONFIG_FILE, "config.json")

    if "salary" not in data:
        raise ValueError("Missing salary key in master.json")

    salary = data["salary"]
    
    required_keys = ["buckets", "age_bands", "by_state", "national"]
    for key in required_keys:
        if key not in salary:
            raise ValueError(f"Missing salary.{key} in master.json")

    for k, v in DEFAULT_CONFIG_VALUES.items():
        config.setdefault(k, v)

    if INDEX_FILE.exists():
        page_index = load_json_file(INDEX_FILE, "page_index.json")
    else:
        page_index = {"config_version": None, "last_build": None, "pages": {}}

    if page_index.get("config_version") != config.get("version"):
        page_index["pages"] = {}

    buckets = salary["buckets"]
    age_bands = salary["age_bands"]
    states = [{"id": k, "name": v["name"], "abbr": v["abbr"]} for k, v in salary["by_state"].items()]

    env = Environment(loader=FileSystemLoader(str(TMPL_DIR)), undefined=StrictUndefined)
    template = env.get_template("salary.html")
    salary_data_json = build_salary_data_json(salary)
    template_version = hashlib.sha256((TMPL_DIR / "salary.html").read_bytes()).hexdigest()[:12]

    build_cfg = config.get("build", {})
    include_state_pages = bool(build_cfg.get("include_state_pages", True))
    state_subset_size = build_cfg.get("state_subset_size")
    state_ids = [s["id"] for s in states]

    if include_state_pages and isinstance(state_subset_size, int) and state_subset_size > 0:
        state_subset = state_ids[:state_subset_size]
    elif include_state_pages:
        state_subset = state_ids
    else:
        state_subset = []

    bucket_ids = [b["id"] for b in buckets]
    age_ids = [a["id"] for a in age_bands]
    max_pages = int(build_cfg.get("max_pages", DEFAULT_CONFIG_VALUES["build"]["max_pages"]))

    rendered_pages = {}
    skipped = []
    build_timestamp = datetime.utcnow().isoformat()

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

        is_valid, reason = validate_page_data(page_data, bucket, age_band, state_id)
        if not is_valid:
            skipped.append(f"skip {bucket['id']} {age_band['id']} {state_id or 'national'}: {reason}")
            return

        state_str = f" in {state_name}" if state_name else ""
        center_percentile = estimate_percentile_for_salary(bucket["mid"], page_data)

        meta_title = (
            f"Is ${bucket['mid']:,} a Good Salary at {age_band['label']}{state_str}? "
            f"(Top {center_percentile}%) | BenchmarkSelf"
        )

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
            "dataset_version": data.get("_meta", {}).get("version", config.get("version", "unknown")),
            "dataset_last_updated": data.get("_meta", {}).get("updated", config.get("dataset_last_updated")),
            "build_timestamp": build_timestamp,
            "intro_sentence": unique_intro_for_context(bucket, age_band, state_name),
            "template_version": template_version,
        }

        try:
            validate_template_context(ctx)
        except Exception as exc:
            skipped.append(f"skip {canonical}: {exc}")
            return

        try:
            html = minify_html_light(template.render(**ctx))
        except UndefinedError as exc:
            raise RuntimeError(f"Template rendering failed: {exc}")
        except Exception as exc:
            skipped.append(f"skip {canonical}: template render error: {exc}")
            return

        rendered_pages[canonical] = html

    # ── FIXED LOOP STRUCTURE ─────────────────────
    count = 0
    limit_hit = False

    bucket_map = {b["id"]: b for b in buckets}
    age_band_map = {a["id"]: a for a in age_bands}

    # NATIONAL
    for bucket_id in bucket_ids:
        if count >= max_pages:
            limit_hit = True
            break

        bucket = bucket_map.get(bucket_id)
        if not bucket:
            skipped.append(f"missing bucket {bucket_id}")
            continue

        for age_id in age_ids:
            if count >= max_pages:
                limit_hit = True
                break

            age_band = age_band_map.get(age_id)
            if not age_band:
                skipped.append(f"missing age_band {age_id}")
                continue

            queue_page(bucket, age_band)
            count += 1

    # STATE
    for state_id in state_subset:
        if count >= max_pages:
            limit_hit = True
            break

        for bucket_id in bucket_ids:
            if count >= max_pages:
                limit_hit = True
                break

            bucket = bucket_map.get(bucket_id)
            if not bucket:
                skipped.append(f"missing bucket {bucket_id}")
                continue

            for age_id in age_ids:
                if count >= max_pages:
                    limit_hit = True
                    break

                age_band = age_band_map.get(age_id)
                if not age_band:
                    skipped.append(f"missing age_band {age_id}")
                    continue

                queue_page(bucket, age_band, state_id)
                count += 1

    # ── REST UNCHANGED ─────────────────────
    if skipped:
        for msg in skipped:
            LOGGER.warning(msg)

    if limit_hit:
        LOGGER.info("Global page limit reached (%s pages).", max_pages)

    if len(rendered_pages) == 0:
        LOGGER.error("No pages rendered. Stopping build.")
        raise RuntimeError("Build failed before file writes")

    if TMP_OUT_DIR.exists():
        shutil.rmtree(TMP_OUT_DIR)
    TMP_OUT_DIR.mkdir(parents=True, exist_ok=True)

    generated_pages = {}
    for canonical, html in sorted(rendered_pages.items()):
        if not dry_run:
            write_file(TMP_OUT_DIR, canonical, html)
        generated_pages[canonical] = {"generated_at": build_timestamp}

    crawl_pages = build_crawl_entry_pages(buckets, age_bands, states)
    for canonical, body in crawl_pages.items():
        if not dry_run:
            write_file(
                TMP_OUT_DIR,
                canonical,
                minify_html_light(f"<!doctype html><html><head><meta charset='utf-8'><title>BenchmarkSelf</title></head><body>{body}</body></html>"),
            )

    root_index = minify_html_light(build_root_index(config, buckets, age_bands, states))
    if not dry_run:
        write_file(TMP_OUT_DIR, "/index.html", root_index)
        write_file(TMP_OUT_DIR, "/404.html", build_404_page())

    domain = validate_domain(config.get("domain"))
    robots_content = f"User-agent: *\nAllow: /\nSitemap: {domain}/sitemap.xml\n"

    if not dry_run:
        write_file(TMP_OUT_DIR, "/robots.txt", robots_content)

    all_paths = list(rendered_pages.keys()) + list(crawl_pages.keys()) + ["/"]
    sitemap_xml = build_sitemap_xml(all_paths, domain)

    if not dry_run:
        write_file(TMP_OUT_DIR, "/sitemap.xml", sitemap_xml)
        write_file(TMP_OUT_DIR, "/_headers",
                   "# Cloudflare Pages handles Brotli/Gzip compression automatically.\n/*\n  X-Content-Type-Options: nosniff\n")

    if not dry_run:
        if OUT_DIR.exists():
            shutil.rmtree(OUT_DIR)
        TMP_OUT_DIR.rename(OUT_DIR)
    elif TMP_OUT_DIR.exists():
        shutil.rmtree(TMP_OUT_DIR)

    page_index["pages"] = generated_pages
    page_index["config_version"] = config.get("version")
    page_index["last_build"] = datetime.utcnow().isoformat()

    if not dry_run:
        INDEX_FILE.write_text(json.dumps(page_index, indent=2), encoding="utf-8")

    if not dry_run:
        sitemap_paths = discover_existing_paths(OUT_DIR)
        sitemap_xml = build_sitemap_xml(
            [p for p in sitemap_paths if p not in ("/robots.txt", "/sitemap.xml", "/_headers", "/404.html")],
            domain
        )
        write_file(OUT_DIR, "/sitemap.xml", sitemap_xml)

    LOGGER.info("Generated %s salary pages", len(rendered_pages))
    LOGGER.info("Added %s crawl/entry pages + index + 404", len(crawl_pages))
    LOGGER.info("Sitemap contains %s canonical URLs", len(set(all_paths)))

    if dry_run:
        LOGGER.info("Dry run enabled: no output files were written.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BUILD FAILED: {exc}")
        sys.exit(1)
