#!/usr/bin/env python3
"""Apply caller-supplied selector YAMLs to cached portfolio-page HTML.

The command reads an explicit firm-list YAML and selector directory, resolves
cached HTML below DATA_ROOT, and emits one schema-conformant Parquet per firm.

Design:
- Image-URL names (`.../databricks-horizontal.webp`) are
  parsed into clean company names via URL-basename heuristic (basename → drop
  image extension → split on `-` or `_` → take first segment). Applied whenever
  the raw extracted name starts with `http://` or `https://`.
- `company_normalized_name` (via _common.normalize_name) and
  `company_domain` (via _common.extract_domain from company_url) are computed
  here so downstream merge and feature workflows read canonical form directly.
- Sanity-check row count vs `sanity_checks.min_edges/max_edges` in YAML;
  on violation, print a warning and continue (do NOT block the run — one bad
  firm does not stop unrelated firms).
- Pure Python execution: no LLM or container is required.

Idempotent: overwrites per-firm parquet on re-run (per edges_long_v0.1 spec Q4
policy — latest scrape wins; audit history via raw HTML snapshots).

Run with ``--top20 FIRMS.yaml --selectors-dir SELECTORS`` and optional firm/date
filters. The historical option name ``--top20`` accepts any bounded firm list.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
import pyarrow as pa
import pyarrow.parquet as pq
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import normalize_name, extract_domain


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_VERSION = "v0.1.0"

# Position enum (per edges_long_v0.1.spec.md §Position value definitions)
POSITION_ENUM = {"active", "exited", "acquired", "ipo", "closed"}

# Image file extensions we strip when name came from an image URL
IMAGE_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico")


# ─────────────────────────────────────────────────────────────────────────
# URL-as-name extraction (Q1-A: Insight-style image-URL rescue)
# ─────────────────────────────────────────────────────────────────────────


# Modifier tokens commonly appended to logo filenames — filtered from name parsing.
# Case-insensitive match. Keep small + well-known — over-filtering risks eating
# legitimate name parts (e.g. don't add "capital" here or we'd strip VC firm names).
URL_NAME_MODIFIERS = {
    "logo", "logotype", "logomark", "wordmark", "brandmark",
    "transparent", "horizontal", "vertical", "square", "landscape",
    "dark", "light", "black", "white", "primary", "secondary",
    "ko", "bg", "nobg", "sq", "cropped",
    "small", "medium", "large", "big", "hi", "lo", "hd",
    "final", "v1", "v2", "v3",
}

# Year token pattern (20XX) and hash-like token pattern (mostly digits, 8+ chars)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_HASH_LIKE_RE = re.compile(r"^[a-zA-Z]?\d{6,}$")  # e.g. e1568058016375 or 20230101


def _is_modifier_token(tok: str) -> bool:
    """True if this URL-filename token looks like a modifier, not part of the name."""
    if not tok:
        return True
    lower = tok.lower()
    if lower in URL_NAME_MODIFIERS:
        return True
    if _YEAR_RE.match(tok):
        return True
    if _HASH_LIKE_RE.match(tok):
        return True
    # Digit-only tokens (like "2" from "zest_logo_2.png")
    if tok.isdigit():
        return True
    return False


def _clean_url_to_name(raw: str) -> str:
    """Insight-style rescue: parse a logo-image URL into the underlying company name.

    Rule: basename → drop image extension → split on `-` or `_` → filter out
    modifier tokens (logo / transparent / horizontal / dark / year / hash-like /
    digit-only) → join remaining with `_`. Falls back to full basename stem if
    filtering removes everything.

    Examples (from Insight):
      databricks-horizontal.webp    → databricks
      OpenAI-Logo-2022.png          → OpenAI
      Wiz.png                       → Wiz
      Anthropic_logo.png            → Anthropic
      Flank_Logotype_Black.png      → Flank
      writer-KO.png                 → writer
      Calm-e1568058016375.png       → Calm
      crew_ai_transparent.png       → crew_ai      (multi-word preserved)
      weights-biases.png            → weights_biases (multi-word preserved)
      zest_logo_2.png               → zest
    """
    if not raw:
        return raw
    p = urlparse(raw)
    # Strip trailing slash before basename — IVP / NEA use path-only hrefs
    # like "/portfolio/perplexity/" which os.path.basename returns as ""
    # otherwise, leaving raw name unhandled.
    basename = os.path.basename(p.path.rstrip("/"))
    stem = basename
    for ext in IMAGE_EXTS:
        if stem.lower().endswith(ext):
            stem = stem[:-len(ext)]
            break
    parts = re.split(r"[-_]", stem)
    # Keep non-modifier tokens; preserve their original case
    kept = [t for t in parts if not _is_modifier_token(t)]
    if not kept:
        # All tokens were modifiers — fall back to full stem
        return stem or raw
    return "_".join(kept)


def _is_url(s: str) -> bool:
    # Also detect path-only hrefs such as "/portfolio/foo/".
    # (IVP / NEA use extract:attribute:href which yields relative paths on
    # logo-only portfolio pages). Without this, path-only names bypassed
    # _clean_url_to_name and leaked "portfolio perplexity"-style noise into
    # edges_long.company_normalized_name.
    return isinstance(s, str) and (
        s.startswith("http://") or s.startswith("https://") or s.startswith("/")
    )


# ─────────────────────────────────────────────────────────────────────────
# HTML path resolution
# ─────────────────────────────────────────────────────────────────────────


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_html_path(slug: str, data_root: Path, date_str: str | None = None) -> Path:
    """Return the cached HTML path for a firm. If `date_str` given use that;
    else pick the newest YYYY-MM-DD subdir containing this firm's html."""
    base = data_root / "raw" / "portfolio_html"
    if not base.exists():
        raise FileNotFoundError(f"HTML cache root missing: {base}")
    if date_str:
        candidate = base / date_str / f"{slug}.html"
        if not candidate.exists():
            raise FileNotFoundError(f"HTML missing for slug={slug} date={date_str}: {candidate}")
        return candidate
    # Auto-resolve newest
    date_dirs = sorted(
        (d for d in base.iterdir() if d.is_dir() and DATE_RE.match(d.name)),
        reverse=True,
    )
    for d in date_dirs:
        p = d / f"{slug}.html"
        if p.exists():
            return p
    raise FileNotFoundError(f"HTML missing for slug={slug} in all date subdirs under {base}")


# ─────────────────────────────────────────────────────────────────────────
# Per-firm extraction
# ─────────────────────────────────────────────────────────────────────────


def _extract_value(el, extract_spec: str) -> str:
    """Apply an extract spec (`text` / `attribute:X`) to a BS4 element."""
    if el is None:
        return ""
    if extract_spec == "text":
        return el.get_text(strip=True)
    if extract_spec.startswith("attribute:"):
        attr = extract_spec.split(":", 1)[1]
        return (el.get(attr) or "").strip()
    return el.get_text(strip=True)


def _extract_field(entry, field_config: dict | None, multi_default: bool = False):
    """Extract one field per its config: {selector, extract, multi?, value_map?}.

    Returns str, list[str], or None depending on multi flag.
    """
    if not field_config or field_config.get("selector") is None:
        return None
    selector = field_config["selector"]
    extract_spec = field_config.get("extract", "text")
    multi = field_config.get("multi", multi_default)
    value_map = field_config.get("value_map", {})

    if multi:
        els = entry.select(selector)
        values = [_extract_value(el, extract_spec) for el in els]
        values = [v for v in values if v]
        if value_map:
            values = [value_map.get(v, v) for v in values]
        return values if values else None
    else:
        el = entry.select_one(selector)
        v = _extract_value(el, extract_spec)
        if not v:
            return None
        if value_map:
            v = value_map.get(v, v)
        return v


def _load_selector_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_top20(top20_path: Path) -> list[dict]:
    with open(top20_path) as f:
        return (yaml.safe_load(f) or {}).get("firms", [])


def extract_firm_edges(
    firm_meta: dict,
    selector_cfg: dict,
    html_path: Path,
    scraped_at: dt.date,
) -> tuple[list[dict], dict]:
    """Return (rows, stats). Rows conform to edges_long_v0.1 spec (15 cols).

    `stats` includes: entries_before_dedup, entries_after_dedup, name_url_rescued,
    sanity_violated_hi/lo (booleans).
    """
    investor_id = firm_meta["investor_id"]
    investor_name = firm_meta.get("canonical_name") or investor_id
    portfolio_url = firm_meta.get("portfolio_url") or ""

    selectors = selector_cfg.get("selectors", {})
    entry_selector = selectors.get("entry_selector")
    fields = selectors.get("fields", {})
    version = str(selector_cfg.get("version", "v0.1"))
    confidence = float(selector_cfg.get("confidence", 0.90))
    sanity = selector_cfg.get("sanity_checks", {}) or {}
    min_edges = int(sanity.get("min_edges", 1))
    max_edges = int(sanity.get("max_edges", 100_000))

    if entry_selector is None:
        return [], {
            "entries_before_dedup": 0,
            "entries_after_dedup": 0,
            "name_url_rescued": 0,
            "sanity_violated_lo": False,
            "sanity_violated_hi": False,
            "skipped": True,
            "skip_reason": "entry_selector is null",
        }

    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    entries = soup.select(entry_selector)

    rows_by_edge_id: dict[str, dict] = {}
    name_url_rescued = 0
    for entry in entries:
        # Required: name
        raw_name = _extract_field(entry, fields.get("company_name_raw"))
        if not raw_name:
            continue
        # Optional: URL, industry, stage, position
        company_url = _extract_field(entry, fields.get("company_url"))
        industry = _extract_field(entry, fields.get("industry"), multi_default=True)
        stage = _extract_field(entry, fields.get("stage_at_investment"), multi_default=True)
        position = _extract_field(entry, fields.get("position"))

        # Normalize industry / stage into list<str>
        if isinstance(industry, str):
            industry = [industry] if industry else None
        if isinstance(stage, str):
            stage = [stage] if stage else None

        # Position enum validation
        if position is not None and position not in POSITION_ENUM:
            position = None  # drop non-enum value silently

        # Q1-A: URL-as-name rescue (Insight image-URL case)
        if _is_url(raw_name):
            cleaned = _clean_url_to_name(raw_name)
            name_url_rescued += 1
            company_name_raw = cleaned
            # If URL wasn't already captured as company_url, capture it
            if not company_url:
                company_url = raw_name
        else:
            company_name_raw = raw_name

        # Q2-A: derived fields — normalized_name (via _common) + domain
        company_normalized_name = normalize_name(company_name_raw)
        company_domain = extract_domain(company_url) if company_url else None

        edge_id = f"{investor_id}::{company_normalized_name}"

        # Dedup within firm on edge_id — union list fields if seen
        if edge_id in rows_by_edge_id:
            existing = rows_by_edge_id[edge_id]
            # Union industry / stage lists
            for k, new_v in (("industry", industry), ("stage_at_investment", stage)):
                if new_v:
                    old = existing.get(k) or []
                    for x in new_v:
                        if x not in old:
                            old.append(x)
                    existing[k] = old
            # Prefer non-null company_url / position
            if not existing.get("company_url") and company_url:
                existing["company_url"] = company_url
                existing["company_domain"] = company_domain
            if not existing.get("position") and position:
                existing["position"] = position
            continue

        rows_by_edge_id[edge_id] = {
            "edge_id":                 edge_id,
            "investor_id":             investor_id,
            "investor_name":           investor_name,
            "company_name_raw":        company_name_raw,
            "company_normalized_name": company_normalized_name,
            "company_url":             company_url,
            "company_domain":          company_domain,
            "stage_at_investment":     stage,
            "industry":                industry,
            "position":                position,
            "source_page_url":         portfolio_url,
            "selector_config_version": version,
            "scraped_at":              scraped_at,
            "schema_version":          SCHEMA_VERSION,
            "confidence":              confidence,
        }

    rows = list(rows_by_edge_id.values())

    # Q3-B: sanity check → warn but don't block
    n = len(rows)
    sanity_lo = n < min_edges
    sanity_hi = n > max_edges

    stats = {
        "entries_before_dedup": len(entries),
        "entries_after_dedup": n,
        "name_url_rescued": name_url_rescued,
        "sanity_violated_lo": sanity_lo,
        "sanity_violated_hi": sanity_hi,
        "skipped": False,
        "skip_reason": "",
    }
    return rows, stats


# ─────────────────────────────────────────────────────────────────────────
# Parquet emission
# ─────────────────────────────────────────────────────────────────────────


def build_schema() -> pa.Schema:
    return pa.schema([
        ("edge_id",                 pa.string()),
        ("investor_id",             pa.string()),
        ("investor_name",           pa.string()),
        ("company_name_raw",        pa.string()),
        ("company_normalized_name", pa.string()),
        ("company_url",             pa.string()),
        ("company_domain",          pa.string()),
        ("stage_at_investment",     pa.list_(pa.string())),
        ("industry",                pa.list_(pa.string())),
        ("position",                pa.string()),
        ("source_page_url",         pa.string()),
        ("selector_config_version", pa.string()),
        ("scraped_at",              pa.date32()),
        ("schema_version",          pa.string()),
        ("confidence",              pa.float32()),
    ])


def write_per_firm_parquet(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    schema = build_schema()
    table = pa.Table.from_pylist(rows, schema=schema) if rows else pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in schema], schema=schema,
    )
    pq.write_table(table, out_path, compression="snappy")


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--firm", help="Only process one firm (slug); default: all 20")
    ap.add_argument("--top20", required=True,
                    help="Path to caller-supplied investor/portfolio metadata YAML")
    ap.add_argument("--selectors-dir", required=True,
                    help="Directory containing caller-supplied selector YAML files")
    ap.add_argument("--data-root", required=True,
                    help="Root containing cached HTML and receiving extracted Parquets")
    ap.add_argument("--date", default=None,
                    help="HTML cache date subdir (default: auto-resolve newest)")
    args = ap.parse_args()

    top20_path = Path(args.top20)
    selectors_dir = Path(args.selectors_dir)
    data_root = Path(args.data_root)
    scraped_at = dt.date.today()

    firms = _load_top20(top20_path)
    if args.firm:
        firms = [f for f in firms if f["investor_id"].split(":", 1)[1] == args.firm]
        if not firms:
            print(f"ERROR: firm slug {args.firm!r} not found in {top20_path}", file=sys.stderr)
            return 2

    print(f"[input] {len(firms)} firm(s) to process from {top20_path}")

    all_stats: list[dict] = []
    total_rows = 0
    n_skipped = 0
    n_sanity_warn = 0

    for firm_meta in firms:
        slug = firm_meta["investor_id"].split(":", 1)[1]
        selector_yaml = selectors_dir / f"{slug}.yaml"
        if not selector_yaml.exists():
            print(f"  [skip] {slug}: selector yaml missing at {selector_yaml}")
            n_skipped += 1
            all_stats.append({"slug": slug, "status": "no_selector_yaml", "entries": 0})
            continue

        try:
            selector_cfg = _load_selector_yaml(selector_yaml)
        except Exception as e:
            print(f"  [skip] {slug}: selector yaml parse error: {e!r}")
            n_skipped += 1
            all_stats.append({"slug": slug, "status": "yaml_parse_error", "entries": 0})
            continue

        try:
            html_path = resolve_html_path(slug, data_root, args.date)
        except FileNotFoundError as e:
            print(f"  [skip] {slug}: {e}")
            n_skipped += 1
            all_stats.append({"slug": slug, "status": "no_html", "entries": 0})
            continue

        rows, stats = extract_firm_edges(firm_meta, selector_cfg, html_path, scraped_at)

        out_path = data_root / "extracted" / "edges_by_source" / f"{slug}_v0.1.parquet"
        write_per_firm_parquet(rows, out_path)

        rescue_note = f" [name_url_rescued={stats['name_url_rescued']}]" if stats["name_url_rescued"] else ""
        if stats["skipped"]:
            print(f"  [skip] {slug}: {stats['skip_reason']}")
            n_skipped += 1
            status = "skip_null_selector"
        else:
            entries = stats["entries_after_dedup"]
            total_rows += entries
            status = "ok"
            if stats["sanity_violated_lo"] or stats["sanity_violated_hi"]:
                n_sanity_warn += 1
                lo_hi = "LO" if stats["sanity_violated_lo"] else "HI"
                sanity = selector_cfg.get("sanity_checks", {}) or {}
                bounds = f"[{sanity.get('min_edges', '?')}, {sanity.get('max_edges', '?')}]"
                status = f"ok_sanity_warn_{lo_hi}"
                print(f"  [warn] {slug}: entries={entries} outside sanity {bounds} ({lo_hi}) — continuing (Q3-B policy)")
            print(f"  [wrote] {slug}: {entries} edges → {out_path.name}{rescue_note}")

        all_stats.append({"slug": slug, "status": status, "entries": stats["entries_after_dedup"]})

    # Summary
    print()
    print("=" * 70)
    print(f"[summary] {len(firms)} firm(s) processed:")
    print(f"  total edges written: {total_rows}")
    print(f"  skipped: {n_skipped}")
    print(f"  sanity warnings: {n_sanity_warn}")
    print()
    for s in all_stats:
        print(f"  {s['slug']:35s}  status={s['status']:25s}  entries={s['entries']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
