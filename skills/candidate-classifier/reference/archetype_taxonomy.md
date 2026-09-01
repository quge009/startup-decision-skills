# Archetype Taxonomy (v1.5)

## Purpose

Reference doc for the `candidate-classifier` skill (Step 1b). Defines the
seven AI-infrastructure archetypes the framework recognizes, the LLM
classification guidance, four boundary-disambiguation primitives for
easy-to-confuse pairs, the `out-of-scope` outcome, and the `archetype.json`
output schema.

**v1.5 status**: archetype is an **auxiliary output**. The classifier
still emits one of seven archetypes (or `out-of-scope`), but archetype is
**not** used to gate the downstream pipeline, not used to look up weights,
and not used to condition the M-check or U-check prompts. M/U-check are
archetype-agnostic per [Decision A #3]. Archetype is preserved in
`archetype.json` for diagnostic / aux-truth comparison only.

## The 7 Archetypes

The seven archetypes are mutually-exclusive structural patterns for
AI-infrastructure business models. Each is defined by its **economic
primitive** — what the candidate sells, how it monetizes, how it sources
compute, what role it plays in the AI infra stack.

| Archetype | Defining primitive |
|---|---|
| **Hardware-vertical** | Candidate designs **its own chip / accelerator** as core IP. Revenue includes hardware sale (possibly bundled with software). Moat hypothesis rests on hardware-IP / vertical hardware integration. |
| **Hyperscaler-bundle** | Candidate **is itself a hyperscale-cloud platform layer**. Already operates hyperscale infra (public-cloud capacity, global data centers). AI capability is offered as a service of the hyperscaler platform; GPU CapEx at hyperscaler scale. |
| **Frontier-model-owner** | Candidate **pretrains its own foundation / large / domain-adapted language model** as core IP (not just fine-tuning OSS). CapEx-heavy on training; proprietary trained weights are primary IP. Includes both frontier-scale labs ($1B+ commitment, e.g. Anthropic / OpenAI) and smaller pretrainers — regional-language frontiers (Sarvam, Krutrim, Clibrain), domain-adapted SLM trainers (Arcee.ai), open-weight foundation labs (Stability AI). What matters is "pretrains own model as the business core" — not capital scale. Pure fine-tuning of someone else's open weights → NOT this archetype. |
| **Enterprise/Rental** | Candidate is a **GPU NeoCloud / capacity provider** (not a rental consumer). Customers are enterprises / sovereign / 政企. Revenue includes private cluster / dedicated capacity / committed-capacity contracts (not pure per-token). Cost structure includes **own GPU CapEx or long-term lease**. GPU compute is **primarily for AI / ML workloads** (training, inference, embeddings, model serving). Sovereign / anchor backlog drives main revenue. NOT this archetype even if "GPU" is in categories: (a) CAD / 3D / generic rendering, (b) financial vehicles ("GPU-backed alt investment"), (c) IT consulting firms mentioning "AI infrastructure" without a real compute product. |
| **OSS-on-rented-GPU** | Candidate **rents** GPU from hyperscalers (rental consumer, not owner). Model strategy = fine-tune OSS / use 3rd-party model API. Revenue = per-token API or thin-margin serving or per-tenant dedicated deployment. Typically thin-funded, can't do frontier training. |
| **Distribution** | Candidate is an **independent platform layer giving developers / businesses programmatic API access to AI capability** for integration into their own products. Revenue from model inference aggregation, marketplace, routing, agent runtime, per-call API metering. Either a real two-sided network (creator + consumer compounding) or marketplace fee or per-call API revenue is the primary moat. **Does NOT** train own frontier; **does NOT** sell hardware; **does NOT** hold own inference asset (0-asset architecture); is **independent** of any one hyperscaler. Single-modality developer APIs (speech-to-text, TTS, voice, vision, embeddings sold to developers — e.g. Deepgram, AssemblyAI, ElevenLabs) **count as Distribution**. NOT Distribution: consumer-facing chat wrappers / UI shells around LLM APIs sold to end-users (e.g. Poe-style consumer apps) — those are AI-using consumer apps, out-of-scope. |
| **CN-private** | A **CN-anchored frontier candidate with sovereign / state-backed anchor** (CN sovereign AI mission funding, 央 / 省级 gov anchor, SOE strategic partner). non-CN sovereign-anchored frontier (India / SE Asia / EU) → use Frontier-model-owner archetype with `cn_flag=false`, not CN-private. |

### `cn_flag` is orthogonal, not a separate archetype slot

`cn_flag` (boolean) is a cross-cutting attribute capturing
geographic / regulatory scope = CN (中国本土市场 + 备案合规驱动).
Output alongside the structural archetype:

- Distribution + `cn_flag=true` → Distribution candidate operating in CN
- Frontier-model-owner + `cn_flag=true` → CN-based frontier trainer (e.g.
  Zhipu) without sovereign anchor — still Frontier-model-owner, not
  CN-private
- CN-private → reserved for **CN-anchored frontier-train + sovereign /
  state-backed** combination specifically

## Classification Guidance for the LLM Classifier

The LLM reads `free_text` + candidate card 5-dim + the archetype
definitions + boundary disambiguations below, and outputs a holistic
judgment. **It is NOT a boolean rule check**. Same candidate may match
parts of multiple archetypes; the LLM picks the dominant primitive and
optionally a secondary, with reasoning.

Use `temp=0` + complete audit trail (`free_text`, 5-dim, archetype
definitions, this guidance, LLM output) to mitigate inherent
non-determinism.

## Boundary Disambiguations — 4 easy-to-confuse pairs

These pairs share surface vocabulary but differ by an underlying
structural primitive. Use the primitive to decide; do not rely on the
shared keyword.

### Enterprise/Rental vs OSS-on-rented-GPU

Distinguishing primitive: **own infrastructure provider vs rented
infrastructure consumer**.

- Enterprise/Rental = own GPU CapEx or long-term lease + sovereign /
  anchor backlog → provider side.
- OSS-on-rented-GPU = rents hyperscaler GPU to run OSS / fine-tune model
  serving + per-tenant dedicated deployment → consumer side.

Key: "dedicated deployment per tenant" alone does not distinguish — both
do it. The real distinction is cost structure. **own GPU CapEx →
Enterprise/Rental; all OpEx-rented → OSS-on-rented-GPU**.

### Hardware-vertical vs software-only architecture

Distinguishing primitive: **candidate designs its own chip / accelerator?**

- Hardware-vertical = own chip / accelerator design + vertical hardware
  integration.
- NOT Hardware-vertical: WebGPU runtimes, on-device ML SDKs, edge runtime,
  browser-native inference frameworks — even if "hardware" / "edge" /
  "device" appears in the description.

Key: target platform ≠ designs the chip. Hardware-vertical requires the
candidate **itself** to do chip / accelerator design.

### CN-private vs Frontier-model-owner + cn_flag

Distinguishing primitive: **specifically CN-anchored sovereign /
state-backed?**

- CN-private = CN-anchored frontier candidate with sovereign / state-backed
  anchor (CN sovereign AI mission funding, gov anchor, SOE strategic
  partner).
- Non-CN sovereign-anchored frontier (India / SE Asia / EU) →
  Frontier-model-owner archetype + sovereign-anchor reasoning, NOT
  CN-private.

Key: `cn_flag` is a cross-cutting boolean (Does it serve the CN market?),
not an archetype slot. CN-region marketplace is Distribution +
`cn_flag=true`, NOT CN-private.

### Distribution vs Hyperscaler-bundle

Distinguishing primitive: **independent platform layer vs bundled
inside hyperscaler platform layer**.

- Distribution = independent marketplace / aggregation / routing platform
  (candidate owns its own platform layer).
- Hyperscaler-bundle = bundled / 寄生 inside a hyperscaler's platform layer
  (the candidate is part of the hyperscaler platform).

Key: a marketplace that *calls* hyperscaler API for backend inference is
still independent Distribution — that's a vendor relationship.
Hyperscaler-bundle requires direct platform-layer binding (AI is offered
as a service of the hyperscaler's platform, not as an independent product
on top of it).

## Out-of-scope classification

When the candidate doesn't fit any of the 7 archetypes — consumer mobile
apps, content / media tools, traditional industry SaaS, domain-specific AI
applications where the AI is only a feature (legal contract review, AI
fitness coach, AI recipe recommender) — the classifier emits
`primary = "out-of-scope"`.

**v1.5 difference from v1.4**: in v1.4, `out-of-scope` short-circuited
the pipeline (Step 1b → skip M/U-check → emit 🚫 OUT_OF_SCOPE verdict).
In v1.5, the classifier still emits `out-of-scope`, but the **downstream
pipeline runs anyway** — M-check, U-check, aggregate, reasoning all
execute on out-of-scope candidates. Archetype is aux output only;
verdict is determined solely by M+U.

**Out-of-scope criteria**: the candidate's business model has no overlap
with the core primitive of any of the 7 archetypes. Examples:

- **Consumer end-user app** (productivity tool, consumer AI app, mobile
  app): not model-serving / inference / training infrastructure.
- **Domain-specific application without an AI-infra primitive**: AI-powered
  legal review, AI fitness coach, AI recipe — AI is a feature, candidate
  is not the infra layer.
- **Non-AI software or hardware**: traditional SaaS, hardware, content
  products with no AI relationship.
- **Pure media / content business**: content generation, media, creative
  tools, even when AI is used to accelerate — not at the AI-infra
  abstraction layer.

**Not out-of-scope**: candidates that are AI-infra businesses but
hybrid across multiple archetypes. These are `confidence=borderline` with
`primary + secondary`, NOT out-of-scope. Out-of-scope means "not in this
taxonomy at all", not "in but hard to classify".

## Output Schema (`archetype.json`)

```json
{
  "primary":    "<one of: Distribution | Frontier-model-owner | OSS-on-rented-GPU | Hyperscaler-bundle | Hardware-vertical | Enterprise/Rental | CN-private | out-of-scope>",
  "secondary":  "<archetype label, optional, for hybrid candidates; null when primary=out-of-scope>",
  "confidence": "robust | borderline",
  "cn_flag":    true | false,
  "reasoning":  "<text, 50-150 chars, LLM's rationale citing 5-dim and free-text evidence>"
}
```

### Confidence self-assessment heuristic (LLM-internal, only `robust` / `borderline` exposed)

- **robust**: candidate's 5-dim characteristics point clearly to a single
  archetype (or clearly outside all 7); free-text aligns with the 5-dim
  distillation; archetype definition match is strong.
- **borderline**: 2+ archetypes match partial characteristics / candidate
  spans multiple businesses / free-text and 5-dim distillation conflict /
  significant 5-dim gaps / sits between in-scope and out-of-scope.

## Edge Cases

- **Hybrid candidate (across declared archetypes)**: candidate exhibits
  multiple archetype characteristics simultaneously (e.g. public inference
  API + consumer-facing app) → output `primary` = dominant-characteristic
  archetype, `secondary` = next-strongest archetype, `confidence` =
  borderline, `reasoning` explains the weight balance. NOT out-of-scope.
- **Ambiguous proposal**: free-text too short / key info missing (business
  model unclear, customer undefined) → `confidence` = borderline,
  `reasoning` notes "needs additional candidate description: <specific
  gaps>"; still attempts a `primary` archetype rather than out-of-scope
  (ambiguous ≠ outside-taxonomy).
- **CN candidate**: structural archetype + `cn_flag` are **both** output,
  not collapsed into a single label. Example: Zhipu-like candidate →
  `primary = Frontier-model-owner`, `cn_flag = true`.
- **Out-of-scope (no fit with any of 7)**: candidate's business model is
  outside the AI-infrastructure taxonomy (consumer app / content / 传统
  SaaS / domain-specific AI app where the candidate is not the infra
  layer) → `primary = "out-of-scope"`, `secondary = null`, `confidence`
  may be `robust` or `borderline`, `reasoning` explains the specific
  ways the business model fails to overlap with any of the 7 core
  primitives. **In v1.5 the pipeline still runs M/U-check on these
  candidates and produces a verdict** — archetype is aux output only.

## Free-text is the primary input

`free_text` is the pipeline's primary input. Step 1a (candidate card
generation) is an LLM-based structured distill that outputs the 5-dim
candidate card; Step 1b (archetype classification) is an LLM-based
classification that outputs archetype metadata. Both steps are independent
LLM calls, both take `free_text` as the main input (5-dim distillation
serves only as auxiliary context to Step 1b).

Candidate card 5-dim + `free_text` both flow into downstream M/U-check,
preserving `free_text` against distillation loss. Candidate card does
**not** contain an archetype field; archetype metadata lives in
`archetype.json` as a sibling to verdicts / reasoning.
