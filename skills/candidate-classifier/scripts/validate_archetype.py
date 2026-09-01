"""Validate archetype.json against [P1W2c §4.3] schema.

Usage: python3 validate_archetype.py --json-text "<json>"
       python3 validate_archetype.py --json-file <path>

Schema (per docs/archetype_taxonomy.html §4.3):
{
  "primary":    str — must be in CANONICAL_ARCHETYPES or "out-of-scope" (§4.2.2)
  "secondary":  str | null — optional, must be in CANONICAL_ARCHETYPES if present;
                              must be null when primary == "out-of-scope"
  "confidence": "robust" | "borderline"
  "cn_flag":    bool
  "reasoning":  str — 50-150 words
}

Exit 0 on pass, exit 1 on validation error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

CANONICAL_ARCHETYPES = {
    "Distribution",
    "OSS-on-rented-GPU",
    "Frontier-model-owner",
    "Hyperscaler-bundle",
    "CN-private",
    "Hardware-vertical",
    "Enterprise/Rental",
}

OUT_OF_SCOPE = "out-of-scope"
VALID_PRIMARIES = CANONICAL_ARCHETYPES | {OUT_OF_SCOPE}


def validate(data) -> list:
    errors = []

    if not isinstance(data, dict):
        return ["FAIL: archetype output must be a JSON object."]

    # primary required, must be canonical archetype OR "out-of-scope" (§4.2.2)
    primary = data.get("primary")
    if not primary:
        errors.append("FAIL: missing required field 'primary'.")
    elif primary not in VALID_PRIMARIES:
        errors.append(
            f"FAIL: primary='{primary}' not in valid set: "
            f"{sorted(CANONICAL_ARCHETYPES)} ∪ {{'{OUT_OF_SCOPE}'}}"
        )

    # secondary: if primary == "out-of-scope", secondary MUST be null;
    # otherwise (primary in CANONICAL_ARCHETYPES), secondary is optional but if
    # present must be canonical.
    secondary = data.get("secondary")
    if primary == OUT_OF_SCOPE:
        if secondary is not None and secondary != "":
            errors.append(
                f"FAIL: secondary must be null when primary='out-of-scope' "
                f"(got '{secondary}'); §4.2.2 forbids declared secondary on out-of-scope candidates."
            )
    else:
        if secondary is not None and secondary != "" and secondary not in CANONICAL_ARCHETYPES:
            errors.append(
                f"FAIL: secondary='{secondary}' not in canonical archetype set: "
                f"{sorted(CANONICAL_ARCHETYPES)}"
            )

    # confidence required, must be enum
    confidence = data.get("confidence")
    if confidence not in {"robust", "borderline"}:
        errors.append(f"FAIL: confidence='{confidence}' must be 'robust' or 'borderline'.")

    # cn_flag required, must be bool
    cn_flag = data.get("cn_flag")
    if not isinstance(cn_flag, bool):
        errors.append(f"FAIL: cn_flag must be true|false (got {type(cn_flag).__name__}).")

    # reasoning required, 50-150 words
    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str) or not reasoning.strip():
        errors.append("FAIL: reasoning must be a non-empty string.")
    else:
        # word count: roughly count CJK chars + ASCII words
        ascii_words = len(re.findall(r"\b\w+\b", reasoning))
        cjk_chars = len(re.findall(r"[一-鿿]", reasoning))
        # Heuristic: 1 CJK char ≈ 1 word
        approx_words = ascii_words + cjk_chars
        if approx_words < 30:
            errors.append(
                f"WARN: reasoning length ~{approx_words} words/chars (target 50-150 per spec)."
            )
        if approx_words > 250:
            errors.append(
                f"WARN: reasoning length ~{approx_words} words/chars (target 50-150 per spec); "
                "trim to focus on key archetype-defining features."
            )

    # Borderline + missing secondary is suspicious — but only when primary is a
    # declared archetype. For primary == "out-of-scope", borderline + null
    # secondary is the expected schema (the borderline is about in-scope vs
    # out-of-scope edge, not about cross-archetype).
    if (
        confidence == "borderline"
        and not secondary
        and primary in CANONICAL_ARCHETYPES
    ):
        errors.append(
            "WARN: confidence='borderline' but no secondary archetype provided — "
            "borderline on a declared archetype implies cross-archetype, expected secondary."
        )

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate archetype.json schema")
    parser.add_argument("--json-text", help="Inline JSON text")
    parser.add_argument("--json-file", help="Path to JSON file")
    args = parser.parse_args()

    if args.json_text:
        try:
            data = json.loads(args.json_text)
        except json.JSONDecodeError as e:
            sys.exit(f"FAIL: invalid JSON: {e}")
    elif args.json_file:
        try:
            data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            sys.exit(f"FAIL: cannot read/parse JSON file: {e}")
    else:
        sys.exit("ERROR: provide --json-text or --json-file")

    errors = validate(data)
    if not errors:
        print("OK: archetype.json schema valid")
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
