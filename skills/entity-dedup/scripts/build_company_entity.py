#!/usr/bin/env python3
"""Build a company entity table from portfolio edges.

Traverses all edges_by_source parquet files, deduplicates company names
(via company_normalized_name), assigns sequential company_id in format
co:<8-digit-seq>:<slug>, and outputs companies_entity_v0.2.parquet.

Company ID assignment:
- Sequence number assigned by first-encounter order (iterating edges files
  sorted alphabetically, edges within file by row order)
- Format: co:0000-0001:<slug> (8-digit with hyphen, e.g. co:0000-0001:apple)
- Slug from make_slug(canonical_name)

Dedup strategy:
- Primary key: company_normalized_name (lowercase, stripped of punctuation)
- Suffix stripping: inc/corp/ltd/plc/llc etc. → same entity
- First-seen raw name kept as canonical display name
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from _common import make_slug

# Legal suffix stripping for company dedup
LEGAL_SUFFIXES = frozenset({
    "inc", "corp", "ltd", "plc", "llc", "lp", "sa", "ag", "nv", "bv",
    "co", "company", "corporation", "limited", "group", "holdings",
    "international", "global", "industries",
})


def normalize_company_name(raw: str) -> str:
    """Normalize company name for dedup: lowercase, strip punctuation + legal suffixes."""
    if not raw:
        return ""
    s = raw.strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip trailing legal suffixes
    tokens = s.split()
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) if tokens else s


def make_company_id(seq: int, slug: str) -> str:
    """Generate company_id: co:XXXX-XXXX:<slug>"""
    high = seq // 10000
    low = seq % 10000
    return f"co:{high:04d}-{low:04d}:{slug}"


def _is_garbage(raw: str, dedup_key: str) -> bool:
    """Filter garbage entries that aren't real company names."""
    # Pure numbers or year-like (2010, 2010年)
    stripped = re.sub(r'[年月日]', '', dedup_key)
    if stripped.isdigit():
        return True
    # Very short (single char or 2 chars) after dedup
    if len(dedup_key) <= 1:
        return True
    # Known UI/navigation garbage patterns (Japanese/Chinese)
    garbage_keywords = [
        'カテゴリー', 'ステータス', 'すべて', 'その他', 'もっと見る',
        '一致するものが見つかりませんでした', '初回投資年次', 'トップメッセージ',
        'サステナビリティ', 'ステークホルダー', '環境への取り組み', '社会への取り組み',
        'ガバナンス', '編集方針', '免責事項', '進入官網', '項目状態',
        '此列表仅为', 'dtype object', 'dtype: object',
    ]
    for kw in garbage_keywords:
        if kw in raw:
            return True
    # Pandas Series repr leaked into data
    if 'Name:' in raw and 'dtype' in raw:
        return True
    # VC portfolio page metadata concatenated into name
    # e.g. "PlexxiFounded2010StageInvestedSeries BBackedSince2011StatusA"
    vc_metadata_patterns = [
        r'Founded\d{4}',           # Founded2010
        r'StageInvested',          # StageInvested
        r'BackedSince\d{4}',       # BackedSince2011
        r'Status(Private|Public|Acquired|A\b)',  # StatusPrivate
        r'Series\s+[A-Z],?\s*\d{4}Read',  # "Series A, 2016Read"
        r'^Acquired[A-Z]',         # "AcquiredShape Security"
        r'Invested(Seed|Series)',  # InvestedSeed
    ]
    for pat in vc_metadata_patterns:
        if re.search(pat, raw):
            return True
    # Website/UI names that aren't companies
    ui_blacklist = {
        'crunchbase', 'read more', 'view all', 'load more', 'see all',
        'learn more', 'portfolio', 'companies', 'team', 'about', 'contact',
        'visit site', 'website', 'news', 'blog', 'careers', 'jobs',
        'all', 'other', 'status', 'category', 'sector', 'stage',
    }
    if dedup_key.lower() in ui_blacklist:
        return True
    # Company name + description concatenation (long names with sentence-like content)
    # e.g. "ScubaReal-time customer intelligence at scale"
    if len(raw) > 40:
        # Check for sentence-like patterns: multiple lowercase words after capital
        if re.search(r'[a-z]{3,}\s+[a-z]{3,}\s+[a-z]{3,}', raw):
            return True
    # Very long names (>100 chars) are usually descriptions not company names
    if len(raw) > 100:
        return True
    return False


def dedup_lookup_key(name: str) -> str:
    """Lookup key for the manual dedup map.

    Collapses runs of whitespace and ignores letter case. Both are provably safe:
    two genuinely different companies never differ only by how many spaces sit
    between tokens, nor by capitalization alone. Punctuation is deliberately NOT
    touched, so no fuzzy merging can occur.
    """
    return re.sub(r"\s+", " ", name).strip().casefold()


def load_manual_dedup(config: Path | None) -> dict:
    """Load manual company dedup rules: normalized alias key → canonical name."""
    if config is None:
        return {}
    import yaml
    if not config.exists():
        raise FileNotFoundError(f"manual alias config not found: {config}")
    rules = yaml.safe_load(config.read_text()) or []
    alias_to_canonical = {}
    for rule in rules:
        canonical = rule.get("canonical")
        for alias in rule.get("aliases", []):
            alias_to_canonical[dedup_lookup_key(alias)] = canonical
        # The canonical spelling itself is a valid lookup target: two rows can share
        # the same display name yet arrive with different normalized_name values.
        if canonical:
            alias_to_canonical.setdefault(dedup_lookup_key(canonical), canonical)
    return alias_to_canonical


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Build company entity table from per-source edges parquets."
    )
    ap.add_argument("--edges-dir", required=True,
                    help="Directory of per-source edge Parquets")
    ap.add_argument("--out", required=True,
                    help="Output company entity Parquet")
    ap.add_argument("--manual-aliases", type=Path,
                    help="Optional YAML file of canonical names and aliases; no aliases are applied by default")
    args = ap.parse_args()
    edges_dir = Path(args.edges_dir)
    output_path = Path(args.out)

    # Collect all unique companies from edges
    print("Scanning edges...")
    
    # Load manual dedup rules
    manual_dedup = load_manual_dedup(args.manual_aliases)
    if manual_dedup:
        print(f"  Loaded {len(manual_dedup)} manual dedup aliases")
    
    # company_normalized_name → {first_raw_name, cusip, count}
    companies = {}  # norm_key → dict
    
    for f in sorted(edges_dir.glob("*_v0.2.parquet")):
        df = pq.read_table(f, columns=[
            "company_name_raw", "company_normalized_name", "metadata_json"
        ]).to_pandas()
        
        for _, row in df.iterrows():
            raw = row["company_name_raw"]
            norm = row["company_normalized_name"]
            if not norm or len(norm) < 2:
                continue
            
            # Further normalize for dedup (strip legal suffixes)
            dedup_key = normalize_company_name(norm)
            if not dedup_key:
                continue
            
            # Filter garbage data
            if _is_garbage(raw, dedup_key):
                continue
            
            # Apply manual dedup: if this raw name is a listed alias, adopt the
            # canonical entry's dedup_key so both spellings collapse into one row.
            lookup = dedup_lookup_key(raw) if raw else ""
            if lookup in manual_dedup:
                canonical_name = manual_dedup[lookup]
                # Normalize canonical name same way as edges' normalized_name
                canon_norm = re.sub(r"[^\w\s]", "", canonical_name.lower(), flags=re.UNICODE).strip()
                canon_norm = re.sub(r"\s+", " ", canon_norm)
                dedup_key = normalize_company_name(canon_norm)
                raw = canonical_name  # use canonical display name
            
            if dedup_key not in companies:
                # Extract CUSIP from metadata if available
                cusip = None
                meta = row.get("metadata_json")
                if meta and isinstance(meta, str) and "cusip" in meta:
                    import json
                    try:
                        m = json.loads(meta)
                        cusip = m.get("cusip")
                    except:
                        pass
                
                companies[dedup_key] = {
                    "canonical_name": raw,
                    "normalized_name": norm,
                    "dedup_key": dedup_key,
                    "cusip": cusip,
                    "edge_count": 1,
                }
            else:
                companies[dedup_key]["edge_count"] += 1
    
    print(f"  Raw company_normalized_name values: many")
    print(f"  After suffix-strip dedup: {len(companies)} unique companies")
    
    # Assign company_id by first-encounter order (dict preserves insertion order)
    rows = []
    for seq, (dedup_key, info) in enumerate(companies.items(), start=1):
        slug = make_slug(info["canonical_name"])
        if not slug:
            slug = make_slug(dedup_key)
        company_id = make_company_id(seq, slug)
        
        rows.append({
            "company_id": company_id,
            "company_canonical_name": info["canonical_name"],
            "company_normalized_name": info["normalized_name"],
            "company_dedup_key": dedup_key,
            "cusip": info["cusip"],
            "edge_count": info["edge_count"],
            "slug": slug,
        })
    
    df_out = pd.DataFrame(rows)
    
    # Verify uniqueness
    assert df_out["company_id"].is_unique, "company_id has duplicates!"
    assert df_out["company_dedup_key"].is_unique, "dedup_key has duplicates!"
    
    # Write
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df_out, preserve_index=False), output_path)

    print(f"\nDone. {len(df_out)} companies written to {output_path}")
    print(f"  Columns: {list(df_out.columns)}")
    print(f"  Top 10 by edge_count:")
    for _, r in df_out.nlargest(10, "edge_count").iterrows():
        print(f"    {r['company_id']:<35s} {r['company_canonical_name']:<30s} edges={r['edge_count']}")


if __name__ == "__main__":
    main()
