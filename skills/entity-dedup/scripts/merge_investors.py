#!/usr/bin/env python3
"""Merge per-source parquets into canonical entity + provenance tables.

W8 deliverable. Reads the 3 per-source extracted parquets from W5/W6/W7,
deduplicates firms via three-way equivalence (wikidata_qid, domain,
normalized_name + location_state overlap), computes canonical field
values per equivalence class, emits a long-format provenance table,
and generates the coverage report.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import SOURCE_CONFIDENCE, SOURCE_PRIORITY, INVESTOR_TYPE_ENUM


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

DEFAULT_ROOT = Path(os.environ.get("INVESTOR_BEHAVIOR_DATA_DIR", str(Path.home() / "investor-behavior-analysis")))
UNIVERSE_YAML = (
    Path(__file__).parent.parent / "configs" / "universe_v0.1.yaml"
)
SOURCES = ("findfunding", "wikidata", "manual_seed", "url_verify")
# `url_verify` is the W9b (2026-07-28) URL全库验证 pass — optional at merge time
# (missing parquet is skipped with a warning; other sources are required).
OPTIONAL_SOURCES = frozenset({"url_verify"})
SCHEMA_VERSION = "v0.1.0"

# For each (source, field) pair, which `SOURCE_CONFIDENCE` entry to look up.
# Fields not listed for a source mean that source doesn't contribute that field
# (e.g. findfunding has no wikidata_qid; wikidata has no crunchbase_permalink).
FIELD_KIND: dict[tuple[str, str], tuple[str, str]] = {
    # findfunding
    ("findfunding", "name"):                 ("findfunding", "structured"),
    ("findfunding", "website"):              ("findfunding", "structured"),
    ("findfunding", "location_state"):       ("findfunding", "structured"),
    ("findfunding", "stage_focus"):          ("findfunding", "structured"),
    ("findfunding", "industry_focus"):       ("findfunding", "structured"),
    ("findfunding", "crunchbase_permalink"): ("findfunding", "structured"),
    ("findfunding", "findfunding_slug"):     ("findfunding", "structured"),
    ("findfunding", "investor_type"):        ("findfunding", "implicit_type"),
    # wikidata
    ("wikidata", "name"):             ("wikidata", "label"),
    ("wikidata", "website"):          ("wikidata", "structured_property"),
    ("wikidata", "location_city"):    ("wikidata", "structured_property"),
    ("wikidata", "founded_year"):     ("wikidata", "structured_property"),
    ("wikidata", "wikidata_qid"):     ("wikidata", "structured_property"),
    ("wikidata", "investor_type"):    ("wikidata", "subclass_derived"),
    # manual_seed — uniform 0.80 curator
    ("manual_seed", "name"):             ("manual_seed", "curator"),
    ("manual_seed", "website"):          ("manual_seed", "curator"),
    ("manual_seed", "location_state"):   ("manual_seed", "curator"),
    ("manual_seed", "location_city"):    ("manual_seed", "curator"),
    ("manual_seed", "investor_type"):    ("manual_seed", "curator"),
    ("manual_seed", "manual_seed_slug"): ("manual_seed", "curator"),
    ("manual_seed", "wikidata_qid"):     ("manual_seed", "qid_cross_ref"),
    # url_verify — W9b. ONLY contributes `website` (the correction). Other fields
    # on url_verify rows (name / location_state / etc.) are copied from Step-1's
    # canonical for DSU dedup rule 3 (name+state) purposes; they must NOT compete
    # for canonical value selection, so we don't register them in FIELD_KIND.
    ("url_verify", "website"):        ("url_verify", "curl_verified"),
}

# Fields that are list<str> (union across sources, not confidence-picked)
LIST_FIELDS = frozenset({
    "stage_focus", "industry_focus", "investor_type",
    "location_state", "location_city",
})

# Domains that are aggregator platforms (multi-tenant profile hosts), NOT
# the firm's own website. Some findfunding entries mistakenly list their
# AngelList / LinkedIn / Crunchbase profile as the "website" — extract_domain
# would then normalize both AngelList (a marquee firm in our seed) and any
# firm using AngelList as their platform to `angellist.com`, causing a false
# merge. Skip these domains in the domain-match dedup rule.
AGGREGATOR_DOMAINS = frozenset({
    "angellist.com",
    "angel.co",
    "linkedin.com",
    "crunchbase.com",
    "medium.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "swfinstitute.org",
    "pitchbook.com",
})
# All entity spec column names, in order
ENTITY_COLUMNS = (
    "investor_id", "schema_version", "name", "normalized_name",
    "website", "domain", "location_country", "location_state",
    "location_city", "stage_focus", "industry_focus", "founded_year",
    "investor_type", "wikidata_qid", "findfunding_slug", "manual_seed_slug",
    "crunchbase_permalink", "source_set", "first_seen_at",
)

# Fields that are content-derived (not merged directly — recomputed from canonical name/website)
DERIVED_FIELDS = frozenset({"normalized_name", "domain"})

# Fields that are "external ID per source" — preserve each source's value regardless of merge
SOURCE_ID_FIELDS = frozenset({"wikidata_qid", "findfunding_slug", "manual_seed_slug"})


# ─────────────────────────────────────────────────────────────────────────
# Union-Find (DSU)
# ─────────────────────────────────────────────────────────────────────────


class DSU:
    """Union-Find for row-index equivalence classes."""
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1

    def classes(self) -> dict[int, list[int]]:
        """Return {root: [row_indices]} for all equivalence classes."""
        out: dict[int, list[int]] = collections.defaultdict(list)
        for i in range(len(self.parent)):
            out[self.find(i)].append(i)
        return dict(out)


# ─────────────────────────────────────────────────────────────────────────
# Load and dedup
# ─────────────────────────────────────────────────────────────────────────


def load_source(out_dir: Path, source: str, required: bool = True) -> list[dict]:
    """Load one per-source parquet into list of dicts. Adds a `_source` field.

    If `required=False` and file missing, returns [] with a warning (used for
    url_verify which is optional at merge time).
    """
    path = out_dir / "extracted" / "investors_by_source" / f"{source}_v0.1.parquet"
    if not path.exists():
        if not required:
            print(f"[skip] {source} parquet not found at {path}; excluding from merge")
            return []
        raise FileNotFoundError(f"Missing per-source parquet: {path}")
    table = pq.read_table(path)
    rows = table.to_pylist()
    for r in rows:
        r["_source"] = source
    return rows


def dedup(rows: list[dict]) -> DSU:
    """Build DSU over row indices using multi-way equivalence rules.

    Rule 1: same wikidata_qid (both non-null) → union.
    Rule 2: same domain (both non-null) → union. (Skips aggregator platforms
            AND skips url_verify rows to prevent url_verify's corrected domain
            from bridging unrelated firms sharing that domain by coincidence.)
    Rule 3: same normalized_name AND at least one shared location_state → union
            (state overlap check is set-intersection on list<str>; if either
            side has empty state list, this rule doesn't fire — safer to
            under-merge than over-merge distinct firms with the same name).
    Rule 4: same findfunding_slug (both non-null) → union. Native for
            findfunding rows (their own identity); also fires for url_verify
            rows that populate findfunding_slug from a findfunding-owned
            canonical.
    Rule 5: same manual_seed_slug (both non-null) → union. Same as rule 4 for
            manual_seed-owned canonicals.

    Rules 4/5 were added by W9b (2026-07-28) so url_verify rows can link to
    canonicals that lack both QID (rule 1 miss) and location_state (rule 3
    miss) — 4 such orphans (8bit Capital / Daybreak Ventures / Momenta /
    Seaplane Ventures) surfaced in the first W9b merge run.
    """
    dsu = DSU(len(rows))

    by_qid: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    by_name: dict[str, list[tuple[int, set[str]]]] = collections.defaultdict(list)
    by_findfunding_slug: dict[str, int] = {}
    by_manual_seed_slug: dict[str, int] = {}

    for i, r in enumerate(rows):
        # Rule 1: wikidata_qid
        qid = r.get("wikidata_qid")
        if qid:
            if qid in by_qid:
                dsu.union(i, by_qid[qid])
            else:
                by_qid[qid] = i

        # Rule 2: domain — but skip aggregator platforms (AngelList / LinkedIn /
        # Crunchbase / etc.) whose domain match would falsely collapse unrelated
        # firms that just happen to use the same platform for their profile
        # or syndicate. Also skip url_verify rows entirely for rule 2: url_verify's
        # corrected URL might legitimately be a domain used by a DIFFERENT firm
        # (rare but possible), so we let url_verify merge via rule 3 (name+state)
        # only. This prevents url_verify from bridging two previously-separate
        # clusters (e.g. Lightspeed VP + a hypothetical "Lightspeed Inc").
        dom = r.get("domain")
        if dom and dom not in AGGREGATOR_DOMAINS and r["_source"] != "url_verify":
            if dom in by_domain:
                dsu.union(i, by_domain[dom])
            else:
                by_domain[dom] = i

        # Rule 3: normalized_name + state overlap
        name = r.get("normalized_name")
        state_list = r.get("location_state") or []
        state_set = set(state_list) if state_list else set()
        if name and state_set:
            for j, prev_state in by_name[name]:
                if state_set & prev_state:  # intersection non-empty
                    dsu.union(i, j)
            by_name[name].append((i, state_set))

        # Rule 4: findfunding_slug match
        ff_slug = r.get("findfunding_slug")
        if ff_slug:
            if ff_slug in by_findfunding_slug:
                dsu.union(i, by_findfunding_slug[ff_slug])
            else:
                by_findfunding_slug[ff_slug] = i

        # Rule 5: manual_seed_slug match
        ms_slug = r.get("manual_seed_slug")
        if ms_slug:
            if ms_slug in by_manual_seed_slug:
                dsu.union(i, by_manual_seed_slug[ms_slug])
            else:
                by_manual_seed_slug[ms_slug] = i

    return dsu


# ─────────────────────────────────────────────────────────────────────────
# Canonical value selection
# ─────────────────────────────────────────────────────────────────────────


def _pick_scalar_canonical(candidates: list[tuple[str, object]]) -> object:
    """Given [(source, value), ...] where value is scalar-non-null,
    return the value with highest confidence, tie-break by source priority.
    Returns None if candidates empty."""
    if not candidates:
        return None
    # Each candidate is (source, value, confidence)
    ranked = sorted(
        candidates,
        key=lambda sv: (-sv[2], SOURCE_PRIORITY.get(sv[0], 99)),
    )
    return ranked[0][1]


def _get_confidence(source: str, field: str) -> float:
    """Look up confidence for (source, field) pair via FIELD_KIND indirection."""
    kind = FIELD_KIND.get((source, field))
    if kind is None:
        return 0.0  # This source doesn't contribute this field
    return SOURCE_CONFIDENCE.get(kind, 0.0)


def compute_canonical(rows_in_class: list[dict], run_date: dt.date) -> dict:
    """Build one canonical entity row from an equivalence class of source rows."""

    # ── investor_id: by source priority (independent of confidence) ──
    inv_id = None
    for src in SOURCES:  # SOURCES ordered by priority
        for r in rows_in_class:
            if r["_source"] == src and r.get("investor_id"):
                inv_id = r["investor_id"]
                break
        if inv_id:
            break

    # ── Per-field canonical selection ──
    canonical: dict = {"investor_id": inv_id, "schema_version": SCHEMA_VERSION}
    for field in ENTITY_COLUMNS:
        if field in ("investor_id", "schema_version", "source_set", "first_seen_at",
                     "location_country", "normalized_name", "domain"):
            continue  # Handled specially below

        if field in LIST_FIELDS:
            # Union across sources; preserve insertion order (first seen)
            union = []
            seen_elems = set()
            for r in rows_in_class:
                for elem in (r.get(field) or []):
                    if elem not in seen_elems:
                        seen_elems.add(elem)
                        union.append(elem)
            canonical[field] = union

        elif field in SOURCE_ID_FIELDS:
            # Preserve each source's own ID field (may be from a different source than investor_id).
            # e.g. an investor merged from wikidata + manual_seed will have wikidata_qid from wikidata
            # AND manual_seed_slug from manual_seed, both populated.
            # For wikidata_qid: pick from wikidata first, then manual_seed's cross-ref.
            # For findfunding_slug / manual_seed_slug: from their own source.
            source_of_field = {
                "wikidata_qid":      ["wikidata", "manual_seed"],
                "findfunding_slug":  ["findfunding"],
                "manual_seed_slug":  ["manual_seed"],
            }.get(field, [])
            picked = None
            for src in source_of_field:
                for r in rows_in_class:
                    if r["_source"] == src and r.get(field):
                        picked = r[field]
                        break
                if picked:
                    break
            canonical[field] = picked

        else:
            # Scalar field: pick by confidence + source-priority tie-break
            candidates = []
            for r in rows_in_class:
                v = r.get(field)
                if v is None or v == "":
                    continue
                conf = _get_confidence(r["_source"], field)
                candidates.append((r["_source"], v, conf))
            canonical[field] = _pick_scalar_canonical(candidates)

    # ── country: hardcoded US in v0.1 ──
    canonical["location_country"] = "US"

    # ── Derived fields: normalized_name from canonical name, domain from canonical website ──
    from _common import normalize_name, extract_domain
    canonical["normalized_name"] = normalize_name(canonical.get("name") or "")
    canonical["domain"] = extract_domain(canonical.get("website"))

    # ── Metadata ──
    canonical["source_set"] = sorted({r["_source"] for r in rows_in_class})
    fetched_dates = [r.get("first_seen_at") for r in rows_in_class if r.get("first_seen_at")]
    canonical["first_seen_at"] = min(fetched_dates) if fetched_dates else run_date

    return canonical


# ─────────────────────────────────────────────────────────────────────────
# Provenance rows
# ─────────────────────────────────────────────────────────────────────────


PROVENANCE_FIELDS = (
    "name", "website", "domain", "location_country", "location_state",
    "location_city", "stage_focus", "industry_focus", "founded_year",
    "investor_type", "wikidata_qid", "findfunding_slug", "crunchbase_permalink",
)


def _source_record_id(row: dict) -> str:
    """Return the source-native record id (Q-number / slug)."""
    src = row["_source"]
    if src == "wikidata":
        return row.get("wikidata_qid") or ""
    if src == "findfunding":
        return row.get("findfunding_slug") or ""
    if src == "manual_seed":
        return row.get("manual_seed_slug") or ""
    if src == "url_verify":
        # url_verify stores its slug in the investor_id field (`url_verify:<slug>`).
        # Parse out the slug portion for the provenance's source_record_id.
        inv_id = row.get("investor_id") or ""
        return inv_id[len("url_verify:"):] if inv_id.startswith("url_verify:") else ""
    return ""


def _evidence_url(row: dict) -> str | None:
    """Return the URL that a human can visit to audit the source record."""
    src = row["_source"]
    if src == "wikidata":
        qid = row.get("wikidata_qid")
        return f"https://www.wikidata.org/wiki/{qid}" if qid else None
    if src == "findfunding":
        slug = row.get("findfunding_slug")
        return f"https://www.findfunding.vc/{slug}" if slug else None
    if src == "url_verify":
        # For url_verify, the "evidence" IS the verified URL itself. The full
        # curl + Tavily audit trail lives in raw/url_verify/YYYY-MM-DD/*.json.
        return row.get("website")
    return None  # manual_seed has no external URL


def build_provenance_rows(
    canonical_by_class: dict[int, dict],
    rows_by_class: dict[int, list[dict]],
) -> list[dict]:
    """Emit long-format provenance rows.

    One row per (investor_id, field, source-contribution). If two sources
    both contribute a value for the same field on the same investor,
    both are emitted so downstream can filter by confidence or source.
    """
    out: list[dict] = []
    for root, canonical in canonical_by_class.items():
        inv_id = canonical["investor_id"]
        for r in rows_by_class[root]:
            src = r["_source"]
            for field in PROVENANCE_FIELDS:
                v = r.get(field)
                if v is None or v == "" or v == []:
                    continue
                conf = _get_confidence(src, field)
                if conf == 0.0 and (src, field) not in FIELD_KIND:
                    continue  # Source doesn't naturally contribute this field
                # Serialize value: list → JSON, others → str
                val_str = json.dumps(v) if isinstance(v, list) else str(v)
                out.append({
                    "investor_id":      inv_id,
                    "field":            field,
                    "value":            val_str,
                    "source":           src,
                    "source_record_id": _source_record_id(r),
                    "confidence":       conf,
                    "evidence_url":     _evidence_url(r),
                    "fetched_at":       r.get("first_seen_at"),
                    "schema_version":   SCHEMA_VERSION,
                })
    return out


# ─────────────────────────────────────────────────────────────────────────
# Coverage report
# ─────────────────────────────────────────────────────────────────────────


def build_coverage_report(
    canonical_rows: list[dict],
    provenance_rows: list[dict],
    per_source_rows: dict[str, list[dict]],
    dedup_stats: dict,
    universe: dict,
    run_date: dt.date,
) -> str:
    """Assemble the step1_coverage_v0.1.md markdown report."""
    lines: list[str] = []

    total = len(canonical_rows)
    corridor_min = universe["sanity_checks"]["min_merged_rows"]
    corridor_max = universe["sanity_checks"]["max_merged_rows"]
    marquee = universe["sanity_checks"]["must_contain_names"]

    lines.append(f"# Step 1 Coverage Report — v0.1")
    lines.append("")
    lines.append(f"**Generated**: {run_date.isoformat()}")
    lines.append(f"**Schema version**: {SCHEMA_VERSION}")
    lines.append("")

    # ─ Row count sanity ─
    lines.append("## 1. Row count sanity")
    lines.append("")
    lines.append(f"- Merged canonical rows: **{total}**")
    lines.append(f"- Pre-dedup total: {sum(len(r) for r in per_source_rows.values())} "
                 f"({' + '.join(f'{s}={len(per_source_rows[s])}' for s in SOURCES)})")
    lines.append(f"- Rows collapsed via dedup: {dedup_stats['collapsed']}")
    lines.append(f"- Universe corridor: [{corridor_min}, {corridor_max}]")
    in_corridor = corridor_min <= total <= corridor_max
    lines.append(f"- Status: **{'PASS' if in_corridor else 'WARN'}** {'' if in_corridor else '(row count outside corridor)'}")
    lines.append("")

    # ─ Marquee sanity ─
    lines.append("## 2. Marquee sanity (must-contain names)")
    lines.append("")
    from _common import normalize_name
    # Build a set of ALL normalized names known for each canonical firm,
    # sourced from provenance rows. This catches marquee firms whose canonical
    # name was picked from a different source than what marquee list uses —
    # e.g. GV (in marquee) merged with Google Ventures (canonical), so
    # normalized("GV")=="gv" but canonical normalized_name=="google". The
    # smart check finds "gv" in the manual_seed provenance rows and hits.
    all_norms: set[str] = set()
    for r in canonical_rows:
        if r.get("normalized_name"):
            all_norms.add(r["normalized_name"])
    # Include names from provenance (each source's contributed name)
    for p in provenance_rows:
        if p["field"] == "name" and p["value"]:
            all_norms.add(normalize_name(p["value"]))
    hits, misses = [], []
    for name in marquee:
        norm = normalize_name(name)
        (hits if norm in all_norms else misses).append(name)
    lines.append(f"- Passed: **{len(hits)}/{len(marquee)}** (matched against canonical + provenance names)")
    if misses:
        lines.append(f"- Missing: {', '.join(misses)}")
    else:
        lines.append(f"- Missing: (none)")
    lines.append("")

    # ─ Per-type distribution ─
    lines.append("## 3. Per-`investor_type` distribution")
    lines.append("")
    type_counter: collections.Counter = collections.Counter()
    firms_with_type = 0
    for r in canonical_rows:
        types = r.get("investor_type") or []
        if types:
            firms_with_type += 1
        for t in types:
            type_counter[t] += 1
    lines.append("| type | firms tagged (multi-count) |")
    lines.append("|---|---|")
    for t in INVESTOR_TYPE_ENUM:
        lines.append(f"| `{t}` | {type_counter.get(t, 0)} |")
    lines.append(f"| (no type) | {total - firms_with_type} |")
    lines.append("")
    lines.append(f"Total firms with ≥1 type: {firms_with_type} / {total} ({100.0*firms_with_type/total:.1f}%)")
    lines.append("")

    # ─ Field completeness × source ─
    lines.append("## 4. Field completeness × source")
    lines.append("")
    lines.append("Non-null coverage per field, computed against each source's per-source parquet "
                 "(rows that source produced) AND against the canonical merged table.")
    lines.append("")
    lines.append("| field | findfunding | wikidata | manual_seed | canonical |")
    lines.append("|---|---:|---:|---:|---:|")
    for field in PROVENANCE_FIELDS + ("normalized_name", "domain"):
        parts = []
        for src in SOURCES:
            src_rows = per_source_rows[src]
            n = sum(1 for r in src_rows if r.get(field) not in (None, "", []))
            pct = 100.0 * n / max(len(src_rows), 1) if src_rows else 0.0
            parts.append(f"{n}/{len(src_rows)} ({pct:.1f}%)")
        can_n = sum(1 for r in canonical_rows if r.get(field) not in (None, "", []))
        can_pct = 100.0 * can_n / total if total else 0.0
        lines.append(f"| `{field}` | {parts[0]} | {parts[1]} | {parts[2]} | {can_n}/{total} ({can_pct:.1f}%) |")
    lines.append("")

    # ─ Source overlap ─
    lines.append("## 5. Cross-source overlap")
    lines.append("")
    overlap_counter: collections.Counter = collections.Counter()
    for r in canonical_rows:
        key = " + ".join(sorted(r.get("source_set") or []))
        overlap_counter[key] += 1
    lines.append("| sources | firms |")
    lines.append("|---|---:|")
    for combo, n in sorted(overlap_counter.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"| {combo} | {n} |")
    lines.append("")

    # ─ QID conflicts ─
    lines.append("## 6. Cross-source QID conflicts")
    lines.append("")
    lines.append("Investors where multiple sources supplied `wikidata_qid` values that "
                 "disagreed. Canonical selection resolved these by confidence "
                 "(Wikidata 0.95 > manual_seed 0.80); this section surfaces the raw "
                 "conflicts for human review of the manual seed's QID entries.")
    lines.append("")
    conflicts = []
    for r in canonical_rows:
        # Look at provenance for this investor's wikidata_qid entries
        prov_for_this = [
            p for p in provenance_rows
            if p["investor_id"] == r["investor_id"] and p["field"] == "wikidata_qid"
        ]
        if len(prov_for_this) < 2:
            continue
        distinct_vals = {p["value"] for p in prov_for_this}
        if len(distinct_vals) > 1:
            conflicts.append((r.get("name") or r["investor_id"], prov_for_this))
    if not conflicts:
        lines.append("- (none)")
    else:
        lines.append("| firm | source | qid |")
        lines.append("|---|---|---|")
        for name, prov_rows in conflicts:
            for p in prov_rows:
                lines.append(f"| {name} | {p['source']} | {p['value']} |")
    lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Parquet schemas
# ─────────────────────────────────────────────────────────────────────────


def build_entity_schema() -> pa.Schema:
    return pa.schema([
        ("investor_id",          pa.string()),
        ("schema_version",       pa.string()),
        ("name",                 pa.string()),
        ("normalized_name",      pa.string()),
        ("website",              pa.string()),
        ("domain",               pa.string()),
        ("location_country",     pa.string()),
        ("location_state",       pa.list_(pa.string())),
        ("location_city",        pa.list_(pa.string())),
        ("stage_focus",          pa.list_(pa.string())),
        ("industry_focus",       pa.list_(pa.string())),
        ("founded_year",         pa.int32()),
        ("investor_type",        pa.list_(pa.string())),
        ("wikidata_qid",         pa.string()),
        ("findfunding_slug",     pa.string()),
        ("manual_seed_slug",     pa.string()),
        ("crunchbase_permalink", pa.string()),
        ("source_set",           pa.list_(pa.string())),
        ("first_seen_at",        pa.date32()),
    ])


def build_provenance_schema() -> pa.Schema:
    return pa.schema([
        ("investor_id",      pa.string()),
        ("field",            pa.string()),
        ("value",            pa.string()),
        ("source",           pa.string()),
        ("source_record_id", pa.string()),
        ("confidence",       pa.float32()),
        ("evidence_url",     pa.string()),
        ("fetched_at",       pa.date32()),
        ("schema_version",   pa.string()),
    ])


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Merge 3 per-source parquets → canonical entity + provenance + coverage report"
    )
    ap.add_argument("--out-dir", default=str(DEFAULT_ROOT),
                    help=f"Root output dir (default: {DEFAULT_ROOT})")
    ap.add_argument("--universe", default=str(UNIVERSE_YAML),
                    help=f"Universe yaml (default: {UNIVERSE_YAML})")
    args = ap.parse_args()

    run_date = dt.date.today()
    out_dir = Path(args.out_dir)

    # Load
    per_source_rows: dict[str, list[dict]] = {}
    for src in SOURCES:
        per_source_rows[src] = load_source(out_dir, src, required=(src not in OPTIONAL_SOURCES))
        print(f"[load] {src}: {len(per_source_rows[src])} rows")

    all_rows: list[dict] = []
    for src in SOURCES:
        all_rows.extend(per_source_rows[src])
    print(f"[total pre-dedup] {len(all_rows)} rows")

    # Dedup
    dsu = dedup(all_rows)
    classes = dsu.classes()
    print(f"[dedup] {len(all_rows)} rows → {len(classes)} equivalence classes")
    dedup_stats = {"collapsed": len(all_rows) - len(classes)}

    # Canonical per class
    rows_by_class = {root: [all_rows[i] for i in idxs] for root, idxs in classes.items()}
    canonical_by_class = {
        root: compute_canonical(rows_by_class[root], run_date)
        for root in classes
    }
    canonical_rows = list(canonical_by_class.values())
    print(f"[canonical] {len(canonical_rows)} merged entity rows")

    # Provenance
    provenance_rows = build_provenance_rows(canonical_by_class, rows_by_class)
    print(f"[provenance] {len(provenance_rows)} rows")

    # Load universe
    with open(args.universe) as f:
        universe = yaml.safe_load(f)

    # Write parquets
    entity_path = out_dir / "investors_entity_v0.1.parquet"
    provenance_path = out_dir / "investors_provenance_v0.1.parquet"
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "step1_coverage_v0.1.md"

    entity_table = pa.Table.from_pylist(canonical_rows, schema=build_entity_schema())
    pq.write_table(entity_table, entity_path, compression="snappy")
    print(f"[wrote] {entity_path} ({entity_path.stat().st_size / 1024:.1f} KB)")

    prov_table = pa.Table.from_pylist(provenance_rows, schema=build_provenance_schema())
    pq.write_table(prov_table, provenance_path, compression="snappy")
    print(f"[wrote] {provenance_path} ({provenance_path.stat().st_size / 1024:.1f} KB)")

    # Coverage report
    report = build_coverage_report(
        canonical_rows, provenance_rows, per_source_rows,
        dedup_stats, universe, run_date,
    )
    report_path.write_text(report)
    print(f"[wrote] {report_path} ({report_path.stat().st_size / 1024:.1f} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
