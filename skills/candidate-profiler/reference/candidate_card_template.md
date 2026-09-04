# Candidate card — TEMPLATE (v1.5)

> Used by the `candidate-profiler` skill to structure the
> 5-dim distillation of a free-text business proposal description.
>
> The card mirrors the same 5-dim shape as a vendor card, but with
> candidate-specific metadata (Original proposal description, Closest
> existing vendor analogues, Founding year).
>
> Aim for **400–750 words** total across the 5 dimensions;
> ~80–150 words per dimension.

---

# Candidate card — `<Candidate name>`

**Slug**: `<candidate-slug>` (kebab-case, max 40 chars; derived from candidate name)
**Public/private**: `<public | private>` (proposal-stage candidates are usually private)
**Founding year**: `<YYYY>` (REQUIRED — used downstream for M-check / U-check time-bounded Tavily retrieval window `[founding_year - 3y, founding_year]`)
**Card last updated**: `<YYYY-MM-DD>`
**Card author**: `auto-generated (via candidate-profiler skill)`

**Original proposal description**: `<verbatim original free-text proposal description, preserved per evaluate-proposal SKILL.md §"Free-text is primary input">`

**Closest existing vendor analogues**: `<list of 1-3 existing vendors closest to this candidate's business model, citing relevant comparables; or "novel candidate, no direct analogue" if proposal describes a truly novel pattern>`

---

## 1. Revenue model

**Primary revenue stream**: `<per-token | committed-capacity | dedicated-infra | hybrid | subscription | marketplace fee | per-call API | hardware sale | other>`

**Secondary revenue streams**: `<list, or "not disclosed">`

**Pricing structure**:
- `<tier-name-1>`: `<$/M token>` or `<$/PTU·hr>` or `<$/MU·month>` — `<conditions>`
- `<tier-name-2>`: …

**Proposal claim**: `<verbatim from free-text about how monetization works>`

**Plausibility flag**: `<low | medium | high>` — `<one-line reasoning>`

**Notes**:

`<80–150 words: how the candidate plans to monetize. Per-call / subscription /
contract? What's the commitment shape? Volume discounts? Free tier funnel?
Any unusual tactics (auction, marketplace, BYOK)? Where is this monetization
pattern in the AI-infra spectrum vs adjacent business categories?>`

---

## 2. Customer segmentation

**Identifiable segments**: `<list relevant ones from {individual developer, SMB, enterprise, cloud reseller, government, academic, internal, consumer end-user, institutional fintech, ...}>`

**Estimated mix** (% ARR or % logo count, with confidence flag):
- `<segment 1>`: ~`<XX>%` — confidence `<low / medium / high>` — `<basis>`
- `<segment 2>`: ~`<YY>%` — confidence `<…>` — `<basis>`

**Notable disclosed customer logos**: `<list, or "none publicly disclosed">`

**Proposal claim**: `<target customer description from free-text>`

**Notes**:

`<80–150 words: who is the customer? Where does most revenue come from? Is
there a long-tail or a heavy concentration? Any geographic skew? Channel
dependency?>`

---

## 3. Cost structure (best-effort estimate)

**GPU spend as % of revenue** (if relevant): ~`<XX>%` — confidence `<low / medium / high>`

**R&D split** (if relevant):
- model training / pre-training: ~`<XX>%`
- inference optimization: ~`<XX>%`
- platform / infra engineering: ~`<XX>%`

**S&M as % of revenue**: ~`<XX>%`

**Estimated gross margin**: ~`<XX>%`

**Source of estimate**: `<10-K / investor deck / industry report / inferred / "not disclosed">`

**Notes**:

`<80–150 words: cost structure picture. What's the main cost driver? GPU
CapEx vs OpEx-rented? Are unit economics positive? If private, what proxies
exist (headcount, funding, GPU bills)?>`

---

## 4. Differentiation / moat

**Differentiation claims** (from candidate's own proposal): `<list, e.g. proprietary model, network effects, switching costs, regulatory anchor, hardware-IP, data moat, brand>`

**Assessment** (durable barrier vs marketing positioning):
- `<claim 1>` — `<durable barrier | marketing | mixed>` because `<reason>`
- `<claim 2>` — …

**Notes**:

`<80–150 words: where is this candidate genuinely defensible? Proprietary
models? Hardware? Network effects? Distribution channels? Talent / IP?
Compliance certifications? Customer lock-in? What evidence supports the
moat claim — and what would erode it? If the candidate makes no moat
claim, note "none-claimed" so U-check FAILs cleanly.>`

---

## 5. Strategic vulnerabilities

**Identified risks** (REQUIRED — must enumerate at least 1):
- `<vulnerability 1>`: `<description + likelihood + impact>`
- `<vulnerability 2>`: …

**Common categories to consider**:
- GPU-supply dependency (single chip-maker / single cloud / spot-price exposure)
- Model staleness (proprietary model falling behind OSS frontier)
- Channel-disintermediation (cloud partner could resell directly)
- Customer concentration (one customer > X% of revenue)
- Regulatory exposure (GDPR / CN realname / export control / AI-safety legislation)
- Cash burn / unit-economics (unprofitable at current scale)
- Market timing (entering a category that's already commoditized OR not yet existent)

**Notes**:

`<80–150 words: what could go wrong? Which vulnerability is most likely to
bite first? Are there signals from the free-text (over-claiming, vague
target customer, weak moat claim) suggesting the candidate hasn't thought
the failure modes through?>`

---

## Notes for the profiler

- Do not assign an archetype. Market and moat checks operate directly on the
  proposal and this five-dimension profile.
- `Founding year` is REQUIRED — if free-text doesn't state it explicitly,
  extract the most plausible year from any cited founding date, "founded
  in YYYY" phrasing, Crunchbase metadata, etc. If truly unrecoverable,
  emit `**Founding year**: 0000` (a sentinel) — downstream pipeline will
  halt M-check with an "un-evaluable founding year" error.
- The 5-dim word counts are sanity-checked by `validate_card.py` (target
  80-150 each; minimum 50). The Strategic vulnerabilities dim must
  enumerate at least 1 risk (bullet or numbered list) — cannot be empty.
