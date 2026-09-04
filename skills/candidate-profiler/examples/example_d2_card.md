# Candidate card — AI Agent Orchestration Marketplace (D2)

**Slug**: d2-agent-orchestration-marketplace
**Public/private**: private (proposal stage)
**Founding year**: 2025
**Card last updated**: 2026-06-04
**Card author**: auto-generated (via candidate-profiler skill)

**Original proposal description**: A multi-vendor AI agent orchestration marketplace where developers create agent workflows (combining tools, prompts, models) and publish them to a central hub. End users invoke agents via API or UI, paying per-call; creators earn revenue share. The platform takes 20% of transaction value as marketplace fee. Bidirectional network effects between creators and users. Differs from frontier-locked agent stores (OpenAI GPT Store, Claude Skills) by supporting any model backend. Initial focus developer + SMB. No proprietary GPU; uses third-party model APIs as backend.

**Closest existing vendor analogues**: HuggingFace (OSS model hub and agent marketplace, ~$70M ARR), OpenRouter (multi-vendor inference router, $1.3B valuation, 1T+ tokens/day Feb 2026), Replicate (creator marketplace for generative AI).

---

## 1. Revenue model

**Primary revenue stream**: marketplace fee (20% take rate on per-call transaction value).

**Secondary revenue streams**: potential creator subscription tier (premium analytics + visibility), enterprise SLA contracts.

**Pricing structure**:
- Marketplace fee: 20% of per-call transaction
- Creator subscription tier (TBD): premium analytics + visibility
- Enterprise SLA: custom (not disclosed in proposal)

**Proposal claim**: 20% take rate is the primary monetization (Apple/Google App Store standard); creator subscription is forward-looking secondary.

**Plausibility flag**: medium — 20% take rate plausible (matches mainstream platform precedent); creator subscription is speculative second-order revenue.

**Notes**: Marketplace flywheel — creators earn revenue share on agent invocations; users pay per-call. Take rate 20% follows mainstream platform precedent. No upfront fee for creators; freemium-style attraction. Volume discounts and enterprise SLAs not yet defined.

---

## 2. Customer segmentation

**Identifiable segments**: developer (primary launch segment), SMB (early adopters), enterprise (later expansion).

**Estimated mix**: ~60% developer / ~30% SMB / ~10% enterprise initial; targeting ~40% / ~40% / ~20% by year 2.

**Notable disclosed customer logos**: none disclosed (proposal stage, pre-launch).

**Proposal claim**: developer-first launch with SMB adopters then enterprise expansion.

**Plausibility flag**: medium — developer-first launch is common for marketplace; enterprise expansion is forward claim, success not guaranteed.

**Notes**: Two-sided customer dynamic — creators (developers building agents) on supply side, users (devs/SMBs invoking agents) on demand side. Bidirectional network effect critical for liftoff. Geographic skew not specified (assume global English-first).

---

## 3. Cost structure

**GPU spend as % of revenue**: ~0% (platform doesn't host models; uses 3rd-party APIs as backend — pass-through cost, not platform GPU CapEx).

**R&D split**:
- Platform engineering: ~70%
- Creator tooling: ~20%
- Trust / safety / compliance: ~10%

**S&M as % of revenue**: high in early stage (creator acquisition + developer evangelism), tapering as flywheel takes hold.

**Estimated gross margin**: ~70-80% (marketplace economics, no compute COGS).

**Proposal claim**: 0-asset OpEx-light model — no GPU CapEx, marketplace fee is high-margin platform revenue.

**Plausibility flag**: high — OpEx-light marketplace model with no GPU CapEx is structurally sound (matches HuggingFace / OpenRouter precedent).

**Notes**: 0-asset architecture — leverages existing model providers' GPU. Cash burn driven by S&M (creator acquisition) + platform engineering, not compute. Fundraising needed primarily for cold-start phase.

---

## 4. Differentiation / moat

**Differentiation claims**:
- Multi-vendor agent backend (vs frontier-locked GPT Store / Claude Skills)
- Bidirectional network effect (creator + user flywheel)
- Switching cost via accumulated agent workflow IP

**Moat hypothesis (per Helmer 7 Powers)**:
- **Network Economies** (primary): bidirectional agent marketplace.
- **Switching Costs** (secondary): creator IP + user workflow lock-in.

**Proposal claim**: defensibility hinges on liftoff — multi-vendor stays unique advantage AND creators commit IP.

**Plausibility flag**: medium — Network economies hypothesis valid in principle, but incumbents (frontier-locked stores) are formidable; multi-vendor differentiation depends on whether users actually want backend choice vs frontier-locked simplicity. Counter-positioning vs Anthropic Skills uncertain (Anthropic already moving toward multi-vendor).

**Notes**: Network economies build slowly via flywheel; cold-start phase is high-risk. Once flywheel takes hold, switching cost compounds (creator IP + user-side workflow templates). Erosion risk: frontier vendors (OpenAI / Anthropic) extending their stores to multi-vendor backends could re-claim the marketplace layer.

---

## 5. Strategic vulnerabilities

**Identified risks (先验失败模式扫描, per [P1W2c §4.1.1] 6-category checklist)**:

- **Channel disintermediation** (high likelihood / severe impact): Frontier vendors (OpenAI / Anthropic) extending their stores to multi-vendor backends could re-claim the marketplace layer. Anthropic Skills already moves this direction (announced 2026 Q1).
- **Network effect cold-start** (likely / moderate): Bidirectional flywheel needs creator + user both at critical mass. Pre-launch chicken-egg risk; cold-start fundraising drag.
- **Take-rate squeeze** (medium likelihood / moderate impact): If frontier vendors offer free/low-fee marketplace, 20% take rate may need to compress.
- **Regulatory exposure** (low current / moderate potential impact): Agent capabilities under emerging AI safety legislation; takedown / liability obligations could increase compliance cost.
- **Customer concentration** (low — long-tail by design): Marketplace's value comes from many creators / users; no single customer dependency.
- **GPU-supply dependency** (low): Pass-through to 3rd-party APIs; not on candidate's balance sheet.

**Notes**: Top vulnerability is channel disintermediation — frontier vendor stores represent direct competitive pressure from a position of structural advantage (existing user base + free distribution + native model integration).
