#!/usr/bin/env python3
"""Dump each per-firm Parquet's first N rows to a review Markdown file.

For selector-quality review: inspect the emitted Markdown for
selectors that captured wrong content (nav items, boilerplate, image alt
text that isn't a company name), then decide which firms need selector regeneration
or manual selector patching.

Outputs: `_audit/spot_check_YYYY-MM-DD.md` — table per firm with first N
rows (name / url / industry / stage / position), plus flags for suspicious
patterns (all names look like nav items / all URLs empty / etc.).

Usage:
  python3 scripts/spot_check_edges.py
  python3 scripts/spot_check_edges.py --sample 15
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq


DEFAULT_SAMPLE = 10

# Words that if they show up in company name field probably mean selector
# captured a nav item / boilerplate, not a real company.
BOILERPLATE_TOKENS = {
    "home", "about", "team", "portfolio", "companies", "invest", "investors",
    "contact", "news", "press", "careers", "jobs", "blog", "login", "signin",
    "menu", "search", "subscribe", "privacy", "terms", "cookies", "legal",
    "next", "prev", "previous", "back", "read more", "learn more", "close",
}


def _looks_boilerplate(name: str) -> bool:
    if not name:
        return True
    lower = name.lower().strip()
    return lower in BOILERPLATE_TOKENS or len(lower) < 2


def _looks_url(name: str) -> bool:
    return isinstance(name, str) and (name.startswith("http://") or name.startswith("https://"))


def _list_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v[:3])
    return str(v)


def _truncate(s: str, n: int = 60) -> str:
    if not isinstance(s, str):
        s = str(s or "")
    return s if len(s) <= n else s[:n-1] + "…"


def spot_check_firm(slug: str, parquet_path: Path, sample: int) -> dict:
    """Read parquet + emit a spot-check dict (name samples + flags)."""
    t = pq.read_table(parquet_path)
    n = t.num_rows
    if n == 0:
        return {"slug": slug, "n": 0, "sample_rows": [], "flags": ["empty_parquet"]}

    df = t.to_pandas()
    sample_df = df.head(sample)

    rows: list[dict] = []
    for _, r in sample_df.iterrows():
        rows.append({
            "name":     _truncate(str(r.get("company_name_raw") or ""), 40),
            "norm":     _truncate(str(r.get("company_normalized_name") or ""), 30),
            "url":      _truncate(str(r.get("company_url") or ""), 60),
            "stage":    _truncate(_list_str(r.get("stage_at_investment")), 30),
            "industry": _truncate(_list_str(r.get("industry")), 30),
            "position": _truncate(str(r.get("position") or ""), 15),
        })

    flags: list[str] = []
    # Flag: ≥ 30% of sample names look boilerplate
    n_boiler = sum(1 for row in rows if _looks_boilerplate(row["name"]))
    if n_boiler / max(len(rows), 1) >= 0.3:
        flags.append(f"⚠️ {n_boiler}/{len(rows)} names look boilerplate (nav / menu)")
    # Flag: ≥ 50% of sample names still URLs (heuristic didn't catch all)
    n_url = sum(1 for row in rows if _looks_url(row["name"]))
    if n_url / max(len(rows), 1) >= 0.5:
        flags.append(f"⚠️ {n_url}/{len(rows)} names still look URL — heuristic incomplete")
    # Flag: all company_url empty
    if all(not row["url"] for row in rows):
        flags.append("ℹ️ all company_url empty (page has no outbound company links)")
    # Flag: all stage / industry / position empty
    if all(not row["stage"] for row in rows):
        flags.append("ℹ️ all stage empty (page doesn't group by stage)")
    if all(not row["industry"] for row in rows):
        flags.append("ℹ️ all industry empty (page doesn't tag industry)")
    if all(not row["position"] for row in rows):
        flags.append("ℹ️ all position empty (page doesn't tag active/exited)")

    return {"slug": slug, "n": n, "sample_rows": rows, "flags": flags}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-root", required=True,
                    help="Root containing extracted/edges_by_source")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help=f"Rows to sample per firm (default: {DEFAULT_SAMPLE})")
    ap.add_argument("--out", required=True, help="Output Markdown path")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    src_dir = data_root / "extracted" / "edges_by_source"
    if not src_dir.exists():
        print(f"ERROR: {src_dir} not found", file=sys.stderr)
        return 1

    parquets = sorted(src_dir.glob("*_v0.1.parquet"))
    if not parquets:
        print(f"ERROR: no per-firm parquets in {src_dir}", file=sys.stderr)
        return 1

    checks: list[dict] = []
    for p in parquets:
        slug = p.stem.rsplit("_v0.1", 1)[0]
        checks.append(spot_check_firm(slug, p, args.sample))

    # Write output
    today = dt.date.today().isoformat()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_edges = sum(c["n"] for c in checks)
    n_firms_with_flags = sum(1 for c in checks if c["flags"] and any(f.startswith("⚠️") for f in c["flags"]))

    lines = [
        f"# Portfolio edge spot-check — {today}",
        "",
        f"Firms reviewed: **{len(checks)}** · Total edges: **{total_edges:,}** · Firms with ⚠️ warnings: **{n_firms_with_flags}**",
        "",
        "For each firm, first N rows shown. ⚠️ = likely selector defect; ℹ️ = field absent on the page (informational).",
        "",
    ]

    for c in checks:
        slug = c["slug"]
        lines.append(f"## {slug} — {c['n']:,} edges")
        lines.append("")
        for f in c["flags"]:
            lines.append(f"- {f}")
        if c["flags"]:
            lines.append("")
        if c["sample_rows"]:
            lines.append("| # | company_name_raw | normalized | url | stage | industry | position |")
            lines.append("|--:|---|---|---|---|---|---|")
            for i, r in enumerate(c["sample_rows"], 1):
                lines.append(
                    f"| {i} | `{r['name']}` | `{r['norm']}` | `{r['url']}` | `{r['stage']}` | `{r['industry']}` | `{r['position']}` |"
                )
        lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"[wrote] {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
    print(f"        {len(checks)} firms · {total_edges:,} total edges · {n_firms_with_flags} firm(s) with ⚠️ warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
