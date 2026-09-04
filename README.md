<p align="center">
  <img src="assets/startup-decision-skills-banner-v2.svg" width="100%" alt="Startup Decision Skills: thirteen modular agent skills">
</p>

<h1 align="center">Startup Decision Skills</h1>

<p align="center">
  <strong>Reusable Agent Skills for evidence-grounded startup evaluation and investor-behavior research.</strong>
</p>

<p align="center">
  <a href="https://agentskills.io"><img src="https://img.shields.io/badge/Standard-Agent%20Skills-2563EB" alt="Agent Skills standard"></a>
  <a href="https://huggingface.co/datasets/quge007/eventchain"><img src="https://img.shields.io/badge/🤗%20Dataset-EventChain-FFD21E" alt="EventChain dataset"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22C55E" alt="MIT license"></a>
</p>

Thirteen composable skills turn startup proposals and public-source records
into structured candidate cards, time-bounded evidence checks, canonical
entities, event chains, and auditable retrospective patterns.

## What you can do

| Research workflow | Start with | Result |
|---|---|---|
| **Paper Part I — Startup proposal evaluation** | [`evaluate-proposal`](skills/evaluate-proposal/) | An evidence-grounded proposal verdict with an auditable market and moat assessment |
| **Paper Part II — Investor and event-chain evaluation** | [`portfolio-edge-builder`](skills/portfolio-edge-builder/) | Canonical investment relationships, event chains, patterns, and post-investment behavior summaries |

## How it works

```text
Paper Part I
Proposal → Candidate profile
              ├→ Market check ─┐
              └→ Moat check ───┴→ Score → Reasoned verdict

Paper Part II
Public sources → Portfolio edges → Canonical entities → Event evidence
                                                            ├→ Funding + exposure events ───┐
                                                            └→ Interface identity resolution ─┴→ Event chains
                                                                                                  ├→ Patterns
                                                                                                  └→ Post-investment behavior
```

The collection covers both sides of a startup decision:

- **Before investment:** evaluate an idea without leaking its later outcome.
- **After investment:** reconstruct how funding, exposure, and investor-company
  interface events unfold over time.

## Start here

Choose the smallest skill that matches the task. Use
[`evaluate-proposal`](skills/evaluate-proposal/) for the complete Part I
workflow; install its component skills directly when you need only one stage.

```bash
git clone https://github.com/quge009/startup-decision-skills.git
# Install the complete collection:
mkdir -p ~/.claude/skills
cp -R startup-decision-skills/skills/. ~/.claude/skills/

# Or install only the proposal-evaluation workflow:
cp -r startup-decision-skills/skills/evaluate-proposal ~/.claude/skills/
cp -r startup-decision-skills/skills/candidate-profiler ~/.claude/skills/
cp -r startup-decision-skills/skills/market-check ~/.claude/skills/
cp -r startup-decision-skills/skills/moat-check ~/.claude/skills/
```

Each folder follows the [Agent Skills](https://agentskills.io) convention: a
required `SKILL.md` plus only the scripts, references, schemas, and examples the
workflow needs. Install folders in the skill directory supported by your agent
runtime.

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
4. [`interface-identity-resolution`](skills/interface-identity-resolution/) —
   **Resolve Interface counterparties.** Attach reviewed investor or associated
   identities to Interface claims.
5. [`event-chain-builder`](skills/event-chain-builder/) — **Build ordered event
   chains.** Resolve claims and materialize funding, exposure, Interface, and
   chain tables.

#### Event-chain analysis

- [`pattern-engine`](skills/pattern-engine/) — **Evaluate chain patterns.**
  Freeze, audit, evaluate, and register label-blind pattern candidates.
- [`post-investment-behavior`](skills/post-investment-behavior/) — **Describe
  investor behavior.** Produce summaries from temporally ordered investment
  evidence.

`RECOMMEND` and `AVOID` in the pattern engine label **operating actions observed
within event chains**. They are not labels assigned to companies.

## Requirements

Several skills use only the Python standard library. Install optional packages
for the workflows you plan to run:

| Workflow | Packages |
|---|---|
| Evidence retrieval | `tavily-python`, Tavily API access, and an OpenAI-compatible endpoint |
| Entity and chain processing | `pandas`, `pyarrow`, `pyyaml` |
| Portfolio extraction | `pyyaml`, `beautifulsoup4`, `lxml` |

```bash
pip install -r requirements.txt
```

LLM-guided skills require a compatible agent runtime. `market-check` and
`moat-check` require `TAVILY_API_KEY`. `event-evidence-collector` requires
`OPENROUTER_API_KEY` plus `TAVILY_API_KEY`, or `SERPER_API_KEY` when selecting
the optional Serper backend. `OPENROUTER_MODEL` and `OPENROUTER_URL` may override
the endpoint defaults. Credentials must be supplied through the environment and
must never be stored in this repository.

## Dataset

The companion [EventChain dataset](https://huggingface.co/datasets/quge007/eventchain)
contains the released entities, provenance, events, and chain tables. See
[`datasets-index.md`](datasets-index.md) for scope and licensing boundaries.

## Scope and validation

These skills package the methods used in the companion research. They are
research tools, not investment advice, and they do not guarantee complete
coverage of private company activity. Deterministic scripts validate schemas
and fail loudly on contract violations; LLM- and web-dependent outputs still
require source review.

Before release, all 13 skills pass structural validation and workflow-level
smoke tests. All 52 bundled Python files pass syntax checks, and their command
interfaces pass CLI checks.
Repeated schema contracts and shared helpers are hash-checked for consistency.
Long procedures use progressive disclosure through each skill's referenced
workflow documentation.

## License

Code and skill instructions are released under the [MIT License](LICENSE).
Dataset files are separately released under CC BY 4.0.
