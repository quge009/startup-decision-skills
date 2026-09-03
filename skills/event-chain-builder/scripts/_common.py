"""Shared helpers for W5-W7 ingest scripts and W8 merge.

All three per-source ingest scripts (findfunding / wikidata / manual_seed)
apply the same normalization pipeline to their entity fields so that the
downstream merge step can compare them apples-to-apples. This module
holds the normalizers plus the confidence-defaults table from the
provenance spec.

The normalizers implement the rules stated in
schemas/investors_entity_v0.1.spec.md — treat this file as executable
spec; if a rule changes in the spec, change it here first.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


def make_slug(name: str, max_len: int = 50) -> str:
    """Generate a slug from any Unicode name.

    Transliterates accented characters (é→e, ö→o, ñ→n) for Latin scripts,
    preserves CJK and other non-Latin characters directly.
    """
    # Separate CJK chars from the rest — don't decompose CJK
    parts = []
    for ch in name:
        if '\u2e80' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff' or '\uff00' <= ch <= '\uffef' or '\uac00' <= ch <= '\ud7af':
            # CJK, Hiragana, Katakana, Fullwidth, Korean — keep as-is
            parts.append(ch)
        else:
            # Latin/other — decompose to strip accents
            nfkd = unicodedata.normalize("NFKD", ch)
            for c in nfkd:
                if not unicodedata.combining(c):
                    parts.append(c)
    
    text = "".join(parts).lower()
    # Replace non-word chars with hyphen
    slug = re.sub(r"[^\w]", "-", text, flags=re.UNICODE)
    # Collapse multiple hyphens, strip leading/trailing
    slug = re.sub(r"-+", "-", slug).strip("-")
    slug = slug.replace("_", "-")
    return slug[:max_len]


# ─────────────────────────────────────────────────────────────────────────
# Normalized-name (entity spec column 4)
# ─────────────────────────────────────────────────────────────────────────

# 16-token trailing strip list. Order-insensitive, matched case-insensitively.
# Applied greedily until no further trailing token matches — so
# "Sequoia Capital Partners" → "sequoia" (Partners then Capital both strip).
# Extended 2026-07-30 (W20 v3): added corp / co / corporation / company to
# harmonize investors_entity / edges_long / form_d_index normalization,
# closing the gap where "Ramp Business Corp" / "Coinbase Global, Inc." /
# "Okta Corp" style operating-company Form D issuer names failed tier-1
# exact match against edges_long portfolio-company names.
_NORMALIZED_NAME_STRIP_TOKENS = frozenset({
    "ventures", "capital", "partners", "fund", "llc", "lp", "inc", "group",
    "advisors", "holdings", "investments", "management",
    "corp", "co", "corporation", "company",
})

_WHITESPACE_RUN = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s-]")  # keep letters, digits, spaces, hyphens


def normalize_name(raw: str) -> str:
    """Compute normalized_name from a canonical firm name.

    Rules (from entity spec column 4):
    - Lowercase
    - Strip punctuation (keep spaces + hyphens)
    - Collapse consecutive whitespace to single space
    - Repeatedly strip trailing tokens from the 12-token list until no more match
    - Trim leading/trailing whitespace

    Example: "Sequoia Capital"       → "sequoia"
             "Andreessen Horowitz"   → "andreessen horowitz"
             "Bain Capital Ventures" → "bain"
    """
    if raw is None:
        return ""
    s = raw.strip().lower()
    s = _NON_WORD.sub(" ", s)
    s = _WHITESPACE_RUN.sub(" ", s).strip()
    if not s:
        return ""
    tokens = s.split(" ")
    while tokens and tokens[-1] in _NORMALIZED_NAME_STRIP_TOKENS:
        tokens.pop()
    return " ".join(tokens)


# ─────────────────────────────────────────────────────────────────────────
# Website + domain (entity spec columns 5, 6)
# ─────────────────────────────────────────────────────────────────────────


def normalize_website(raw: str) -> str | None:
    """Canonicalize a website URL.

    - Add `https://` if the scheme is missing
    - Lowercase the host part
    - Strip a trailing `/` from the path
    - Return None for empty / falsy input

    The path is preserved (e.g. `https://foo.com/vc-arm` stays intact),
    but the scheme + host are normalized. Query strings and fragments
    are kept as-is.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # If no scheme, prepend https://
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    if not netloc:
        return raw
    path = parsed.path.rstrip("/")
    tail = ""
    if parsed.query:
        tail += f"?{parsed.query}"
    if parsed.fragment:
        tail += f"#{parsed.fragment}"
    return f"{scheme}://{netloc}{path}{tail}"


# Minimal public-suffix table for the common TLDs seen in v0.1 sources.
# NOT a full PSL implementation — for that we'd need the `tldextract`
# package. For MVP the 3 sources produce almost exclusively .com / .co /
# .vc / .org / .io / .ai / .net / .fund domains, so this simple list
# handles the common cases. Two-part suffixes (co.uk etc.) are unusual
# in US VC context; if they appear, extract_domain falls back to the
# last two labels which is a safe overestimate.
_KNOWN_SIMPLE_SUFFIXES = frozenset({
    "com", "org", "net", "io", "ai", "co", "vc", "fund", "capital",
    "us", "app", "dev", "tech", "ventures", "xyz", "so", "me",
})


def extract_domain(website: str | None) -> str | None:
    """Extract the registrable domain from a normalized website URL.

    - Returns None if input is None/empty
    - Strips scheme, path, query, fragment
    - Strips leading `www.`
    - Returns the two-label suffix (host + TLD) for known single-label
      TLDs, or the last two labels as a safe overestimate

    Example: normalize_website("https://www.sequoiacap.com/team")
             extract_domain(above) → "sequoiacap.com"
    """
    if not website:
        return None
    if "://" in website:
        website = website.split("://", 1)[1]
    # Drop everything after first / ? #
    for sep in ("/", "?", "#"):
        if sep in website:
            website = website.split(sep, 1)[0]
    website = website.lower().lstrip(".")
    if website.startswith("www."):
        website = website[4:]
    parts = website.split(".")
    if len(parts) < 2:
        return website or None
    # If last label is a known simple suffix, return last-2 labels.
    # Otherwise return last-2 as fallback.
    return ".".join(parts[-2:])


# ─────────────────────────────────────────────────────────────────────────
# Investment-stage enum (entity spec column 10)
# ─────────────────────────────────────────────────────────────────────────

# Full 10-value enum from the entity spec.
STAGE_ENUM = (
    "pre-seed",
    "seed",
    "series-a",
    "series-b",
    "series-c",
    "series-d-plus",
    "growth",
    "late-stage",
    "pre-ipo",
    "any",
)

# Case-insensitive lookup covering the raw strings we've seen across
# findfunding + Wikidata + manual seed. Add mappings here when a new
# raw string is observed rather than making the parser guess.
_STAGE_ALIASES = {
    "pre-seed": "pre-seed",
    "preseed": "pre-seed",
    "pre seed": "pre-seed",
    "seed": "seed",
    "series a": "series-a",
    "series-a": "series-a",
    "seriesa": "series-a",
    "series b": "series-b",
    "series-b": "series-b",
    "series c": "series-c",
    "series-c": "series-c",
    "series d": "series-d-plus",
    "series-d": "series-d-plus",
    "series d+": "series-d-plus",
    "series d and later": "series-d-plus",
    "series d-plus": "series-d-plus",
    "series-d-plus": "series-d-plus",
    "series e": "series-d-plus",
    "series f": "series-d-plus",
    "series g": "series-d-plus",
    "growth": "growth",
    "growth stage": "growth",
    "late stage": "late-stage",
    "late-stage": "late-stage",
    "pre-ipo": "pre-ipo",
    "pre ipo": "pre-ipo",
    "any": "any",
    "any stage": "any",
    "any-stage": "any",
}


def normalize_stage(raw: str) -> str | None:
    """Map a raw stage string to the entity spec's 10-value enum.

    Returns None if the raw value doesn't map to any enum member.
    Callers may treat None as a soft error and log it — the ingest
    scripts should count and report unmapped raw values so this table
    can be extended.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    return _STAGE_ALIASES.get(key)


def normalize_stage_list(raws: Iterable[str]) -> list[str]:
    """Normalize a list of stage strings; drop unmappable entries; dedup."""
    out: list[str] = []
    seen: set[str] = set()
    for r in raws or []:
        n = normalize_stage(r)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ─────────────────────────────────────────────────────────────────────────
# List-wrapping helper for entity spec list-typed columns
# ─────────────────────────────────────────────────────────────────────────


def to_list(value) -> list:
    """Wrap a scalar into a single-element list, or pass through if list.

    Used by ingest scripts to normalize scalar source values (e.g.
    findfunding's single-state `location` field) into the list<str>
    shape required by the entity spec for `investor_type`,
    `location_state`, `location_city`.

    Handles:
    - None → [] (empty list; distinguishes "no value known" from "empty")
    - [] / list → returned as-is (already list)
    - scalar → [scalar]
    - "" (empty string) → [] (treat as no value)
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str) and not value.strip():
        return []
    return [value]


# ─────────────────────────────────────────────────────────────────────────
# Investor type enum (entity spec column 13)
# ─────────────────────────────────────────────────────────────────────────

INVESTOR_TYPE_ENUM = (
    "institutional_vc",
    "cvc",
    "angel",
    "syndicate",
    "family_office",
    "accelerator",
)


# ─────────────────────────────────────────────────────────────────────────
# Confidence defaults (from provenance spec)
# ─────────────────────────────────────────────────────────────────────────

# (source, field-kind) → confidence value
# field-kind is a coarse tag chosen by the ingest script based on how the
# field was derived (e.g. structured API field vs. free-text regex vs.
# curator-set). The merge step reads this table when constructing the
# provenance rows.
SOURCE_CONFIDENCE = {
    ("wikidata", "structured_property"): 0.95,  # P856 / P571 / P159 / P17
    ("wikidata", "label"): 0.90,
    ("wikidata", "subclass_derived"): 0.85,  # investor_type from P31 subclass tree
    ("findfunding", "structured"): 0.90,
    ("findfunding", "implicit_type"): 0.85,  # institutional_vc from site scope
    ("findfunding", "text_extracted"): 0.60,
    # manual_seed lowered from 1.00 → 0.80 on 2026-07-28 (W8 pre-implementation).
    # Reason: v0.1 seed is AI-drafted from Claude's Jan-2026 training data with
    # E-policy manual patches for 8 real errors, not truly human-verified.
    # Uniform 1.00 overstated authority relative to Wikidata's community-maintained
    # structured properties. See _PROGRESS.md → W8 pre-implementation revision.
    ("manual_seed", "curator"): 0.80,
    ("manual_seed", "qid_cross_ref"): 0.80,
    # url_verify (W9b, opened 2026-07-28): Step-1 URL 全库验证 pass. curl_verified
    # means one of the existing sources' URLs curl-succeeded when canonical did not
    # (provenance-fallback). tavily_verified means all curl attempts on existing-source
    # URLs failed and Tavily search + double-verify (domain contains normalized_name
    # AND response HTML contains firm name) found a new working URL. Both = 0.98:
    # url_verify represents LIVE evidence (HTTP-verified alive URL), which is
    # qualitatively stronger than any other source's structured but potentially
    # stale record — so it must exceed wikidata's structured_property (0.95) by
    # numeric margin (not just SOURCE_PRIORITY tie-break, which wikidata wins).
    # First W9b merge attempt found Lightspeed's canonical stuck at wikidata's dead
    # www.lsvp.com because url_verify=wikidata=0.95 tied and wikidata's priority=0
    # won the tie; bumping url_verify to 0.98 breaks the tie by margin.
    ("url_verify", "curl_verified"): 0.98,
    ("url_verify", "tavily_verified"): 0.98,
}

# Source priority for canonical-selection tie-breaks (lower = higher priority).
# Only applied when confidence is numerically tied. Applied universally to
# `investor_id` regardless of confidence (each source has one canonical ID
# per firm; we pick the source-authoritative one).
SOURCE_PRIORITY = {"wikidata": 0, "findfunding": 1, "manual_seed": 2, "url_verify": 3}


# ─────────────────────────────────────────────────────────────────────────
# Investor ID lookup (M2-Step-6: canonical investor_id from entity table)
# ─────────────────────────────────────────────────────────────────────────

_ENTITY_PATH = os.environ.get("INVESTOR_ENTITY_PATH")
_INVESTOR_ID_CACHE = None


def load_investor_id_map() -> dict[str, str]:
    """Load entity table and return {make_slug(name): investor_id} mapping.
    
    All ingest scripts should use this to look up the canonical investor_id
    rather than generating their own. Cached after first call.
    """
    global _INVESTOR_ID_CACHE
    if _INVESTOR_ID_CACHE is not None:
        return _INVESTOR_ID_CACHE
    
    import pyarrow.parquet as pq
    from pathlib import Path
    
    if not _ENTITY_PATH:
        raise RuntimeError("set INVESTOR_ENTITY_PATH to the canonical investor Parquet")
    p = Path(_ENTITY_PATH)
    if not p.exists():
        raise FileNotFoundError(f"investor entity table not found: {p}")
    
    df = pq.read_table(p, columns=["investor_id", "name"]).to_pandas()
    _INVESTOR_ID_CACHE = {make_slug(row["name"]): row["investor_id"] for _, row in df.iterrows()}
    return _INVESTOR_ID_CACHE


def get_investor_id(name: str) -> str | None:
    """Look up canonical investor_id by name. Returns None if not found."""
    id_map = load_investor_id_map()
    slug = make_slug(name)
    return id_map.get(slug)
