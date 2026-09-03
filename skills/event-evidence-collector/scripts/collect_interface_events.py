"""Collect investor-company interface events into a JSON evidence cache.

This script has one job: for each
requested company, run Tavily queries, extract events with the LLM, and write
`<slug>.json` under CACHE_DIR. It does NOT produce the parquet output; run
the interface-event builder separately for that.

Usage:
    export TAVILY_API_KEY=...
    export OPENROUTER_API_KEY=...
    python3 scripts/collect_interface_events.py \\
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

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas/interface_events_v0.3.schema.json"
EVENT_TYPES, EVENT_TYPE_DEFINITIONS = load_event_type_contract(SCHEMA_PATH)
EVENT_TYPE_GUIDE = "\n".join(
    f"    {event_type}: {EVENT_TYPE_DEFINITIONS[event_type]}"
    for event_type in EVENT_TYPES
)


CACHE_DIR = Path(".")

QUERY_TEMPLATES = [
    '"{company}" (board of directors OR director appointed OR chairman)',   # board_change
    '"{company}" (major shareholder OR stake sold OR "sold shares")',       # major_holder_change
    '"{company}" (secondary transaction OR tender offer OR "share sale")',  # secondary_transaction
    '"{company}" (activist investor OR proxy fight OR "campaign")',         # activist_action
    '"{company}" (strategic investor OR minority stake OR corporate venture)', # strategic_investment
    '"{company}" (investor OR leads OR participates OR subscription) (financing OR "funding round" OR "Series A" OR "Series B" OR "Series C")', # funding_participation
    '"{company}" (legal adviser OR legal advisor OR counsel OR underwriter OR auditor OR consultant)', # service_provider
    '"{company}" (exit OR sold OR acquired OR "acquired by")',              # exit
]


SYSTEM_PROMPT = (
    "You are extracting structured investor↔company interface events from news "
    "article snippets. Only report events actually described in the snippets. "
    "Do NOT fabricate. If the snippets don't mention any relevant events, "
    "return an empty array [].\n"
    "\n"
    "An interface event is a discrete relationship between the company and a "
    "specific investor, shareholder, or professional service provider. Examples: "
    "participating in or leading a primary funding round, appointing or removing "
    "a director, selling a stake on the secondary market, an activist campaign, "
    "a strategic partner taking a minority position, providing legal or advisory "
    "services, or an IPO/acquisition exit. Ordinary numbered-round participation "
    "is funding_participation, not strategic_investment.\n"
    "\n"
    "For each distinct event, emit one JSON object with fields:\n"
    "  - event_date: ISO date string (YYYY-MM-DD, or YYYY-MM). null if not stated.\n"
    "  - event_type: one of "
    f"{EVENT_TYPES}. Apply these authoritative definitions:\n{EVENT_TYPE_GUIDE}\n"
    "  - event_subtype: optional finer free-text label. null if unclear.\n"
    "  - raw_investor_name: the investor, shareholder, service provider, or other "
    "interface party mentioned in this event, exactly as it appears in the snippet. "
    "null only if the event is about the investor base as a whole.\n"
    "  - title: short headline (<=80 chars). Never null.\n"
    "  - summary: 1-3 sentence description grounded in the snippet. Never null.\n"
    "  - source_url: the URL of the snippet the event is drawn from. null if unclear.\n"
    "  - confidence: number in [0.0, 1.0].\n"
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


def web_search(company_name: str) -> list[dict]:
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


def extract_events(company: str, snippets: list[dict]) -> list[dict]:
    if not snippets:
        return []
    user_content = f'Company: "{company}"\n\nSearch result snippets:\n'
    for i, s in enumerate(snippets, 1):
        url = s.get("url", "")
        text = s.get("raw_content") or s.get("content") or ""
        user_content += f'\n[{i}] URL: {url}\n{text}\n'

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


def load_cache(slug: str):
    f = CACHE_DIR / f"{slug}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def save_cache(slug: str, events: list[dict]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{slug}.json").write_text(json.dumps(events, indent=2))


def web_search_serper(company_name: str) -> list[dict]:
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

    ap = argparse.ArgumentParser(description="Collect interface events into per-company cache")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--slugs", type=str, default=None,
                    help="Comma-separated slugs (overrides --limit)")
    ap.add_argument("--force", action="store_true",
                    help="Re-collect (overwrite existing cache) even when a slug.json is present")
    ap.add_argument("--search-backend", type=str, default="tavily",
                    choices=["tavily", "serper"],
                    help="Search API backend: 'tavily' (default, requires TAVILY_API_KEY) or 'serper' (requires SERPER_API_KEY)")
    ap.add_argument("--entity-path", type=str, required=True,
                    help="Path to the company entity Parquet")
    ap.add_argument("--cache-dir", type=str, required=True,
                    help="Directory for per-company JSON cache")
    args = ap.parse_args()

    CACHE_DIR = Path(args.cache_dir).expanduser()
    _search_dispatch = {
        "tavily": web_search,
        "serper": web_search_serper,
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

    print(f"Collecting interface events for {len(co_df)} companies")
    print(f"Cache dir: {CACHE_DIR}")

    for idx, (_, co) in enumerate(co_df.iterrows()):
        company_name = co["company_canonical_name"]
        slug = co["slug"]

        if not args.force:
            cached = load_cache(slug)
            if cached is not None:
                print(f"  [{idx+1}] {company_name[:34]:<34} → cached ({len(cached)} events)")
                continue

        print(f"  [{idx+1}] {company_name[:34]:<34} searching ...")
        snippets = do_search(company_name)
        if not snippets:
            print(f"        no snippets")
            save_cache(slug, [])
            continue

        events = extract_events(company_name, snippets)
        for e in events:
            e.setdefault("source_type", args.search_backend)
        save_cache(slug, events)
        print(f"        → {len(events)} events")


if __name__ == "__main__":
    main()
