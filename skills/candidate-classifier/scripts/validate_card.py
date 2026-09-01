"""Validate candidate card markdown structure (v1.5).

Usage: python3 validate_card.py --card-text "<markdown>"
       python3 validate_card.py --card-file <path>

Checks:
- 5 required dim sections present (Revenue model / Customer segmentation / Cost structure /
  Differentiation·moat / Strategic vulnerabilities)
- No "archetype" field anywhere (candidate card must not contain archetype)
- Required forward-looking metadata fields present (Slug, Public/private, Founding year,
  Original proposal description, Closest existing vendor analogues)
- Founding year value is a 4-digit year (1900-2099)
- Each dim section has at least 50 words (sanity check, target 80-150)
- Strategic vulnerabilities dim has at least 1 enumerated risk (cannot be empty per spec)

v1.5: Founding year is REQUIRED. Downstream M-check and U-check use it for time-bounded
Tavily retrieval window [founding_year - 3y, founding_year].

Exit 0 on pass, exit 1 on validation error with diagnostic message.
"""

import argparse
import re
import sys
from pathlib import Path

REQUIRED_DIMS = [
    "Revenue model",
    "Customer segmentation",
    "Cost structure",
    "Differentiation",  # match "Differentiation / moat" or "Differentiation·moat"
    "Strategic vulnerabilities",
]

REQUIRED_METADATA = [
    "Slug",
    "Public/private",
    "Founding year",
    "Original proposal description",
    "Closest existing vendor analogues",
]


def validate(card_text: str) -> list:
    errors = []

    # 1. Check no archetype field
    if re.search(r"^\s*\*?\*?Archetype\*?\*?\s*[:：]", card_text, flags=re.MULTILINE | re.IGNORECASE):
        errors.append(
            "FAIL: candidate card contains an 'Archetype' field. Candidate card must not "
            "contain archetype — archetype is separate metadata in archetype.json."
        )

    # 2. Check 5 required dim sections
    for dim in REQUIRED_DIMS:
        # Match heading like "## 1. Revenue model" or "## Revenue model" (case-insensitive)
        pattern = rf"^\s*#{{1,3}}\s+(?:\d+\.\s+)?{re.escape(dim)}"
        if not re.search(pattern, card_text, flags=re.MULTILINE | re.IGNORECASE):
            errors.append(f"FAIL: missing dim section heading '{dim}' (per vendor card 5-dim schema).")

    # 3. Check required metadata fields
    for field in REQUIRED_METADATA:
        if field.lower() not in card_text.lower():
            errors.append(f"FAIL: missing metadata field '{field}'.")

    # 3a. Founding year value sanity check — must be a 4-digit year in [1900, 2099].
    # Required because downstream M-check / U-check parse this for time-bounded Tavily.
    founding_match = re.search(
        r"\*?\*?Founding\s*year\*?\*?\s*[:：]\s*[`'\"]?(\d{4})[`'\"]?",
        card_text,
        flags=re.IGNORECASE,
    )
    if founding_match:
        year = int(founding_match.group(1))
        if year < 1900 or year > 2099:
            errors.append(
                f"FAIL: Founding year value '{year}' outside plausible range [1900, 2099]."
            )
    elif any("FAIL: missing metadata field 'Founding year'" in e for e in errors):
        # Already flagged above as missing — skip
        pass
    else:
        # Field present but value not parseable as year
        errors.append(
            "FAIL: Founding year field present but value not a parseable 4-digit year. "
            "Expected format: `**Founding year**: <YYYY>`."
        )

    # 4. Check each dim section has at least 50 words (sanity)
    for dim in REQUIRED_DIMS:
        section_match = re.search(
            rf"#{{1,3}}\s+(?:\d+\.\s+)?{re.escape(dim)}.*?(?=^#{{1,3}}\s+(?:\d+\.\s+)?\w|\Z)",
            card_text,
            flags=re.MULTILINE | re.IGNORECASE | re.DOTALL,
        )
        if section_match:
            section_text = section_match.group(0)
            word_count = len(re.findall(r"\b\w+\b", section_text))
            if word_count < 50:
                errors.append(
                    f"WARN: dim section '{dim}' has only {word_count} words (target 80-150 per spec)."
                )

    # 5. Strategic vulnerabilities dim must have at least 1 risk (look for bullet or numbered list)
    vuln_match = re.search(
        r"#{1,3}\s+(?:\d+\.\s+)?Strategic\s+vulnerabilities.*?(?=^#{1,3}\s+(?:\d+\.\s+)?\w|\Z)",
        card_text,
        flags=re.MULTILINE | re.IGNORECASE | re.DOTALL,
    )
    if vuln_match:
        vuln_text = vuln_match.group(0)
        # At least 1 bullet (- or *) or numbered list item; allow any non-whitespace
        # after the bullet marker (e.g. **bold**, _emphasis_, plain text)
        if not re.search(r"^\s*[-*]\s+\S", vuln_text, flags=re.MULTILINE) and not re.search(
            r"^\s*\d+\.\s+\S", vuln_text, flags=re.MULTILINE
        ):
            errors.append(
                "FAIL: Strategic vulnerabilities dim must enumerate at least 1 risk (bullet or "
                "numbered list). This dim cannot be empty."
            )

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate candidate card markdown structure")
    parser.add_argument("--card-text", help="Inline markdown text")
    parser.add_argument("--card-file", help="Path to markdown file")
    args = parser.parse_args()

    if args.card_text:
        text = args.card_text
    elif args.card_file:
        text = Path(args.card_file).read_text(encoding="utf-8")
    else:
        sys.exit("ERROR: provide --card-text or --card-file")

    errors = validate(text)
    if not errors:
        print("OK: candidate card structure valid (5 dims + metadata + non-empty vulnerabilities)")
        return 0

    fail_count = sum(1 for e in errors if e.startswith("FAIL"))
    warn_count = sum(1 for e in errors if e.startswith("WARN"))
    for e in errors:
        print(e, file=sys.stderr)
    if fail_count > 0:
        print(f"\n{fail_count} fail(s), {warn_count} warn(s)", file=sys.stderr)
        return 1
    print(f"\n0 fail(s), {warn_count} warn(s) — passed with warnings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
