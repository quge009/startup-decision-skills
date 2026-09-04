"""Validate M-check JSON against [P1W4 §6.1] schema.

Usage: python3 validate_m_schema.py --json-file <path>
       python3 validate_m_schema.py --json-text "<json>"
"""

import argparse
import json
import re
import sys
from pathlib import Path

VALID_VERDICTS = {"PASS", "WARN", "FAIL"}
VALID_EMOJIS = {"✅", "⚠", "🔴"}
VERDICT_TO_EMOJI = {"PASS": "✅", "WARN": "⚠", "FAIL": "🔴"}
VALID_PORTER_LEVELS = {"low", "med", "high"}
VALID_PORTER_DISCOUNT = {0.3, 0.5, 0.7, 1.0}


def validate(data) -> list:
    errors = []
    if not isinstance(data, dict):
        return ["FAIL: M-check output must be a JSON object."]

    # verdict + emoji
    verdict = data.get("verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"FAIL: verdict='{verdict}' not in {VALID_VERDICTS}")
    emoji = data.get("verdict_emoji")
    if emoji not in VALID_EMOJIS:
        errors.append(f"FAIL: verdict_emoji='{emoji}' not in {VALID_EMOJIS}")
    elif verdict in VERDICT_TO_EMOJI and emoji != VERDICT_TO_EMOJI[verdict]:
        errors.append(f"FAIL: verdict_emoji mismatch verdict.")

    if not data.get("verdict_text"):
        errors.append("FAIL: missing verdict_text.")

    # tam
    tam = data.get("tam")
    if not isinstance(tam, dict):
        errors.append("FAIL: tam must be object.")
    else:
        for k in ("value_b", "scope", "source"):
            if tam.get(k) in (None, ""):
                errors.append(f"FAIL: tam.{k} required.")
        rng = tam.get("range_b")
        if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng)):
            errors.append("FAIL: tam.range_b must be [low, high] numeric pair.")

    # sam
    sam = data.get("sam")
    if not isinstance(sam, dict):
        errors.append("FAIL: sam must be object.")
    else:
        if not sam.get("subset_descriptor"):
            errors.append("FAIL: sam.subset_descriptor required.")
        pct = sam.get("pct_of_tam")
        if not isinstance(pct, (int, float)) or not 0 <= pct <= 1:
            errors.append("FAIL: sam.pct_of_tam must be number in [0.0, 1.0].")

    # som — critical, has the headroom formula inputs
    som = data.get("som")
    if not isinstance(som, dict):
        errors.append("FAIL: som must be object.")
    else:
        locked = som.get("locked_pct")
        if not isinstance(locked, (int, float)) or not 0 <= locked <= 1:
            errors.append("FAIL: som.locked_pct must be number in [0.0, 1.0].")
        discount = som.get("porter_discount")
        if discount not in VALID_PORTER_DISCOUNT:
            errors.append(f"FAIL: som.porter_discount={discount} must be in {VALID_PORTER_DISCOUNT}.")
        headroom = som.get("headroom_b")
        if not isinstance(headroom, (int, float)) or headroom < 0:
            errors.append("FAIL: som.headroom_b must be non-negative number.")
        # Sanity: check formula consistency if all present
        # Per [P1W4 §1.2] step 4: SOM_headroom = TAM × pct_of_tam × (1 − locked_pct) × porter_discount
        sam_pct = sam.get("pct_of_tam") if isinstance(sam, dict) else None
        if (
            isinstance(tam, dict)
            and isinstance(tam.get("value_b"), (int, float))
            and isinstance(sam_pct, (int, float))
            and isinstance(locked, (int, float))
            and discount in VALID_PORTER_DISCOUNT
            and isinstance(headroom, (int, float))
        ):
            expected = tam["value_b"] * sam_pct * (1 - locked) * discount
            if abs(headroom - expected) > 0.05 * max(headroom, expected, 0.01):
                errors.append(
                    f"WARN: som.headroom_b={headroom} differs from formula "
                    f"TAM × pct_of_tam × (1 − locked_pct) × porter_discount = {expected:.3f} (>5% deviation). "
                    "Per [P1W4 §1.2] step 4, headroom_b must follow three-layer chain TAM-SAM-SOM."
                )

    # porter_forces
    pf = data.get("porter_forces")
    if not isinstance(pf, dict):
        errors.append("FAIL: porter_forces must be object.")
    else:
        for k in ("rivalry", "substitutes", "new_entrants"):
            if pf.get(k) not in VALID_PORTER_LEVELS:
                errors.append(f"FAIL: porter_forces.{k}='{pf.get(k)}' must be in {VALID_PORTER_LEVELS}.")

    # search_metadata
    meta = data.get("search_metadata")
    if not isinstance(meta, dict):
        errors.append("FAIL: search_metadata must be object.")
    else:
        if not meta.get("timestamp"):
            errors.append("FAIL: search_metadata.timestamp required.")
        if not isinstance(meta.get("source_urls", []), list):
            errors.append("FAIL: search_metadata.source_urls must be list.")

    # refresh_trigger
    rt = data.get("refresh_trigger")
    if not isinstance(rt, dict) or not rt.get("default_interval"):
        errors.append("FAIL: refresh_trigger.default_interval required.")

    # reasoning
    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str) or not reasoning.strip():
        errors.append("FAIL: reasoning must be non-empty string.")
    else:
        approx = len(re.findall(r"\b\w+\b", reasoning)) + len(re.findall(r"[一-鿿]", reasoning))
        if approx < 30 or approx > 250:
            errors.append(f"WARN: reasoning ~{approx} (target 50-200).")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate M-check JSON schema")
    parser.add_argument("--json-text")
    parser.add_argument("--json-file")
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
            sys.exit(f"FAIL: cannot read/parse: {e}")
    else:
        sys.exit("ERROR: provide --json-text or --json-file")

    errors = validate(data)
    if not errors:
        print("OK: M-check JSON schema valid")
        return 0

    fail = sum(1 for e in errors if e.startswith("FAIL"))
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
