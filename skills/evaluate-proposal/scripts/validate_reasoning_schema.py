"""Validate final reasoning JSON schema (v1.5 / v1.5a).

v1.5 reasoning.json shape (per evaluate-proposal Step 7):
  {
    "verdict":        "PASS" | "WARN" | "FAIL",
    "verdict_emoji":  "✅" | "⚠" | "🔴",
    "verdict_text":   <string>,
    "mu_breakdown":   {
      "m_check":      {"verdict": ..., "som_headroom_b": <num>, "key_evidence": <string>},
      "u_check":      {"verdict": ..., "vrio_score": <0..4>, "key_evidence": <string>}
    },
    "aggregate":      {
      "weighted_score":   <0..1>,
      "weights_used":     {"m": 0.5, "u": 0.5},
      "verdict_path":     "score_threshold",
      "score_explanation": <string>
    },
    "dominant_driver": {"dim": "M" | "U", "verdict": ..., "reason": <string>},
    "decision_rationale": <string, 50-200 chars>,
    "metadata": {
      "timestamp":         <YYYY-MM-DD>,
      "framework_version": "v1.5" | "v1.5a",
      "founding_year":     <int>,
      "retrieval_window":  [<YYYY-MM-DD>, <YYYY-MM-DD>]
    }
  }

v1.5a is v1.5 with PASS_THRESHOLD 0.75 → 0.50 (aggregate.py only; schema
unchanged). v1.4 schema (still on disk for legacy outputs) had h_check in
breakdown and H/M/U dominant dim — those are explicitly rejected here.

Usage: python3 validate_reasoning_schema.py --json-file <path>
"""

import argparse
import json
import re
import sys
from pathlib import Path

VALID_VERDICTS = {"PASS", "WARN", "FAIL"}
VALID_EMOJIS = {"✅", "⚠", "🔴"}
VERDICT_TO_EMOJI = {"PASS": "✅", "WARN": "⚠", "FAIL": "🔴"}
VALID_VERDICT_PATHS = {"score_threshold"}  # v1.5 has one path only
VALID_DOMINANT_DIMS = {"M", "U"}
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(data) -> list:
    errors = []
    if not isinstance(data, dict):
        return ["FAIL: reasoning JSON must be object."]

    verdict = data.get("verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"FAIL: verdict must be in {VALID_VERDICTS}")
    emoji = data.get("verdict_emoji")
    if emoji not in VALID_EMOJIS:
        errors.append(f"FAIL: verdict_emoji must be in {VALID_EMOJIS}")
    elif verdict in VERDICT_TO_EMOJI and emoji != VERDICT_TO_EMOJI[verdict]:
        errors.append("FAIL: verdict_emoji mismatch verdict.")
    if not data.get("verdict_text"):
        errors.append("FAIL: verdict_text required.")
    if "archetype" in data:
        errors.append("FAIL: archetype is not part of the proposal-evaluation schema.")

    # v1.5: mu_breakdown (NOT hmu_breakdown — H dropped)
    if "hmu_breakdown" in data:
        errors.append(
            "FAIL: 'hmu_breakdown' is v1.4. v1.5 uses 'mu_breakdown' (H-check dropped)."
        )
    mu = data.get("mu_breakdown")
    if not isinstance(mu, dict):
        errors.append("FAIL: mu_breakdown must be object.")
    else:
        if "h_check" in mu:
            errors.append("FAIL: mu_breakdown.h_check is v1.4. v1.5 has only m_check + u_check.")
        m = mu.get("m_check", {})
        if m.get("verdict") not in VALID_VERDICTS:
            errors.append("FAIL: mu_breakdown.m_check.verdict required.")
        if not isinstance(m.get("som_headroom_b"), (int, float)):
            errors.append("FAIL: mu_breakdown.m_check.som_headroom_b must be number.")
        u = mu.get("u_check", {})
        if u.get("verdict") not in VALID_VERDICTS:
            errors.append("FAIL: mu_breakdown.u_check.verdict required.")
        v_score = u.get("vrio_score")
        if not isinstance(v_score, (int, float)) or not 0 <= v_score <= 4.0:
            errors.append("FAIL: mu_breakdown.u_check.vrio_score must be number in [0.0, 4.0].")

    agg = data.get("aggregate")
    if not isinstance(agg, dict):
        errors.append("FAIL: aggregate must be object.")
    else:
        ws = agg.get("weighted_score")
        if not isinstance(ws, (int, float)) or not 0 <= ws <= 1.0:
            errors.append("FAIL: aggregate.weighted_score must be number in [0.0, 1.0].")
        wu = agg.get("weights_used", {})
        if not isinstance(wu, dict) or not all(k in wu for k in ("m", "u")):
            errors.append("FAIL: aggregate.weights_used must have m, u fields.")
        elif "h" in wu:
            errors.append("FAIL: aggregate.weights_used.h is v1.4. v1.5 has only m + u.")
        elif abs(sum(wu.get(k, 0) for k in ("m", "u")) - 1.0) > 0.01:
            errors.append("FAIL: aggregate.weights_used m+u must sum to 1.0 (±0.01).")
        if agg.get("verdict_path") not in VALID_VERDICT_PATHS:
            errors.append(
                f"FAIL: aggregate.verdict_path must be in {VALID_VERDICT_PATHS} "
                "(v1.5 uses score-threshold only; v1.4 conjunction-rule paths dropped)."
            )

    dd = data.get("dominant_driver")
    if not isinstance(dd, dict):
        errors.append("FAIL: dominant_driver must be object.")
    else:
        if dd.get("dim") not in VALID_DOMINANT_DIMS:
            errors.append(
                f"FAIL: dominant_driver.dim must be in {VALID_DOMINANT_DIMS} "
                "(v1.5; H dropped)."
            )
        if dd.get("verdict") not in VALID_VERDICTS:
            errors.append("FAIL: dominant_driver.verdict required.")
        if not dd.get("reason"):
            errors.append("FAIL: dominant_driver.reason required.")

    rationale = data.get("decision_rationale", "")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append("FAIL: decision_rationale must be non-empty.")
    else:
        approx = len(re.findall(r"\b\w+\b", rationale)) + len(re.findall(r"[一-鿿]", rationale))
        if approx < 30 or approx > 300:
            errors.append(f"WARN: decision_rationale ~{approx} chars (target 50-200).")

    meta = data.get("metadata")
    if not isinstance(meta, dict):
        errors.append("FAIL: metadata must be object.")
    else:
        if not meta.get("timestamp"):
            errors.append("FAIL: metadata.timestamp required.")
        fv = meta.get("framework_version")
        if not fv:
            errors.append("FAIL: metadata.framework_version required.")
        elif not fv.startswith("v1.5"):
            errors.append(
                f"WARN: metadata.framework_version={fv!r}; expected 'v1.5' or 'v1.5.x'."
            )
        founding_year = meta.get("founding_year")
        if not isinstance(founding_year, int) or not 1900 <= founding_year <= 2099:
            errors.append(
                "FAIL: metadata.founding_year required (int in [1900, 2099]). "
                "v1.5 records the founding year used for time-bounded retrieval."
            )
        window = meta.get("retrieval_window")
        if not isinstance(window, list) or len(window) != 2 or not all(
            isinstance(d, str) and ISO_DATE_RE.match(d) for d in window
        ):
            errors.append(
                "FAIL: metadata.retrieval_window required: [<YYYY-MM-DD>, <YYYY-MM-DD>]. "
                "v1.5 records the Tavily search window used."
            )

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate reasoning JSON schema (v1.5)")
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
        print("OK: reasoning JSON schema valid (v1.5)")
        return 0

    fail = sum(1 for e in errors if e.startswith("FAIL"))
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
