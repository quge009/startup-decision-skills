#!/usr/bin/env python3
"""Merge caller-supplied per-firm edge Parquets into an auditable table.

Reads ``DATA_ROOT/extracted/edges_by_source/*_v0.1.parquet`` and validates the
result against an explicitly supplied canonical investor table.

Sanity invariants (fail-loud):
  - `(investor_id, edge_id)` composite unique across the file
  - Every ``investor_id`` appears in the supplied investor table (FK integrity)
  - Per-firm row sum == top-level row count

Output paths default below DATA_ROOT and may be overridden explicitly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_IBA_DATA = Path(os.environ.get("INVESTOR_BEHAVIOR_DATA_DIR", "~/investor-behavior-analysis")).expanduser()


def load_all_per_firm(root: Path) -> list[tuple[str, pa.Table]]:
    """Load every per-firm parquet under extracted/edges_by_source/ .

    Returns [(slug, table), ...] sorted by slug.
    """
    src_dir = root / "extracted" / "edges_by_source"
    if not src_dir.exists():
        raise FileNotFoundError(f"missing per-firm parquet dir: {src_dir}")
    out: list[tuple[str, pa.Table]] = []
    for p in sorted(src_dir.glob("*_v0.1.parquet")):
        slug = p.stem.rsplit("_v0.1", 1)[0]
        table = pq.read_table(p)
        out.append((slug, table))
    if not out:
        raise ValueError(f"no per-firm parquets found under {src_dir}")
    return out


def load_investor_ids(entity_pq: Path) -> set[str]:
    if not entity_pq.exists():
        raise FileNotFoundError(f"missing investors_entity parquet: {entity_pq}")
    t = pq.read_table(entity_pq, columns=["investor_id"])
    return set(t["investor_id"].to_pylist())


def verify_invariants(merged: pa.Table, per_firm: list[tuple[str, pa.Table]], canonical_ids: set[str]) -> dict:
    """Fail-loud on any invariant violation. Returns stats dict on success."""
    n_total = merged.num_rows
    n_sum_per_firm = sum(t.num_rows for _, t in per_firm)
    if n_total != n_sum_per_firm:
        raise AssertionError(
            f"row-sum mismatch: merged has {n_total}, sum-of-per-firm is {n_sum_per_firm}"
        )

    # Composite PK: (investor_id, edge_id) unique
    inv_ids = merged["investor_id"].to_pylist()
    edge_ids = merged["edge_id"].to_pylist()
    pk_pairs = list(zip(inv_ids, edge_ids))
    dup_pairs = [pair for pair, cnt in Counter(pk_pairs).items() if cnt > 1]
    if dup_pairs:
        sample = dup_pairs[:5]
        raise AssertionError(
            f"composite PK (investor_id, edge_id) has {len(dup_pairs)} duplicates. "
            f"Sample: {sample}"
        )

    # FK integrity: every investor_id in canonical
    unique_investors = set(inv_ids)
    unknown = unique_investors - canonical_ids
    if unknown:
        raise AssertionError(
            f"{len(unknown)} investor_id(s) not in investors_entity: {list(unknown)[:5]}"
        )

    return {
        "n_edges": n_total,
        "n_firms": len(unique_investors),
        "per_firm_files": len(per_firm),
    }


def write_coverage_report(
    merged: pa.Table,
    per_firm: list[tuple[str, pa.Table]],
    out_path: Path,
    run_date: dt.date,
) -> None:
    """Emit step2_coverage_v0.1.md — high-level coverage + per-firm + field completeness + co-invest."""
    n = merged.num_rows
    df = merged.to_pandas()

    # Per-firm edge counts (already deduped within firm)
    per_firm_counts = [(slug, t.num_rows) for slug, t in per_firm]

    # Field completeness (top-level)
    def _nonnull_count(col: str) -> int:
        s = df[col]
        if col in ("stage_at_investment", "industry"):
            # list<str>: count rows with non-empty list
            return int(sum(1 for v in s if v is not None and len(v) > 0))
        return int(s.notna().sum())

    field_completeness = {
        col: _nonnull_count(col)
        for col in ["investor_id", "company_name_raw", "company_normalized_name",
                    "company_url", "company_domain", "stage_at_investment",
                    "industry", "position", "confidence"]
    }

    # Cross-firm shared companies: same normalized name appears under ≥2 investor_ids
    by_company: dict[str, set[str]] = defaultdict(set)
    for _, r in df.iterrows():
        by_company[r["company_normalized_name"]].add(r["investor_id"])
    shared = [(name, investors) for name, investors in by_company.items() if len(investors) >= 2]
    shared.sort(key=lambda x: (-len(x[1]), x[0]))

    # Count firms with edges vs total files (empty parquets = null-selector skips)
    n_files = len(per_firm)
    n_with_edges = sum(1 for _, t in per_firm if t.num_rows > 0)
    n_empty = n_files - n_with_edges

    lines = [
        "# Portfolio Edge Coverage Report",
        "",
        f"**Generated**: {run_date.isoformat()}",
        f"**Schema version**: v0.1.0",
        "",
        "## 1. Overall",
        "",
        f"- **Total edges**: {n:,}",
        f"- **Source files**: {n_files}",
        f"- **Firms with edges**: {n_with_edges} (empty source tables: {n_empty})",
        f"- **Unique portfolio companies (by normalized name)**: {len(by_company):,}",
        f"- **Cross-firm shared companies (invested by ≥ 2 VCs)**: {len(shared):,}",
        "",
        "## 2. Per-firm edge distribution",
        "",
        "| # | Firm slug | Edges |",
        "|---|---|---:|",
    ]
    for i, (slug, cnt) in enumerate(sorted(per_firm_counts, key=lambda x: -x[1]), 1):
        lines.append(f"| {i} | {slug} | {cnt:,} |")

    lines += [
        "",
        "## 3. Field completeness (top-level table)",
        "",
        "| Field | Non-null | Coverage |",
        "|---|---:|---:|",
    ]
    for col, cnt in field_completeness.items():
        pct = 100.0 * cnt / n if n else 0.0
        lines.append(f"| `{col}` | {cnt:,} | {pct:.1f}% |")

    lines += [
        "",
        "## 4. Cross-firm shared companies (top 20)",
        "",
        "Companies appearing under multiple investors, suitable for co-investment network analysis.",
        "",
        "| Company (normalized) | # co-investors | Investor slugs |",
        "|---|---:|---|",
    ]
    for name, investors in shared[:20]:
        slugs = ", ".join(sorted(inv_id.split(":", 1)[-1] for inv_id in investors))
        lines.append(f"| `{name}` | {len(investors)} | {slugs} |")
    if not shared:
        lines.append("| *(none — no cross-firm shared companies detected)* | | |")

    lines += ["", "## 5. Interpretation", "",
              "Coverage reflects only the supplied public pages and selector configurations; absence is not evidence that an investment did not occur.", ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-root", default=str(DEFAULT_IBA_DATA))
    ap.add_argument("--investors", required=True,
                    help="Canonical investor entity Parquet used for FK validation")
    ap.add_argument("--output", help="Merged Parquet path (default: DATA_ROOT/edges_long_v0.1.parquet)")
    ap.add_argument("--report", help="Coverage report path (default: DATA_ROOT/reports/portfolio_edge_coverage.md)")
    args = ap.parse_args()
    data_root = Path(args.data_root)

    per_firm = load_all_per_firm(data_root)
    print(f"[load] {len(per_firm)} per-firm parquet(s) from {data_root/'extracted/edges_by_source'}")

    canonical_ids = load_investor_ids(Path(args.investors))
    print(f"[canonical] {len(canonical_ids)} investor_ids loaded from entity parquet")

    merged = pa.concat_tables([t for _, t in per_firm])
    print(f"[concat] {merged.num_rows} rows")

    stats = verify_invariants(merged, per_firm, canonical_ids)
    print(f"[verify] OK — {stats}")

    out_pq = Path(args.output) if args.output else data_root / "edges_long_v0.1.parquet"
    out_pq.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(merged, out_pq, compression="snappy")
    print(f"[wrote parquet] {out_pq} ({out_pq.stat().st_size / 1024:.1f} KB)")

    out_md = Path(args.report) if args.report else data_root / "reports" / "portfolio_edge_coverage.md"
    write_coverage_report(merged, per_firm, out_md, dt.date.today())
    print(f"[wrote report] {out_md} ({out_md.stat().st_size / 1024:.1f} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
