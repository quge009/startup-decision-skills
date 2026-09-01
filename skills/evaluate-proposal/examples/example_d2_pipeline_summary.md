# Example: D2 — AI Agent Orchestration Marketplace — End-to-End Pipeline Run

## Step 0 — Free-text input

```
A multi-vendor AI agent orchestration marketplace where developers create agent workflows
(combining tools, prompts, models) and publish them to a central hub. End users invoke
agents via API or UI, paying per-call to the platform; creators earn revenue share. The
platform takes 20% of transaction value as marketplace fee. Bidirectional network effects.
[... see candidate-classifier/examples/example_d2_proposal.txt for full text ...]
```

Working dir: `./working/d2-agent-orchestration-marketplace/`

## Step 1a + 1b — candidate-classifier

```
## Candidate classification result

**Slug**: d2-agent-orchestration-marketplace
**Primary archetype**: Distribution
**Secondary archetype**: —
**Confidence**: robust
**CN flag**: false
**Reasoning** (50–150 字): Multi-vendor agent orchestration marketplace fits Distribution
archetype core features: revenue from marketplace fee, bidirectional network economies as
primary moat, no proprietary GPU or frontier model. All 5-dim features point to Distribution
without significant cross-archetype features.

**Outputs written**:
- working/d2-agent-orchestration-marketplace/candidate_card.md
- working/d2-agent-orchestration-marketplace/archetype.json
```

## Step 2 — lookup_weights.py

```json
{
  "archetype": "Distribution",
  "h": 0.30,
  "m": 0.50,
  "u": 0.20
}
```

## Step 3-5 prep — tavily-query-builder

```
## Tavily query build result

**Candidate**: d2-agent-orchestration-marketplace
**Archetype**: Distribution
**M-check queries**: 5 (extract targets: Gartner Magic Quadrant, IDC MarketScape, a16z LLMOps stack report, OpenAI GPT Store, Anthropic Claude Skills, HuggingFace, OpenRouter IR pages)
**U-check queries**: 4

**Output written**: working/d2-agent-orchestration-marketplace/queries.json
```

## Step 3 — H-check

```
## H-check result

**Candidate**: d2-agent-orchestration-marketplace
**Archetype**: Distribution
**Verdict**: ✅ PASS (variant H-check-Mid, branch H1a)
**Reasoning** (50-150 字): D2 通过 H1a 真双边 marketplace pattern 满足 H-check-Mid 必要条件...

**Output**: working/d2-agent-orchestration-marketplace/h_check.json
```

## Step 4 — M-check (with tavily)

```
## M-check result

**Candidate**: d2-agent-orchestration-marketplace
**Archetype**: Distribution
**Verdict**: ⚠ WARN
**SOM headroom**: ~$0.51 B (TAM $3 B agent runtime sub-segment, pct_of_tam 0.7 — D2 是 multi-vendor 全球平台 single-vendor 部分不可服务, locked_pct 0.65 to OpenAI/Anthropic stores, porter_discount 0.7; SOM = 3 × 0.7 × 0.35 × 0.7 ≈ 0.51)
**Reasoning** (50-200 字): 多 vendor agent runtime sub-segment TAM ~$2-5 B; 主要 incumbent
是 frontier-locked stores (OpenAI GPT Store, Claude Skills) 跟 multi-vendor router (OpenRouter).
Locked_pct ~0.65 反映 frontier stores 的 default-distribution 优势 + Anthropic Skills 多 vendor
扩展;...

**Output**: working/d2-agent-orchestration-marketplace/m_check.json
```

## Step 5 — U-check (with tavily)

```
## U-check result

**Candidate**: d2-agent-orchestration-marketplace
**Archetype**: Distribution
**Verdict**: ⚠ WARN
**VRIO score**: 3.0 / 4.0 (V=1.0 R=0.5 I=0.5 O=1.0)
**Dominant moat**: Network Economies — required by Distribution archetype (per [P1W5 §4]),
candidate status = marginal
**Reasoning** (50-200 字): bidirectional network economies 在 V/O 维强 (双边 flywheel valuable + 团队 GTM organized), R/I 中等 (R: frontier-locked GPT Store + Claude Skills 已存在 — partial 不是独家; I: Skills 协议 OSS 协议层易复制, 但网络效应 inertia 让 imitator 重建 creator/user 池子 — partial barrier). VRIO score 3.0 / 4.0 → marginal WARN...

**Output**: working/d2-agent-orchestration-marketplace/u_check.json
```

## Step 6 — aggregate.py

```
OK: wrote working/d2-agent-orchestration-marketplace/aggregate.json

Final verdict: ⚠ WARN (score 0.650, path: two_or_more_warn)
Conjunction: 2 dims ⚠ + 0 dims 🔴 → ⚠
Dominant driver: M-check WARN — M-check WARN with weight 50% — heaviest cautionary dim.
```

`aggregate.json` content:
```json
{
  "archetype": {"primary": "Distribution", "secondary": null, "cn_flag": false},
  "weights": {"h": 0.30, "m": 0.50, "u": 0.20},
  "verdicts_per_dim": {"h": "PASS", "m": "WARN", "u": "WARN"},
  "weighted_score": 0.65,
  "verdict_path": "two_or_more_warn",
  "conjunction_trigger": "2 dims ⚠ + 0 dims 🔴 → ⚠",
  "final_verdict": "WARN",
  "final_verdict_emoji": "⚠",
  "dominant_driver": {
    "dim": "M",
    "verdict": "WARN",
    "reason": "M-check WARN with weight 50% — heaviest cautionary dim."
  }
}
```

## Step 7 — Generate reasoning JSON (LLM)

`reasoning.json`:
```json
{
  "verdict": "WARN",
  "verdict_emoji": "⚠",
  "verdict_text": "conditional — viable if creator flywheel achieves liftoff before frontier stores extend multi-vendor",
  "archetype": {"primary": "Distribution", "secondary": null, "cn_flag": false},
  "hmu_breakdown": {
    "h_check": {
      "verdict": "PASS",
      "variant": "H-check-Mid",
      "satisfied_branch": "H1a",
      "key_evidence": "Bidirectional creator + user marketplace, 0-asset architecture (uses 3rd-party APIs as backend), avoids OSS-on-rented-GPU anti-pattern."
    },
    "m_check": {
      "verdict": "WARN",
      "som_headroom_b": 0.51,
      "key_evidence": "TAM $3 B agent runtime sub-segment; SAM = 0.7 × TAM = $2.1 B (multi-vendor scope, 排除 single-vendor agents); locked_pct ~65% to OpenAI GPT Store + Anthropic Claude Skills; porter_discount 0.7 for moderate rivalry; SOM headroom ≈ $0.51 B borderline ($100M-$1B WARN range)."
    },
    "u_check": {
      "verdict": "WARN",
      "vrio_score": 3.0,
      "key_evidence": "Network Economies V[✓] R[partial] I[partial] O[✓] — VRIO 3.0/4.0; protocol-level imitation easy but network economies inertia provides partial barrier; frontier stores extending multi-vendor (Anthropic Skills) is real risk; creator IP + user workflow lock-in still building."
    }
  },
  "aggregate": {
    "weighted_score": 0.65,
    "weights_used": {"h": 0.30, "m": 0.50, "u": 0.20},
    "verdict_path": "two_or_more_warn",
    "conjunction_trigger": "2 dims ⚠ + 0 dims 🔴 → ⚠"
  },
  "dominant_driver": {
    "dim": "M",
    "verdict": "WARN",
    "reason": "M-check is heaviest weight (50%) and WARN — SOM headroom $0.51B borderline (post pct_of_tam 0.7 + locked_pct 0.65 + porter_discount 0.7) drives final verdict."
  },
  "decision_rationale": "Conditional viable. H-check pass via 双边 marketplace pattern (textbook H1a). 关键风险是 channel disintermediation — frontier vendors 扩展 multi-vendor stores (Anthropic Skills 已 move 该方向) 会侵蚀 SOM headroom 跟 moat 双重 cumulative. 推荐 path: 先 specialty agent niches (vertical / regional) 不直接竞争 frontier stores; sovereign / enterprise anchor 锁 IP creator 防 imitate. 不推荐 generic agent marketplace 路径直接走通用 + 全球 multi-vendor (会被 frontier 反噬).",
  "metadata": {"timestamp": "2026-06-04", "framework_version": "v1.0"}
}
```

## Step 8 — write_storage.py

```
OK: appended d2-agent-orchestration-marketplace entry to ltp_agents/agents/business-predictive-model/docs/candidate_evaluation_results.html
```

The entry inserted in `candidate_evaluation_results.html` is a structured `<details>` block containing free-text + archetype + H/M/U breakdown + aggregate + dominant driver + decision rationale + metadata, with verdict-color summary.

## Step 9 — Final summary printed to user

```
## Pipeline run complete

**Candidate**: d2-agent-orchestration-marketplace
**Archetype**: Distribution · confidence robust
**Final verdict**: ⚠ WARN
**Why**: 多维 borderline — 2 维同时风险, 单点改善不够

**H-check**: ✅ PASS (variant H-check-Mid, branch H1a)
**M-check**: ⚠ WARN (TAM $3B · SAM $2.1B · SOM headroom $0.51 B)
**U-check**: ⚠ WARN (VRIO 3.0/4.0 · required moat Network Economies · candidate status partial)

**Decision rationale**: Conditional viable. H-check pass via 双边 marketplace pattern. 关键风险是
channel disintermediation — frontier vendors 扩展 multi-vendor stores 会侵蚀 SOM headroom 跟 moat
双重. 推荐 path: 先 specialty agent niches (vertical / regional) 不直接竞争 frontier stores;
sovereign / enterprise anchor 锁 IP creator 防 imitate.

**Outputs in**: working/d2-agent-orchestration-marketplace/
**STORAGE entry appended**: ltp_agents/agents/business-predictive-model/docs/candidate_evaluation_results.html
```

Note: terminal summary v1.1 format intentionally drops `weighted_score` / `verdict_path` / `dominant_driver` from operator output to mirror v1.1 storage HTML rendering policy. Those fields remain in `aggregate.json` + `reasoning.json` for audit + `score_threshold` path tie-breaking.
