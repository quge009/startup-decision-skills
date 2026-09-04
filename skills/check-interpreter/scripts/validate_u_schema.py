"""Validate U-check JSON against [P1W5 §7.1] schema.

Usage: python3 validate_u_schema.py --json-file <path>
       python3 validate_u_schema.py --json-text "<json>"
"""

import argparse
import json
import re
import sys
from pathlib import Path

VALID_VERDICTS = {"PASS", "WARN", "FAIL"}
VALID_EMOJIS = {"✅", "⚠", "🔴"}
VERDICT_TO_EMOJI = {"PASS": "✅", "WARN": "⚠", "FAIL": "🔴"}
VALID_MOAT_TYPES = {
    "Network Economies",
    "Scale",
    "Cornered Resource",
    "Process Power",
    "Brand",
    "Switching Costs",
    "Counter-positioning",
}
VALID_VRIO_VERDICTS = {"✓", "✗", "partial"}
VRIO_VALUES = {"✓": 1.0, "partial": 0.5, "✗": 0.0}


def validate(data) -> list:
    errors = []
    if not isinstance(data, dict):
        return ["FAIL: U-check output must be a JSON object."]

    verdict = data.get("verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"FAIL: verdict='{verdict}' not in {VALID_VERDICTS}")
    emoji = data.get("verdict_emoji")
    if emoji not in VALID_EMOJIS:
        errors.append(f"FAIL: verdict_emoji='{emoji}' not in {VALID_EMOJIS}")
    elif verdict in VERDICT_TO_EMOJI and emoji != VERDICT_TO_EMOJI[verdict]:
        errors.append("FAIL: verdict_emoji mismatch verdict.")

    if not data.get("verdict_text"):
        errors.append("FAIL: missing verdict_text.")

    # claimed_moats
    claimed = data.get("claimed_moats")
    if not isinstance(claimed, list) or not claimed:
        errors.append("FAIL: claimed_moats must be non-empty list.")
    else:
        for i, m in enumerate(claimed):
            if not isinstance(m, dict):
                errors.append(f"FAIL: claimed_moats[{i}] must be object.")
                continue
            mt = m.get("moat_type")
            if mt not in VALID_MOAT_TYPES:
                errors.append(f"FAIL: claimed_moats[{i}].moat_type='{mt}' not in {VALID_MOAT_TYPES}.")
            if not m.get("evidence"):
                errors.append(f"FAIL: claimed_moats[{i}].evidence required.")

    # vrio_breakdown
    vrio = data.get("vrio_breakdown")
    if not isinstance(vrio, dict):
        errors.append("FAIL: vrio_breakdown must be object.")
    else:
        if vrio.get("dominant_moat") not in VALID_MOAT_TYPES:
            errors.append("FAIL: vrio_breakdown.dominant_moat must be a valid moat_type.")
        for dim_key in ("valuable", "rare", "inimitable", "organized"):
            d = vrio.get(dim_key)
            if not isinstance(d, dict):
                errors.append(f"FAIL: vrio_breakdown.{dim_key} must be object.")
                continue
            if d.get("verdict") not in VALID_VRIO_VERDICTS:
                errors.append(
                    f"FAIL: vrio_breakdown.{dim_key}.verdict='{d.get('verdict')}' "
                    f"must be in {VALID_VRIO_VERDICTS}."
                )
            if not d.get("evidence"):
                errors.append(f"FAIL: vrio_breakdown.{dim_key}.evidence required.")
        score = vrio.get("score")
        if not isinstance(score, (int, float)) or not 0.0 <= score <= 4.0:
            errors.append(f"FAIL: vrio_breakdown.score={score} must be number in [0.0, 4.0].")
        # Sanity: score should equal sum of 4 dim values per [P1W5 §5.1]
        try:
            expected = sum(VRIO_VALUES[vrio[d]["verdict"]] for d in ("valuable", "rare", "inimitable", "organized"))
            if abs(score - expected) > 0.05:
                errors.append(
                    f"WARN: vrio_breakdown.score={score} ≠ sum of 4 dim values ({expected}). "
                    "Per [P1W5 §5.1] score = V+R+I+O (Yes=1.0 / Partial=0.5 / No=0.0)."
                )
        except (KeyError, TypeError):
            pass

    # v1.5 deliberately removed the archetype-specific required-moat gate.
    if "dominant_moat_required" in data:
        errors.append(
            "FAIL: dominant_moat_required is a retired v1.4 field; "
            "v1.5 applies one uniform VRIO assessment."
        )

    # erosion_risks
    er = data.get("erosion_risks")
    if not isinstance(er, list):
        errors.append("FAIL: erosion_risks must be list.")

    # search_metadata
    meta = data.get("search_metadata")
    if not isinstance(meta, dict):
        errors.append("FAIL: search_metadata must be object.")
    else:
        if not meta.get("timestamp"):
            errors.append("FAIL: search_metadata.timestamp required.")

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
    parser = argparse.ArgumentParser(description="Validate U-check JSON schema")
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
        print("OK: U-check JSON schema valid")
        return 0

    fail = sum(1 for e in errors if e.startswith("FAIL"))
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
