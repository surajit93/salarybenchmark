# Indexing + Tracking Notes

<!-- SEO indexing launch checklist:
1) Submit https://bench.pages.dev/sitemap.xml to Google Search Console.
2) Request indexing for top entry pages:
   - /salary-by-age/
   - /salary-by-state/
   - /best-salary-ranges/
   - /is-75000-good-salary/
   - /average-salary-age-30/
   - /salary-percentile-calculator/
-->

<!-- Tracking backend prep (Cloudflare Worker):
- Accept POST /track with JSON body.
- Capture geo metadata from request.cf (country/region/city).
- Store events in KV (fast counters) or R2 (raw event logs).
- Return 204 quickly; no blocking logic on edge path.
-->
