<p align="center">
  <img src="assets/startup-decision-skills-banner-v2.svg" width="100%" alt="Startup Decision Skills: thirteen modular agent skills">
</p>

<h1 align="center">Startup Decision Skills</h1>

<p align="center">
  <strong>Evidence-grounded workflows for startup evaluation and investor event-chain research.</strong>
</p>

<p align="center">
  <a href="https://agentskills.io"><img src="https://img.shields.io/badge/Standard-Agent%20Skills-2563EB" alt="Agent Skills standard"></a>
  <a href="https://huggingface.co/datasets/quge007/eventchain"><img src="https://img.shields.io/badge/🤗%20Dataset-EventChain-FFD21E" alt="EventChain dataset"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22C55E" alt="MIT license"></a>
</p>

Thirteen composable Agent Skills turn a startup proposal into an auditable
evaluation—or turn public-source company records into canonical event chains
for retrospective investor-behavior research.

## What you can do

| Research workflow | Start with | Result |
|---|---|---|
| **Paper Part I — Startup proposal evaluation** | [`evaluate-proposal`](skills/evaluate-proposal/) | Candidate profile, market and moat checks, score, and reasoned verdict |
| **Paper Part II — Investor and event-chain evaluation** | [`portfolio-edge-builder`](skills/portfolio-edge-builder/) | Canonical entities, source-grounded events, ordered chains, and descriptive analyses |

## How it works

```text
Paper Part I
Proposal → Candidate profile
              ├→ Market check ─┐
              └→ Moat check ───┴→ Score → Reasoned verdict

Paper Part II
Public sources → Portfolio edges → Canonical entities → Event evidence
                                                   ├→ Funding events ───────────┐
                                                   ├→ Exposure events ──────────┤
                                                   └→ Interface events → Review ┴→ Event chains
                                                                                   ├→ Patterns
                                                                                   └→ Investor behavior
```

The collection covers both sides of a startup decision:

- **Before investment:** evaluate an idea without leaking its later outcome.
- **After investment:** reconstruct how funding, exposure, and investor-company
  interface events unfold over time.

## Start here

Clone the repository, then copy either the complete collection or only the
skills required by your workflow into your agent runtime's skill directory.

```bash
git clone https://github.com/quge009/startup-decision-skills.git

# Codex: install the complete collection
mkdir -p ~/.codex/skills
cp -R startup-decision-skills/skills/. ~/.codex/skills/

# Claude Code: install the complete collection
mkdir -p ~/.claude/skills
cp -R startup-decision-skills/skills/. ~/.claude/skills/
```

For a minimal Part I installation in Codex:

```bash
cp -r startup-decision-skills/skills/evaluate-proposal ~/.codex/skills/
cp -r startup-decision-skills/skills/candidate-profiler ~/.codex/skills/
cp -r startup-decision-skills/skills/market-check ~/.codex/skills/
cp -r startup-decision-skills/skills/moat-check ~/.codex/skills/
```

Each directory follows the [Agent Skills](https://agentskills.io) convention:
`SKILL.md` defines when and how to use the skill, while bundled scripts,
references, schemas, and examples support execution. Start with the workflow
skill when you want orchestration; install a component directly when you need
only one stage.

## Skill catalog

### Startup proposal evaluation — Paper Part I

#### Proposal success prediction

1. [`evaluate-proposal`](skills/evaluate-proposal/) — **Run the end-to-end workflow.**
   Coordinate proposal profiling, evidence checks, scoring, and reasoning;
   produce `aggregate.json` and `reasoning.json`.
2. [`candidate-profiler`](skills/candidate-profiler/) — **Profile the proposal.**
   Structure its claims without adding later outcomes; produce
   `candidate_card.md`.
3. [`market-check`](skills/market-check/) — **Evaluate market opportunity.**
   Retrieve and interpret time-bounded market evidence; produce `m_check.json`.
4. [`moat-check`](skills/moat-check/) — **Evaluate defensibility.**
   Assess VRIO strength and erosion risks using time-bounded evidence; produce
   `u_check.json`.

#### Predictor validation and benchmarking

- [`benchmark-validation`](skills/benchmark-validation/) — **Validate predictor
  performance.** Create outcome labels, reproducible cohorts and holdouts, and
  F0.5 statistics.
- [`leakage-scan`](skills/leakage-scan/) — **Check temporal integrity.** Detect
  post-founding outcome information in candidate cards.

### Investor and event-chain evaluation — Paper Part II

#### Event-chain construction

1. [`portfolio-edge-builder`](skills/portfolio-edge-builder/) — **Build the
   investment graph.** Extract, audit, and merge investor portfolio
   relationships into edge tables.
2. [`entity-dedup`](skills/entity-dedup/) — **Resolve core entities.** Turn
   inconsistent company and investor names into canonical entity tables.
3. [`event-evidence-collector`](skills/event-evidence-collector/) — **Collect
   event evidence.** Produce source-grounded funding, exposure, and Interface
   claims.
4. [`event-chain-builder`](skills/event-chain-builder/) — **Materialize event
   tables.** Resolve claims and build funding, exposure, and raw Interface
   tables from the collected evidence.
5. [`interface-identity-resolution`](skills/interface-identity-resolution/) —
   **Resolve Interface counterparties.** Review raw Interface records and
   materialize identity-resolved Interface v0.4.

After identity review, return the v0.4 Interface table to
`event-chain-builder` for final chain materialization.

#### Event-chain analysis

- [`pattern-engine`](skills/pattern-engine/) — **Evaluate chain patterns.**
  Freeze, audit, evaluate, and register label-blind pattern candidates.
- [`post-investment-behavior`](skills/post-investment-behavior/) — **Describe
  investor behavior.** Produce summaries from temporally ordered investment
  evidence.

`RECOMMEND` and `AVOID` in the pattern engine label **operating actions observed
within event chains**. They are not labels assigned to companies.

## Requirements

Install the repository requirements, or only the packages needed by your
selected workflow:

| Workflow | Packages |
|---|---|
| Evidence retrieval | `tavily-python`, Tavily API access, and an OpenAI-compatible endpoint |
| Entity and chain processing | `pandas`, `pyarrow`, `pyyaml` |
| Portfolio extraction | `pyyaml`, `beautifulsoup4`, `lxml` |

```bash
pip install -r requirements.txt
```

`market-check` and `moat-check` require `TAVILY_API_KEY`.
`event-evidence-collector` requires `OPENROUTER_API_KEY` plus
`TAVILY_API_KEY`, or `SERPER_API_KEY` for the optional Serper backend.
`OPENROUTER_MODEL` and `OPENROUTER_URL` can override the default model and
endpoint. Supply credentials through the environment; never store them in the
repository.

## Dataset

Use the companion [EventChain dataset](https://huggingface.co/datasets/quge007/eventchain)
to explore the released entities, provenance, events, and chain tables without
reconstructing the source data. [`datasets-index.md`](datasets-index.md) maps
each research task to its required tables.

The companion paper is [*From Ideas to Actions: A Public-Data
Decision-Support Toolchain Across the Venture Lifecycle*](https://ssrn.com/abstract=7445800),
available on SSRN. An arXiv link will be added when available. Citation metadata is provided in
[`CITATION.cff`](CITATION.cff).

## Scope and validation

These skills package the methods used in the companion research. They are
research tools, not investment advice, and they do not guarantee complete
coverage of private company activity. Deterministic processing stages validate
their schema contracts; LLM- and web-dependent outputs still require source
review.

The release suite covers all 13 skills and 52 bundled Python files with
structural checks, syntax and CLI checks, offline functional tests, and
workflow-level acceptance scenarios. Repeated schema contracts and shared
helpers are hash-checked for consistency. Long procedures use progressive
disclosure through each skill's referenced workflow documentation.

## License

Code and skill instructions are released under the [MIT License](LICENSE).
Dataset files are separately released under CC BY 4.0.
