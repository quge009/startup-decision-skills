"""M2-Step-10.3 v0.3: collect chip funding events into per-company JSON cache.

Split from build_funding_events_v03.py: this script has one job — for each
requested company, run Tavily queries, extract funding rounds with the LLM, and
write `<slug>.json` (a list of round dicts) under CACHE_DIR. It does NOT
produce the parquet output; run build_funding_events_v03.py separately for that.

Splitting collect from build fixes the design flaw exposed by the 2026-08-12
cache-loss incident: the previous single-script build rewrote the parquet in
full on every run, so any cache deletion (accidental or otherwise) shrank the
parquet to whatever was left on disk. Now the parquet builder reads all slug
files it finds on disk, and this collector only ever adds/overwrites one slug at
a time.

Usage:
    export TAVILY_API_KEY=...
    export OPENROUTER_API_KEY=...
    python3 scripts/collect_funding_events_v03.py \\
        --slugs cerebras-systems-inc,sambanova-systems,lightmatter,graphcore,syntiant \\
        [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from schema_contract_loader import load_event_type_contract

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas/funding_events_v0.3.schema.json"
EVENT_TYPES, EVENT_TYPE_DEFINITIONS = load_event_type_contract(SCHEMA_PATH)
EVENT_TYPE_GUIDE = "\n".join(
    f"    {event_type}: {EVENT_TYPE_DEFINITIONS[event_type]}"
    for event_type in EVENT_TYPES
)


DATA_ROOT = Path(os.environ.get("INVESTOR_BEHAVIOR_DATA_DIR", "~/investor-behavior-analysis")).expanduser()
COMPANY_ENTITY_PATH = DATA_ROOT / "companies_chip_subset_entity_v0.3.parquet"
CACHE_DIR = DATA_ROOT / "raw/funding_events_chip_v03"

# Multi-query strategy: each query targets a distinct funding-round subset so
# Tavily's top-8 spans the round-name space rather than being crowded by the
# same "most recent round" news. Same design as exposure/interface collectors.
QUERY_TEMPLATES = [
    '"{company}" "Series A" OR "Series B" funding',
    '"{company}" "Series C" OR "Series D" funding',
    '"{company}" "Series E" OR "Series F" funding',
    '"{company}" "Series G" OR "Series H" OR "Series I" funding',
    '"{company}" seed OR "pre-seed" round',
    '"{company}" raised OR "closes round" OR "funding round" million',
]

SYSTEM_PROMPT = (
    "You are extracting structured funding-level events from news article "
    "snippets. Only report events actually stated in the snippets. "
    "Do NOT fabricate. If the snippets don't mention any relevant events, "
    "return an empty array [].\n"
    "\n"
    "Include BOTH successful funding rounds AND non-round funding-level "
    "outcomes: publicly reported failed rounds, IPO filings/completions/"
    "withdrawals, acquisitions, and company closures.\n"
    "\n"
    "For each distinct event, emit one JSON object with fields:\n"
    "  - event_type: one of "
    f"{EVENT_TYPES}. Apply these authoritative definitions:\n{EVENT_TYPE_GUIDE}\n"
    "  - round_name: string. For funding_round / funding_failed / funding_withdrawn, "
    "one of [\"Seed\", \"Pre-Seed\", \"Series A\", \"Series B\", \"Series C\", "
    "\"Series D\", \"Series E\", \"Series F\", \"Series G\", \"Series H\", "
    "\"Growth\", \"Convertible\", \"Debt\", \"Bridge\", \"Secondary\", \"unknown\"]. "
    "null for ipo_* / acquired / company_closed.\n"
    "  - announce_date: ISO date string (YYYY-MM-DD). null if not stated.\n"
    "  - amount_usd: integer USD. For funding_round: round total. For "
    "acquired: deal value. For ipo_completed: valuation at IPO. null if not stated.\n"
    "  - lead_investor: string. Applicable to funding_round / funding_failed / "
    "funding_withdrawn. null otherwise.\n"
    "  - co_investors: array of strings. Empty [] if none. Applicable to "
    "funding_round / funding_failed / funding_withdrawn.\n"
    "  - acquirer: string. Applicable to \"acquired\" event_type. null otherwise.\n"
    "  - confidence: number in [0.0, 1.0].\n"
    "  - source_url: string or null. Copy the Source URL attached to the "
    "supporting snippet; never invent a URL.\n"
    "\n"
    "Return ONLY a JSON array. No explanation, no markdown.\n"
)

def _load_openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return key
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")


def web_search_funding(company_name: str) -> list[dict]:
    """Run every query in QUERY_TEMPLATES and return the union of results,
    de-duplicated by URL (first-seen wins so ordering is reproducible)."""
    if "TAVILY_API_KEY" not in os.environ:
        raise RuntimeError("TAVILY_API_KEY is not set")
    all_results = []
    seen_urls = set()
    for tmpl in QUERY_TEMPLATES:
        query = tmpl.format(company=company_name)
        for attempt in range(3):
            try:
                r = subprocess.run(
                    ["tvly", "search", query, "--include-raw-content", "markdown",
                     "--max-results", "8", "--json"],
                    capture_output=True, text=True, timeout=90,
                )
                if r.returncode != 0:
                    time.sleep(2)
                    continue
                results = json.loads(r.stdout).get("results", [])
                for res in results:
                    url = res.get("url", "")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    all_results.append(res)
                break
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                time.sleep(2)
    # Identity filter: keep only results whose title/content/raw_content
    # mention the target company (case-insensitive). Prevents LLM from
    # attributing events from semantically-drifted results (Vaderis
    # Therapeutics, Valthos, etc.) to the target company.
    key = company_name.lower()
    return [r for r in all_results
            if key in ((r.get("title") or "") + " " +
                       (r.get("content") or "") + " " +
                       (r.get("raw_content") or "")).lower()]


def extract_rounds_from_snippets(company: str, snippets: list[str]) -> list[dict]:
    if not snippets:
        return []
    user_content = f'Company: "{company}"\n\nSearch result snippets:\n'
    for i, s in enumerate(snippets, 1):
        user_content += f"\n[{i}] {s}\n"

    body = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "reasoning": {"enabled": False},
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_load_openrouter_api_key()}",
    }
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            content = (result["choices"][0]["message"].get("content") or "").strip()
            if not content:
                print(f"    LLM returned empty content (finish_reason={result['choices'][0].get('finish_reason')})")
                return []
            if content.startswith("```"):
                content = content.split("```", 2)[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip().rstrip("`").strip()
            arr = json.loads(content)
            return arr if isinstance(arr, list) else []
    except Exception as e:
        print(f"    LLM extract failed: {type(e).__name__}: {str(e)[:120]}")
        return []


def load_cached_rounds(company_slug: str) -> list[dict] | None:
    cache_file = CACHE_DIR / f"{company_slug}.json"
    if not cache_file.exists():
        return None
    try:
        return json.loads(cache_file.read_text())
    except Exception:
        return None


def save_cached_rounds(company_slug: str, rounds: list[dict]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{company_slug}.json").write_text(
        json.dumps(rounds, indent=2, ensure_ascii=False))


def web_search_funding_serper(company_name: str) -> list[dict]:
    """Serper backend — search + scrape top results for full content."""
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is not set")
    search_url = "https://google.serper.dev/search"
    scrape_url = "https://scrape.serper.dev"

    all_results = []
    seen_urls = set()
    for tmpl in QUERY_TEMPLATES:
        query = tmpl.format(company=company_name)
        body = json.dumps({"q": query, "num": 8}).encode()
        req = urllib.request.Request(search_url, data=body, headers={
            "X-API-KEY": api_key, "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                for r in data.get("organic", []):
                    url = r.get("link", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append({
                            "url": url, "title": r.get("title", ""),
                            "content": r.get("snippet", ""), "raw_content": "",
                        })
                break
            except urllib.error.HTTPError as e:
                if e.code == 402:
                    print("\n    *** SERPER CREDITS EXHAUSTED (HTTP 402) — stopping. ***")
                    sys.exit(1)
                time.sleep(2)
            except Exception:
                time.sleep(2)
        time.sleep(0.5)

    # Identity filter
    key = company_name.lower()
    filtered = [r for r in all_results
                if key in ((r.get("title") or "") + " " +
                           (r.get("content") or "")).lower()]

    # Scrape top results for full content
    for r in filtered[:10]:
        body = json.dumps({"url": r["url"]}).encode()
        req = urllib.request.Request(scrape_url, data=body, headers={
            "X-API-KEY": api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                scrape_data = json.loads(resp.read().decode())
                r["raw_content"] = scrape_data.get("text", "")
        except urllib.error.HTTPError as e:
            if e.code == 402:
                print("\n    *** SERPER CREDITS EXHAUSTED (HTTP 402) — stopping. ***")
                sys.exit(1)
        except Exception:
            pass
        time.sleep(0.5)

    return filtered


def main():
    global CACHE_DIR

    ap = argparse.ArgumentParser(description="Collect funding events into per-company cache")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--slugs", type=str, default=None,
                    help="Comma-separated slugs (overrides --limit)")
    ap.add_argument("--force", action="store_true",
                    help="Re-collect (overwrite existing cache) even when a slug.json is present")
    ap.add_argument("--search-backend", type=str, default="tavily",
                    choices=["tavily", "serper"],
                    help="Search API backend: 'tavily' (default, requires TAVILY_API_KEY) or 'serper' (requires SERPER_API_KEY)")
    ap.add_argument("--entity-path", type=str, default=str(COMPANY_ENTITY_PATH),
                    help="Path to company entity parquet (default: companies_chip_subset_entity_v0.3.parquet)")
    ap.add_argument("--cache-dir", type=str, default=str(CACHE_DIR),
                    help="Directory for per-company JSON cache")
    args = ap.parse_args()

    CACHE_DIR = Path(args.cache_dir).expanduser()
    _search_dispatch = {
        "tavily": web_search_funding,
        "serper": web_search_funding_serper,
    }
    do_search = _search_dispatch[args.search_backend]
    print(f"Using search backend: {args.search_backend}")

    co_df = pq.read_table(args.entity_path).to_pandas().sort_values(
        "edge_count", ascending=False)
    if args.slugs:
        wanted = {s.strip() for s in args.slugs.split(",") if s.strip()}
        co_df = co_df[co_df["slug"].isin(wanted)]
        missing = wanted - set(co_df["slug"])
        if missing:
            print(f"WARNING: slugs not found: {sorted(missing)}")
    elif args.limit:
        co_df = co_df.head(args.limit)

    print(f"Collecting funding events for {len(co_df)} companies")
    print(f"Cache dir: {CACHE_DIR}")

    for idx, (_, co) in enumerate(co_df.iterrows()):
        company_name = co["company_canonical_name"]
        slug = co["slug"]

        if not args.force:
            cached = load_cached_rounds(slug)
            if cached is not None:
                print(f"  [{idx+1}] {company_name[:34]:<34} → cached ({len(cached)} rounds)")
                continue

        print(f"  [{idx+1}] {company_name[:34]:<34} searching ...")
        search_results = do_search(company_name)
        if not search_results:
            print(f"        no snippets")
            save_cached_rounds(slug, [])
            continue

        snippets = [
            f"Source URL: {r.get('url') or 'unknown'}\n"
            f"Content:\n{r.get('raw_content') or r.get('content') or ''}"
            for r in search_results
        ]
        rounds = extract_rounds_from_snippets(company_name, snippets)
        for event in rounds:
            event.setdefault("source_type", args.search_backend)
        save_cached_rounds(slug, rounds)
        print(f"        → {len(rounds)} rounds")
        time.sleep(1)


if __name__ == "__main__":
    main()
