"""Comprehensive outcome-label leakage scan for pool 1 candidate cards.

Purpose: A4 preprocessing generates candidate_card.md from Crunchbase categories +
website_body_text (fetched 2026-06-29). This scrape captures CURRENT state, not
founding-era state. So cards may contain post-founding outcome-related information
(IPO tickers, acquisition text, defunct domain markers, funding rounds, etc.) that
would trivially reveal the SUCCESS/FAILURE label to any downstream model.

This scan looks for 6 categories of leakage and reports per-row + per-category
+ summary stats.

CATEGORIES:
  1. Public/IPO indicators (ticker symbols, "publicly traded", "listed on")
  2. Acquisition/merger indicators ("acquired by", "wholly owned by", "X is Y")
  3. Shutdown/defunct indicators ("shut down", "domain for sale", "no longer active")
  4. Post-founding funding rounds (Series B/C/D..., "$X raised", "unicorn")
  5. Post-founding temporal anchors (years > founding_year + 2 mentioned as EVENTS)
  6. Structural card field leaks (Public/private = public)

Scan scope:
  - Primary: "Original proposal description" section (raw text pipeline consumes)
  - Secondary: LLM "Notes" sections (may reflect outcome knowledge)
  - Structural: card metadata fields

Output:
  - /tmp/leakage_scan_output.json — per-row leakage flags
  - /tmp/leakage_scan_report.md — human-readable summary + samples
"""

import csv
import json
import os
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

csv.field_size_limit(sys.maxsize)

POOL_1_DIR = Path(os.environ.get(
    "POOL_1_DIR", str(Path.home() / "_data/pipeline_benchmark/crunchbase_filtered")))
POOL_1_CSVS = [
    POOL_1_DIR / "a5_train_results_v15a_iter4.csv",
    POOL_1_DIR / "a5_train_results_v15a_iter5.csv",
]
CARDS_DIR = Path(os.environ.get("CARDS_DIR", str(Path.home() / "workspace_a4_cards/working")))
OUT_JSON = Path(os.environ.get("LEAKAGE_OUT_JSON", "/tmp/leakage_scan_output.json"))
OUT_MD = Path(os.environ.get("LEAKAGE_OUT_MD", "/tmp/leakage_scan_report.md"))

SUCCESS_TRUTH = {"POSITIVE_IPO", "POSITIVE_LATE_STAGE_FUNDED",
                 "POSITIVE_ACQUIRED", "EQUIVOCAL_DELISTED"}
FAILURE_TRUTH = {"NEGATIVE_CLOSED", "NEGATIVE_NO_TRACTION"}


# ============================================================================
# LEAKAGE PATTERNS
# ============================================================================

# Category 1: Public / IPO indicators
IPO_PATTERNS = [
    # Exchange:Ticker format like "NASDAQ: ABCD" or "NYSE:XYZ"
    (r'\b(NYSE|NASDAQ|NASDAQGS|NASDAQGM|NYSEAMEX|AMEX|OTC|OTCQX|OTCQB|OTCBB|OTCPINK|LSE|LON|HKEX|SEHK|HKG|ASX|SGX|BSE|NSE|SHA|SSE|SHSE|SZSE|SZSC|BOM|TSX|TSXV|CVE|NEO|BME|EPA|ETR|FSE|TASE|KRX|KOSDAQ|BMV|BOVESPA|B3|SET|IDX|JSE|EGX|MOEX|SGXNET|CBOE)\s*:\s*[A-Z0-9\.]{1,8}\b',
     'ticker_exchange_colon'),
    # Parenthetical form: (NASDAQ: ABCD) or (V: SPOT)
    (r'\([A-Z]{1,6}\s*:\s*[A-Z0-9\.]{2,8}\)', 'ticker_paren'),
    # Explicit "listed on X" / "traded on X"
    (r'\b(?:listed on|traded on|floats? on|floated on|debuted on)\s+(?:the\s+)?(?:NYSE|NASDAQ|LSE|HKEX|TSX|ASX|SGX|BSE|NSE)', 'listed_on_exchange'),
    (r'\b(?:publicly[\s-]traded|publicly[\s-]listed|went public|IPO(?:ed|\'d|d)?\s+in|initial public offering)\b',
     'public_keyword'),
    (r'\bIPO\s+(?:in|on|at)\s+\d{4}', 'ipo_year'),
    (r'\bmarket\s+cap(?:italization)?\s+of\s+\$\d', 'market_cap'),
    (r'\bshare\s+price\s+of\s+\$\d', 'share_price'),
    (r'\btrading\s+at\s+\$\d', 'trading_at'),
    # Structural field: Public/private = public
    (r'\*\*Public/private\*\*:\s*`?\s*public\s*`?', 'field_public_yes'),
]

# Category 2: Acquisition / merger indicators
ACQUISITION_PATTERNS = [
    (r'\bacquired\s+by\s+[A-Z]', 'acquired_by_upper'),
    (r'\bacquired\s+for\s+\$', 'acquired_for_dollar'),
    (r'\b(?:acquisition|takeover|buyout)\s+by\s+[A-Z]', 'acquisition_by'),
    (r'\bwas\s+acquired\b', 'was_acquired'),
    (r'\bhas\s+been\s+acquired\b', 'has_been_acquired'),
    (r'\bgot\s+acquired\b', 'got_acquired'),
    (r'\bwas\s+purchased\s+by\s+[A-Z]', 'was_purchased_by'),
    (r'\bpurchased\s+by\s+[A-Z]', 'purchased_by'),
    (r'\bbought\s+by\s+[A-Z]', 'bought_by'),
    (r'\bmerged\s+(?:with|into)\s+[A-Z]', 'merged_with'),
    (r'\bmerger\s+(?:with|of)\s+[A-Z]', 'merger_of'),
    (r'\bwholly[\s-]owned\s+subsidiary\s+of\b', 'subsidiary_of'),
    (r'\bnow\s+(?:part\s+of|owned\s+by|a\s+subsidiary\s+of)\s+[A-Z]', 'now_part_of'),
    (r'\bnow\s+known\s+as\s+[A-Z]', 'now_known_as'),
    (r'\bnow\s+operates\s+as\s+[A-Z]', 'now_operates_as'),
    (r'\bformerly\s+known\s+as\s+[A-Z]', 'formerly_known_as'),
    (r'\brebranded\s+(?:as|to)\s+[A-Z]', 'rebranded'),
    (r'\bmerging\s+(?:with|into)\s+[A-Z]', 'merging_with'),
]

# Category 3: Shutdown / defunct indicators
SHUTDOWN_PATTERNS = [
    (r'\b(?:shut\s+down|shutdown|shuttered|ceased\s+operations?|ceased\s+trading|wound\s+down|wound\s+up|dissolved|liquidat(?:ed|ion|ing)|bankruptcy|bankrupt|insolven(?:t|cy))\b',
     'shutdown_keyword'),
    (r'\bno\s+longer\s+(?:active|operating|in\s+business|trading|exists?)\b', 'no_longer_active'),
    (r'\b(?:defunct|inactive|dormant|abandoned|discontinued|folded|failed\s+company)\b', 'defunct'),
    (r'\bdomain\s+(?:for\s+sale|resale|being\s+resold|is\s+being\s+marketed|for-sale|has\s+been\s+resold)\b',
     'domain_for_sale'),
    (r'\bpremium\s+domain\b', 'premium_domain'),
    (r'\bacquisitions?@[a-zA-Z0-9\.\-_]+', 'domain_broker_email'),
    (r'\b(?:DomainPointe|Sedo|GoDaddy Auctions|Undeveloped|Efty|Dan\.com|Squadhelp|BrandBucket)\b', 'domain_broker_name'),
    (r'\bwent\s+out\s+of\s+business\b', 'went_out_of_business'),
    (r'\bceased\s+to\s+(?:exist|operate|trade)\b', 'ceased_to_exist'),
    (r'\b(?:startup|company|business)\s+failed\b', 'startup_failed'),
    (r'\bshut\s+its\s+doors?\b', 'shut_its_doors'),
    (r'\bpivoted\s+off\b', 'pivoted_off'),
    # Domain-parking / for-sale landing signals
    (r'\b(?:this\s+domain|the\s+domain)\s+is\s+for\s+sale', 'this_domain_for_sale'),
    (r'\bavailable\s+for\s+purchase\b', 'domain_available_for_purchase'),
    # Zombie / no-response signals
    (r'\bwebsite\s+(?:is\s+)?(?:down|offline|not\s+responding|inaccessible)\b', 'website_down'),
    (r'\b404\s+(?:Not\s+Found|error)?\b', 'http_404'),
    (r'\bdead\s+link\b', 'dead_link'),
]

# Category 4: Post-founding funding rounds & valuations
FUNDING_PATTERNS = [
    # Named series - Series A can be early; Series B+ is post-founding
    (r'\bSeries\s+[BCDEFGHIJ][+]?\b', 'series_bcdefgh'),
    (r'\braised\s+\$?\d+(?:\.\d+)?\s*[MBKmbk]?\s+(?:in|from|by|Series|Round|funding|financing)',
     'raised_amount'),
    (r'\$\d+(?:\.\d+)?\s*(?:million|billion|B|M)\s+(?:raised|in\s+funding|Series|investment|round|financing)\b',
     'dollar_amount_raised'),
    (r'\bunicorn(?:\s+status)?\b', 'unicorn'),
    (r'\$\d+(?:\.\d+)?\s*(?:billion|B)\s+(?:valuation|valued\s+at|market\s+cap)', 'billion_valuation'),
    (r'\b(?:pre|post)-money\s+valuation\b', 'pre_post_money'),
    (r'\bpost-Series\s+[A-J]', 'post_series'),
    (r'\bexited\s+(?:at|for)\s+\$', 'exited_at_dollar'),
    (r'\bexit\s+of\s+\$\d', 'exit_of_dollar'),
    (r'\bIPO\'?d?\s+at\s+\$', 'ipod_at'),
    (r'\bvaluation\s+of\s+\$\d+(?:\.\d+)?\s*(?:billion|million|B|M)', 'valuation_dollar'),
]

# Category 6: Retrospective/observed-outcome language (LLM directly stating outcome)
RETROSPECTIVE_PATTERNS = [
    (r'\balready\s+realized\b', 'already_realized'),
    (r'\bobserved\b(?=.*(?:acquisition|shutdown|closed|wound|defunct))', 'observed_outcome'),
    (r'\bhas\s+wound\s+down\b', 'has_wound_down'),
    (r'\bwas\s+not\s+standalone-defensible\b', 'not_standalone'),
    (r'\bapparent\s+business\s+discontinuity\b', 'apparent_discontinuity'),
    (r'\bterminal\s+to\s+this\s+candidate\b', 'terminal_candidate'),
    (r'\bhistorically\s+successful\b', 'historically_successful'),
    (r'\bexit(?:ed)?\s+(?:via|through|by)\s+(?:IPO|acquisition)', 'exit_via'),
    (r'\bhas\s+since\s+(?:been|become|failed|shut|closed|acquired|been\s+acquired)', 'has_since'),
    (r'\bcurrent(?:ly)?\s+(?:trading|listed|traded)\s+on\b', 'currently_trading'),
    (r'\brecent\s+(?:acquisition|shutdown|closure|wind[- ]?down)', 'recent_event'),
]


# ============================================================================
# CARD PARSING
# ============================================================================

def extract_founding_year(card_text):
    m = re.search(r'\*\*Founding\s+year\*\*:\s*`?(\d{4})`?', card_text)
    if m: return int(m.group(1))
    m = re.search(r'Founded\s+(\d{4})-\d{2}-\d{2}', card_text)
    if m: return int(m.group(1))
    return None


def extract_public_private(card_text):
    m = re.search(r'\*\*Public/private\*\*:\s*`?\s*(public|private|equivocal|delisted)\s*`?',
                  card_text, re.IGNORECASE)
    if m: return m.group(1).lower()
    return None


def extract_proposal_desc(card_text):
    """Extract the 'Original proposal description' section (raw pipeline input)."""
    # Find start
    start_m = re.search(r'\*\*Original proposal description\*\*:', card_text)
    if not start_m:
        return ""
    start = start_m.end()
    # End = next ** (bold field) or --- separator
    end_m = re.search(r'\n\*\*[^\*]+\*\*:|\n---', card_text[start:])
    if end_m:
        return card_text[start:start + end_m.start()].strip()
    return card_text[start:start + 3000].strip()


def extract_llm_sections(card_text):
    """Extract all LLM-generated sections (1-5 including their Notes)."""
    m = re.search(r'---\s*\n\s*## 1\.', card_text)
    if not m:
        return ""
    return card_text[m.start():]


# Extract years from text (for temporal analysis)
YEAR_RE = re.compile(r'\b(20\d{2})\b')


def find_post_founding_years(text, founding_year, grace=2):
    """Find years mentioned that are > founding_year + grace."""
    if founding_year is None:
        return []
    years = YEAR_RE.findall(text)
    return [int(y) for y in years if int(y) > founding_year + grace]


# ============================================================================
# SCANNING
# ============================================================================

def scan_text(text, patterns):
    """Return dict of pattern_name → list of matches (str)."""
    hits = defaultdict(list)
    for pat, name in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            match_str = m.group(0)
            hits[name].append(match_str)
    return dict(hits)


def scan_card(card_text, card_id):
    founding_year = extract_founding_year(card_text)
    public_private = extract_public_private(card_text)
    proposal_desc = extract_proposal_desc(card_text)
    llm_sections = extract_llm_sections(card_text)

    # Run patterns on both proposal_desc (primary) and llm_sections (secondary)
    result = {
        "card_id": card_id,
        "founding_year": founding_year,
        "public_private_field": public_private,
        "proposal_desc_length": len(proposal_desc),
        "llm_sections_length": len(llm_sections),
        "leakage": {
            "proposal_desc": {},
            "llm_sections": {},
        },
        "temporal": {},
    }

    for name, patterns in [
        ("ipo_public", IPO_PATTERNS),
        ("acquisition", ACQUISITION_PATTERNS),
        ("shutdown", SHUTDOWN_PATTERNS),
        ("funding", FUNDING_PATTERNS),
        ("retrospective", RETROSPECTIVE_PATTERNS),
    ]:
        result["leakage"]["proposal_desc"][name] = scan_text(proposal_desc, patterns)
        result["leakage"]["llm_sections"][name] = scan_text(llm_sections, patterns)

    # Temporal analysis
    result["temporal"]["proposal_desc_post_years"] = find_post_founding_years(proposal_desc, founding_year, grace=2)
    result["temporal"]["llm_sections_post_years"] = find_post_founding_years(llm_sections, founding_year, grace=2)

    # Compute severity
    severity_score = 0
    severity_reasons = []

    # Weight structural / explicit leaks highest
    if public_private == "public":
        severity_score += 3
        severity_reasons.append("field_public_yes")
    if result["leakage"]["proposal_desc"].get("ipo_public"):
        severity_score += 3
        severity_reasons.append("ipo_in_proposal")
    if result["leakage"]["proposal_desc"].get("acquisition"):
        severity_score += 3
        severity_reasons.append("acquisition_in_proposal")
    if result["leakage"]["proposal_desc"].get("shutdown"):
        severity_score += 3
        severity_reasons.append("shutdown_in_proposal")
    if result["leakage"]["proposal_desc"].get("funding"):
        severity_score += 2
        severity_reasons.append("funding_in_proposal")
    if result["temporal"]["proposal_desc_post_years"]:
        # Post-founding years in raw proposal → suspicious
        severity_score += 1
        severity_reasons.append(f"post_founding_years_in_proposal:{result['temporal']['proposal_desc_post_years']}")

    # LLM section leaks weighted lower (may be legitimate commentary)
    if result["leakage"]["llm_sections"].get("retrospective"):
        severity_score += 1
        severity_reasons.append("retrospective_in_llm")

    result["severity_score"] = severity_score
    result["severity_reasons"] = severity_reasons
    result["severity"] = "HIGH" if severity_score >= 3 else ("MED" if severity_score >= 1 else "LOW")

    return result


# ============================================================================
# MAIN
# ============================================================================

def truth_class(outcome):
    if outcome in SUCCESS_TRUTH: return "SUCCESS"
    if outcome in FAILURE_TRUTH: return "FAILURE"
    return None


def load_pool1():
    rows = []
    for p in POOL_1_CSVS:
        for r in csv.DictReader(open(p)):
            tc = truth_class(r.get("outcome_label", ""))
            if tc is None: continue
            rows.append({"id": r["id"], "name": r["name"], "outcome": r["outcome_label"], "truth": tc})
    return rows


def main():
    print("Loading pool 1...", flush=True)
    pool1 = load_pool1()
    print(f"  {len(pool1)} rows")

    print("\nScanning candidate cards for leakage...", flush=True)
    results = []
    n_high = n_med = n_low = 0
    n_missing = 0
    by_truth = {"SUCCESS": Counter(), "FAILURE": Counter()}
    by_outcome = defaultdict(Counter)

    for i, row in enumerate(pool1):
        card_path = CARDS_DIR / row["id"] / "candidate_card.md"
        if not card_path.exists():
            n_missing += 1
            continue
        card_text = card_path.read_text(errors="ignore")
        scan = scan_card(card_text, row["id"])
        scan["name"] = row["name"]
        scan["outcome"] = row["outcome"]
        scan["truth"] = row["truth"]
        results.append(scan)

        by_truth[row["truth"]][scan["severity"]] += 1
        by_outcome[row["outcome"]][scan["severity"]] += 1

        if scan["severity"] == "HIGH": n_high += 1
        elif scan["severity"] == "MED": n_med += 1
        else: n_low += 1

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(pool1)} scanned, HIGH={n_high} MED={n_med} LOW={n_low}", flush=True)

    print(f"\nMissing cards: {n_missing}")
    print(f"\n=== Overall leakage severity ===")
    print(f"  HIGH: {n_high} ({n_high/len(results)*100:.1f}%)")
    print(f"  MED:  {n_med} ({n_med/len(results)*100:.1f}%)")
    print(f"  LOW:  {n_low} ({n_low/len(results)*100:.1f}%)")

    print(f"\n=== Severity by truth ===")
    for t in ["SUCCESS", "FAILURE"]:
        tot = sum(by_truth[t].values())
        h = by_truth[t]["HIGH"]
        m = by_truth[t]["MED"]
        l = by_truth[t]["LOW"]
        print(f"  {t} (n={tot}): HIGH={h} ({h/tot*100:.1f}%) MED={m} ({m/tot*100:.1f}%) LOW={l} ({l/tot*100:.1f}%)")

    print(f"\n=== Severity by outcome label ===")
    for outcome in sorted(by_outcome.keys()):
        tot = sum(by_outcome[outcome].values())
        h = by_outcome[outcome]["HIGH"]
        m = by_outcome[outcome]["MED"]
        l = by_outcome[outcome]["LOW"]
        print(f"  {outcome:35s} (n={tot:3d}): HIGH={h:3d} ({h/tot*100:5.1f}%) MED={m:3d} ({m/tot*100:5.1f}%) LOW={l:3d} ({l/tot*100:5.1f}%)")

    # Pattern-level stats
    pattern_totals = defaultdict(int)
    for r in results:
        for section in ["proposal_desc", "llm_sections"]:
            for cat, hits in r["leakage"][section].items():
                for pat_name in hits.keys():
                    pattern_totals[f"{section}.{cat}.{pat_name}"] += 1
    print(f"\n=== Top pattern hits (across all rows) ===")
    for pat, cnt in sorted(pattern_totals.items(), key=lambda x: -x[1])[:30]:
        print(f"  {pat}: {cnt}")

    # Save JSON
    save_data = {
        "n_pool1_rows": len(pool1),
        "n_scanned": len(results),
        "n_missing_cards": n_missing,
        "severity_counts": {"HIGH": n_high, "MED": n_med, "LOW": n_low},
        "severity_by_truth": {t: dict(c) for t, c in by_truth.items()},
        "severity_by_outcome": {o: dict(c) for o, c in by_outcome.items()},
        "pattern_totals": dict(pattern_totals),
        "rows": results,
    }
    OUT_JSON.write_text(json.dumps(save_data, indent=2, default=str))
    print(f"\nSaved: {OUT_JSON}")

    # Write markdown report
    with open(OUT_MD, "w") as f:
        f.write(f"# Pool 1 Candidate Card Leakage Scan\n\n")
        f.write(f"**Total rows scanned**: {len(results)} / {len(pool1)}\n\n")
        f.write(f"## Severity distribution\n\n")
        f.write(f"- HIGH: {n_high} ({n_high/len(results)*100:.1f}%)\n")
        f.write(f"- MED: {n_med} ({n_med/len(results)*100:.1f}%)\n")
        f.write(f"- LOW: {n_low} ({n_low/len(results)*100:.1f}%)\n\n")

        f.write(f"## Severity by outcome\n\n")
        f.write(f"| Outcome | N | HIGH | MED | LOW |\n|---|---:|---:|---:|---:|\n")
        for outcome in sorted(by_outcome.keys()):
            tot = sum(by_outcome[outcome].values())
            h = by_outcome[outcome]["HIGH"]
            m = by_outcome[outcome]["MED"]
            l = by_outcome[outcome]["LOW"]
            f.write(f"| {outcome} | {tot} | {h} ({h/tot*100:.1f}%) | {m} ({m/tot*100:.1f}%) | {l} ({l/tot*100:.1f}%) |\n")

        f.write(f"\n## Sample HIGH-severity flagged cards (top 10 per truth)\n\n")
        for truth in ["SUCCESS", "FAILURE"]:
            f.write(f"\n### {truth} — HIGH severity examples\n\n")
            samples = [r for r in results if r["truth"] == truth and r["severity"] == "HIGH"][:10]
            for s in samples:
                f.write(f"**{s['name']}** ({s['outcome']}, id={s['card_id']})\n")
                f.write(f"- severity_score: {s['severity_score']}\n")
                f.write(f"- reasons: {s['severity_reasons']}\n")
                # Show top hits
                for section in ["proposal_desc", "llm_sections"]:
                    for cat, hits in s["leakage"][section].items():
                        for pat, matches in hits.items():
                            if matches:
                                f.write(f"  - {section}.{cat}.{pat}: {matches[:3]}\n")
                f.write("\n")

    print(f"Report: {OUT_MD}")


if __name__ == "__main__":
    main()
