"""Aggregate M/U verdicts into final verdict (v1.5a).

v1.5a changes from v1.5 (per pipeline_benchmark iter 3 §5.8 + iter 4 finding):
  - PASS_THRESHOLD lowered 0.75 → 0.50. Reason: iter 3 + iter 5 (two
    independent 198-samples) showed v1.5's M/U-check never produces
    weighted_score ≥ 0.75 in practice; the score distribution lives in
    {0.0, 0.25, 0.5} buckets. Threshold 0.50 calibrates to empirical sweet
    spot; iter 6 measured F0.5=0.616 on independent sample.
  - Everything else from v1.5 unchanged (no H-check; archetype is metadata;
    M/U archetype-agnostic; time-bounded retrieval; score-threshold path).

v1.5 changes from v1.4 (per pipeline_benchmark iter 1 §6.2 Decision A):
  - Drop H-check entirely (was archetype-conditional; 2/7 archetypes had method).
  - Drop archetype-keyed weight lookup; weights are uniform M=0.5, U=0.5.
    Archetype remains in output as METADATA only — no compute uses it.
  - Drop OUT_OF_SCOPE short-circuit. Every candidate produces a verdict.
  - Drop the 5-path conjunction rule (all_pass / weight_aware_fail /
    two_or_more_warn / one_warn / score_threshold) calibrated for 3 dims.
    v1.5 uses score-threshold only (Q1 option A from iter 1 §6.2 review).

Score formula (v1.5+):
  score = 0.5 · M + 0.5 · U,   where verdict→num: PASS=1.0, WARN=0.5, FAIL=0.0

Verdict thresholds (v1.5a):
  score ≥ 0.50  → PASS
  0.25 ≤ score < 0.50  → WARN
  score < 0.25  → FAIL

Usage:
  python3 aggregate.py --working-dir <path>
"""

import argparse
import json
import sys
from pathlib import Path

VERDICT_TO_NUM = {"PASS": 1.0, "WARN": 0.5, "FAIL": 0.0}
VERDICT_TO_EMOJI = {
    "PASS": "✅",
    "WARN": "⚠",
    "FAIL": "🔴",
}

# Uniform weights — archetype-agnostic per v1.5 Decision A #3.
WEIGHT_M = 0.5
WEIGHT_U = 0.5

# v1.5a: lowered from 0.75 (v1.5) per iter 4 finding.
PASS_THRESHOLD = 0.50
WARN_THRESHOLD = 0.25  # below this → FAIL; at-or-above → WARN (up to PASS_THRESHOLD)


def aggregate(m_verdict: str, u_verdict: str) -> dict:
    """Compute weighted score + verdict for v1.5a (M+U only, score-based, PASS≥0.50)."""
    m_num = VERDICT_TO_NUM[m_verdict]
    u_num = VERDICT_TO_NUM[u_verdict]
    score = WEIGHT_M * m_num + WEIGHT_U * u_num

    if score >= PASS_THRESHOLD:
        verdict = "PASS"
    elif score >= WARN_THRESHOLD:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    score_explanation = (
        f"{WEIGHT_M}·M({m_verdict}={m_num}) + {WEIGHT_U}·U({u_verdict}={u_num}) "
        f"= {score:.2f} → {verdict} "
        f"(thresholds: ≥{PASS_THRESHOLD} PASS / ≥{WARN_THRESHOLD} WARN / <{WARN_THRESHOLD} FAIL)"
    )

    return {
        "verdict": verdict,
        "verdict_path": "score_threshold",
        "weighted_score": round(score, 3),
        "score_explanation": score_explanation,
    }


def determine_dominant_driver(m_verdict: str, u_verdict: str) -> dict:
    """Pick the worse-performing dim as dominant driver. Ties broken by M-first convention."""
    verdicts = {"M": m_verdict, "U": u_verdict}
    # Worst dim = lowest numeric verdict
    sorted_dims = sorted(verdicts.items(), key=lambda kv: VERDICT_TO_NUM[kv[1]])
    worst_dim, worst_verdict = sorted_dims[0]

    if m_verdict == u_verdict:
        # Tie — M-first convention
        return {
            "dim": "M",
            "verdict": m_verdict,
            "reason": f"Both M and U returned {m_verdict}; M reported first by convention.",
        }

    return {
        "dim": worst_dim,
        "verdict": worst_verdict,
        "reason": f"{worst_dim}-check {worst_verdict} is the weaker of the two dims (M={m_verdict}, U={u_verdict}).",
    }


def main():
    parser = argparse.ArgumentParser(description="Aggregate M/U → final verdict (v1.5a)")
    parser.add_argument("--working-dir", required=True)
    parser.add_argument("--archetype-file", help="Default <working_dir>/archetype.json (metadata only)")
    parser.add_argument("--m-file", help="Default <working_dir>/m_check.json")
    parser.add_argument("--u-file", help="Default <working_dir>/u_check.json")
    args = parser.parse_args()

    wd = Path(args.working_dir).resolve()
    archetype_path = Path(args.archetype_file) if args.archetype_file else wd / "archetype.json"
    m_path = Path(args.m_file) if args.m_file else wd / "m_check.json"
    u_path = Path(args.u_file) if args.u_file else wd / "u_check.json"

    for name, p in [("archetype", archetype_path), ("m", m_path), ("u", u_path)]:
        if not p.exists():
            sys.exit(f"ERROR: {name} file not found: {p}")

    archetype_data = json.loads(archetype_path.read_text(encoding="utf-8"))
    m_data = json.loads(m_path.read_text(encoding="utf-8"))
    u_data = json.loads(u_path.read_text(encoding="utf-8"))

    m_verdict = m_data.get("verdict")
    u_verdict = u_data.get("verdict")
    for name, v in [("m", m_verdict), ("u", u_verdict)]:
        if v not in VERDICT_TO_NUM:
            sys.exit(f"ERROR: {name}_check.json verdict='{v}' must be PASS/WARN/FAIL")

    agg = aggregate(m_verdict, u_verdict)
    dominant = determine_dominant_driver(m_verdict, u_verdict)

    output = {
        "archetype": {
            "primary": archetype_data.get("primary"),
            "secondary": archetype_data.get("secondary"),
            "cn_flag": archetype_data.get("cn_flag", False),
        },
        "weights": {"m": WEIGHT_M, "u": WEIGHT_U},
        "verdicts_per_dim": {"m": m_verdict, "u": u_verdict},
        "weighted_score": agg["weighted_score"],
        "verdict_path": agg["verdict_path"],
        "score_explanation": agg["score_explanation"],
        "final_verdict": agg["verdict"],
        "final_verdict_emoji": VERDICT_TO_EMOJI[agg["verdict"]],
        "dominant_driver": dominant,
        "framework_version": "v1.5a",
    }

    out_path = wd / "aggregate.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"OK: wrote {out_path}")
    print(
        f"\nFinal verdict: {output['final_verdict_emoji']} {output['final_verdict']} "
        f"(score {output['weighted_score']:.3f}, path: {output['verdict_path']})"
    )
    print(f"Score: {agg['score_explanation']}")
    print(f"Dominant driver: {dominant['dim']}-check {dominant['verdict']} — {dominant['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
