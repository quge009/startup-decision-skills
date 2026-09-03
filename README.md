# Startups Research Skills · startup-decision-skills

Agent skills extracted from the **business-predictive-model** research work---a
full-lifecycle decision toolchain for tech founders:

* **Part 1 — Pre-BP (business-model evaluation)**: assess a startup
  idea's viability before the pitch and benchmark the evaluation pipeline.
* **Part 2 — Post-BP (post-investment behavior)**: after the pitch, help
  founders find the right capital and avoid pitfalls by profiling investor
  pre/post-investment behavior.

This repo (`startup-decision-skills`) publishes the **12 Claude Agent
Skills** that power both parts.
Each is a standard Agent Skill (a folder with a `SKILL.md` + optional
`scripts/`), self-contained, with no absolute paths or secrets — drop any
folder into `.claude/skills/` (Claude Code) or import it per
[agentskills.io](https://agentskills.io).

## Skills

### Part 1 — Business-model prediction (idea-stage)

| Skill | Tier | What it does |
|---|---|---|
| [evaluate-proposal](skills/evaluate-proposal/) | core | End-to-end orchestrator: free-text proposal → candidate card → archetype → M-check → U-check → verdict + reasoning |
| [candidate-classifier](skills/candidate-classifier/) | core | Candidate-card 5-dim summary + archetype classification + founding year |
| [outcome-labeling](skills/outcome-labeling/) | 1 | Deterministic 8-label outcome taxonomy + verdict↔truth bridge for Crunchbase rows |
| [f05-stats](skills/f05-stats/) | 1 | F0.5, Wilson 95% CI, confusion-matrix cells for calibration |
| [leakage-scan](skills/leakage-scan/) | 2 | Scan candidate cards for post-founding outcome leakage |
| [cohort-sampling](skills/cohort-sampling/) | 2 | Build benchmark cohorts + stratified train/val split + pool-1 sample |

### Part 1 — Benchmark / case study support

| Skill | Tier | What it does |
|---|---|---|
| [web-screenshot-capture](skills/web-screenshot-capture/) | 2 | Full-page web screenshots (Playwright) + PNG→webp conversion |

### Part 2 — Post-investment behavior (investor analysis)

| Skill | Tier | What it does |
|---|---|---|
| [portfolio-selector-generator](skills/portfolio-selector-generator/) | core | Generate CSS-selector configs to extract portfolio companies from VC sites |
| [event-claim-resolution](skills/event-claim-resolution/) | 1 | Merge multi-source event claims into canonical events |
| [event-chain-builder](skills/event-chain-builder/) | 2 | Build multi-source event timelines + materialize outcome chains |
| [entity-dedup](skills/entity-dedup/) | 1 | Dedupe company / investor entities into canonical IDs |
| [pattern-engine](skills/pattern-engine/) | 2 | Config-driven retrospective event-pattern recognition over entity/event data |

**Tier definitions** — `core`: existing production framework skills;
`1`: pure-logic, previously productized script; `2`: needs small
parameterization (done here) before release.

## Installation (Claude Code)

Each skill is a self-contained folder. Copy a skill into your agent skill
directory:

```bash
cp -r skills/evaluate-proposal ~/.claude/skills/
# or, to enable several at once:
cp -r skills/outcome-labeling skills/f05-stats skills/leakage-scan ~/.claude/skills/
```

Skills that invoke LLM orchestration (evaluate-proposal,
candidate-classifier) need an LLM runtime; the pure-compute skills
(outcome-labeling, f05-stats, event-claim-resolution, leakage-scan,
cohort-sampling) run on stdlib alone.

## Dependencies

Most skills are pure Python stdlib. A few need third-party packages:

| Skill | Dependencies |
|---|---|
| entity-dedup | `pandas`, `pyarrow`, `pyyaml` |
| event-chain-builder | `pandas`, `pyarrow` |
| pattern-engine | `pyarrow` |
| portfolio-selector-generator | `pyyaml`, `beautifulsoup4`, `lxml` |
| web-screenshot-capture | `playwright`, `pillow` |

```bash
pip install pandas pyarrow pyyaml beautifulsoup4 lxml playwright pillow
```

## License

[MIT](LICENSE)

## Dataset index

Related datasets published on Hugging Face are listed in
[datasets-index.md](datasets-index.md).
