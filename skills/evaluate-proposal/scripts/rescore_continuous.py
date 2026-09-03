"""Continuous-score rescoring helper for market and uniqueness checks.

Pure function library. It does not change the discrete aggregation path
(PASS/WARN/FAIL → {1.0,
0.5, 0.0} → weighted 0.5·M + 0.5·U → threshold 0.50).

This module maps the raw
quantitative fields already emitted by m_check.json / u_check.json into
continuous [0, 1] scores without discretizing through the verdict layer.
Discrete verdicts quantize weighted_score into {0.0, 0.25, 0.5, 0.75,
    1.0}; PASS threshold 0.50 sits atop the largest cluster, so precision
    is capped structurally.

Continuous scoring goals:
  - Preserve v1.5-M-check's FAIL ($100M) / PASS ($1B) SOM-headroom anchors.
  - Preserve v1.5-U-check's VRIO physical semantics (4-dim additive).
  - Expose fine-grained score distribution so threshold sweep on ROC can
    find data-driven optimum.

Mappings:

  M_score = clip((log10(som_headroom_$) - 7) / 3, 0, 1)
    som_headroom_$ = m_check_json["som"]["headroom_b"] * 1e9
    Anchors:  $10M → 0.0    $100M → 0.333    $1B → 0.667    $10B → 1.0

  U_score = clip(vrio_score / 4.0, 0, 1)
    vrio_score = u_check_json["vrio_breakdown"]["score"]  (0..4, 0.5 granularity)
    Anchors:  VRIO 0 → 0.0    1 → 0.25    2 → 0.5    3 → 0.75    4 → 1.0

  weighted_score = 0.5 * M_score + 0.5 * U_score  ∈ [0, 1]

Threshold sweep for verdict is caller's responsibility (research script
scans PASS threshold ∈ [0, 1]; production landing would set a single
constant). No production landing yet — this module is helpers only.
"""

import math

M_HEADROOM_LOG10_ZERO = 7.0   # log10($10M) → M_score 0.0
M_HEADROOM_LOG10_ONE = 10.0   # log10($10B) → M_score 1.0

U_VRIO_MAX = 4.0

WEIGHT_M = 0.5
WEIGHT_U = 0.5


def m_continuous_score(m_check_json: dict) -> float:
    """Log-scale mapping of SOM headroom to [0, 1].

    Anchored to M-check discrete verdict boundaries:
      $10M → 0.0 (deep FAIL), $100M → 0.333 (FAIL→WARN),
      $1B  → 0.667 (WARN→PASS), $10B → 1.0 (deep PASS).

    Missing / zero / negative headroom → 0.0.
    """
    try:
        headroom_b = float(m_check_json["som"]["headroom_b"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if headroom_b <= 0:
        return 0.0
    log10_headroom_dollars = math.log10(headroom_b * 1e9)
    raw = (log10_headroom_dollars - M_HEADROOM_LOG10_ZERO) / (
        M_HEADROOM_LOG10_ONE - M_HEADROOM_LOG10_ZERO
    )
    return max(0.0, min(1.0, raw))


def u_continuous_score(u_check_json: dict) -> float:
    """Linear mapping of VRIO score (0..4) to [0, 1].

    VRIO 4-dim additive semantics already linear — direct division.
    Missing / malformed → 0.0.
    """
    try:
        vrio_score = float(u_check_json["vrio_breakdown"]["score"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    raw = vrio_score / U_VRIO_MAX
    return max(0.0, min(1.0, raw))


def aggregate_continuous(m_score: float, u_score: float) -> float:
    """v1.5b weighted aggregate — matches v1.5 M=0.5, U=0.5 weighting."""
    return WEIGHT_M * m_score + WEIGHT_U * u_score
