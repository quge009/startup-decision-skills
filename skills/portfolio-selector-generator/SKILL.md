---
name: portfolio-selector-generator
description: "Generate a CSS-selector YAML config for extracting portfolio companies from a specific VC firm's portfolio page HTML. Given HTML + edges_long spec + firm metadata (investor_id, portfolio_url, tech_flag), produce a per-firm selector config that downstream extractors (ingest_vc_portfolio.py) will consume to emit rows conforming to edges_long_v0.1.spec.md. Trigger on: 'generate portfolio selector', 'create selector config', 'portfolio page selector', 'VC portfolio extractor config'."
---

# Portfolio Selector Generator (v0.1)

You generate a CSS-selector YAML config that tells the downstream extractor
`scripts/ingest_vc_portfolio.py` how to pull portfolio companies out of one
VC firm's portfolio-page HTML. One firm per invocation. The output YAML is
consumed directly by W13's dispatcher; if your output is wrong, the whole
firm's edges get lost or contaminated.

The skill's job is CSS-selector authorship, not orchestration. The Python
orchestrator (`scripts/generate_selectors.py`) handles: fetching HTML,
selecting firms, invoking this skill per firm, and validating output. You
only see one firm's HTML at a time. Install BeautifulSoup, lxml, and PyYAML as
listed in the repository README before running the helper scripts.

## Input

The orchestrator passes the **firm slug** and supplies project locations through
`PORTFOLIO_CONFIG_PATH`, `PORTFOLIO_HTML_DIR`, and `PORTFOLIO_SCHEMA_PATH` (or
equivalent command-line arguments). Firm metadata and paths are resolved
via the helper scripts under this skill's `scripts/` directory (invoked
through the Bash tool; do NOT re-implement them in inline Python—the scripts
wrap BeautifulSoup / lxml / PyYAML deterministically):

- `slug` — firm slug (e.g. `sequoia-capital`), passed via the prompt.
- **Firm metadata** — obtain via
  `python3 ~/.claude/skills/portfolio-selector-generator/scripts/lookup_firm.py --slug ${SLUG} --config-path "${PORTFOLIO_CONFIG_PATH}"`
  which prints a JSON object with `investor_id`, `canonical_name`,
  `portfolio_url`, `tech_flag`. Fail-loud (exit 1) on unknown slug.
- **HTML path** — obtain via
  `python3 ~/.claude/skills/portfolio-selector-generator/scripts/resolve_html_path.py --slug ${SLUG} --html-dir "${PORTFOLIO_HTML_DIR}"`
  which prints the absolute path to the newest cached HTML. Fail-loud if no
  HTML exists (orchestrator would have fetched it before invoking you; this
  guards against invocation-order bugs).
- **Output path** — write below `${PORTFOLIO_OUTPUT_DIR}` as
  `<MODEL_TAG>/<slug>.yaml` when `MODEL_TAG` is non-empty, otherwise
  `<slug>.yaml`. The orchestrator must provide `PORTFOLIO_OUTPUT_DIR`.
- **Spec path** — read from `${PORTFOLIO_SCHEMA_PATH}`.

## Output

A YAML file written to `output_path` following this schema:

```yaml
version: v0.1                          # constant — bump on manual edits later
investor_id: findfunding:sequoia-capital
canonical_name: Sequoia Capital
portfolio_url: https://www.sequoiacap.com/companies
tech_flag: static

selectors:
  entry_selector: "div.company-card"   # CSS selector matching ONE portfolio-entry element
  fields:
    company_name_raw:                  # REQUIRED — every edge needs a name
      selector: "h3.title"             # relative to entry_selector match
      extract: text                    # or "attribute:href", "attribute:alt", etc.
    company_url:                       # OPTIONAL — populate if the page links out
      selector: "a"
      extract: attribute:href
    stage_at_investment:               # OPTIONAL — only if page groups by stage
      selector: null                   # explicit null when the page doesn't provide this field
    industry:                          # OPTIONAL
      selector: "span.industry-tag"
      extract: text
      multi: true                      # true = collect ALL matches into list<str>
    position:                          # OPTIONAL — only when page distinguishes active/exit
      selector: "span.status"
      extract: text
      value_map:                       # map extracted string → position enum
        "Current": active
        "Acquired": acquired
        "IPO": ipo
        "Public": ipo
        "Closed": closed

sanity_checks:
  min_edges: 1                         # loosest default; W14 tightens per-firm after first run
  max_edges: 10000

confidence: 0.95                       # per-firm override; matches tech_flag baseline

_generation_notes: |                   # REQUIRED — see "Notes discipline" below
  <multi-line pipe block explaining the DOM analysis>
```

### Notes discipline — `_generation_notes` is REQUIRED

Every YAML you emit MUST include a top-level `_generation_notes:` field
(multi-line pipe block, minimum 4 lines) documenting:

1. **What the entry_selector matches** — describe the DOM pattern in
   plain English (e.g. "table row with data-toggle attr; each row also
   has a hidden .child sibling for the JS-loaded detail panel").
2. **Coverage** — how many entries does this selector capture? Is it the
   FULL portfolio or a partial view (e.g. "featured exits ticker only —
   full ~850-company portfolio lives in a JSON blob on
   `div[data-companies]` that BS4 CSS can't decompose")?
3. **Why each null field is null** — for every field with
   `selector: null`, one sentence explaining WHY (e.g. "portfolio page
   does not surface industry / vertical tags per row", "company_url is
   only a hash-anchor to a JS-collapsed detail panel, not the external
   company website").
4. **Sanity-check reasoning** — one line explaining the chosen
   `min_edges` / `max_edges` bounds, especially if narrower than the
   [1, 10000] default (e.g. "42-entry ticker, bounded 20..200 to catch
   drift").
5. **Confidence rationale** — one line justifying the chosen `confidence`
   value, especially if below the tech_flag baseline (e.g. "0.85 — below
   static 0.95 baseline because selector captures only a subset of the
   full portfolio").

The notes are consumed by future maintainers (including you on a re-run
6 months later, or a human reviewer during W14 iteration). They also
serve as an audit trail explaining what the LLM inspected and decided.
A YAML without `_generation_notes` — or with a one-line placeholder like
`_generation_notes: "generated"` — will be rejected by the orchestrator
as inadequate provenance and force a retry.

### Field-extract cheat-sheet

- `extract: text` — Beautiful Soup's `.get_text(strip=True)` on the matched element.
- `extract: attribute:<name>` — the element's HTML attribute value (`href`,
  `alt`, `data-name`, ...).
- `multi: true` — the field takes ALL matches (list of extracted values)
  instead of the first. Default `false`.
- `value_map:` — post-extract string → controlled enum. Case-insensitive
  in the extractor; use canonical casing here.
- `selector: null` — explicit "this page doesn't provide this field".
  ALWAYS include the field key with `selector: null` rather than omitting
  the key — this makes the YAML self-documenting about what you inspected.

### Required fields you MUST populate a selector for

- `entry_selector`
- `fields.company_name_raw` (with a real selector, not null)

### Optional fields — populate if the page provides them, else `selector: null`

- `company_url`
- `stage_at_investment`
- `industry`
- `position`

## Steps

1. **Get firm metadata** via
   `python3 ~/.claude/skills/portfolio-selector-generator/scripts/lookup_firm.py --slug ${SLUG} --config-path "${PORTFOLIO_CONFIG_PATH}"`.
   Note the `investor_id`, `canonical_name`, `portfolio_url`, `tech_flag`
   values for the YAML you'll emit.
2. **Get HTML path** via
   `python3 ~/.claude/skills/portfolio-selector-generator/scripts/resolve_html_path.py --slug ${SLUG} --html-dir "${PORTFOLIO_HTML_DIR}"`.
   This is the file you'll Read next.
3. **Read the schema** at `${PORTFOLIO_SCHEMA_PATH}` —
   only skim the columns table + which fields are extracted vs derived. Do
   NOT re-read on subsequent invocations once you know it.
4. **Read the HTML file** returned by step 2. Look at the DOM structure:
   - What is the outermost repeated container for a portfolio company entry?
     Common patterns: `<div class="company-card">`, `<li class="portfolio-item">`,
     `<a class="portfolio-link">`, `<article>`, `<tr>`.
   - Where is the company name within one entry? A heading (`h2`/`h3`/`h4`),
     a `<span class="name">`, or the `alt` of a logo `<img>`.
   - Where is the company URL? Often an outer or inner `<a href>`.
   - Are there stage/industry/position labels grouped by the entries?
     Some pages group by section (e.g. an `<h2>Series A</h2>` header with
     entries under it — use `has-text` or `xpath` if needed; if BS4 CSS-only
     can't express this, use a container selector that captures the whole
     group and note in the YAML that the stage is inferred structurally,
     not by inline selector).
5. **Choose selectors** biased toward specificity (`div.company-card` >
   `div`) and stability (prefer semantic class names / data-attributes over
   generated hashes like `.css-1abc23`).
6. **Write a candidate YAML** to the output path. Include the required
   `_generation_notes` block (see "Notes discipline" above).
7. **Self-test** the candidate via
   `python3 ~/.claude/skills/portfolio-selector-generator/scripts/self_test_selector.py --yaml <output_path> --html <html_path> --write-back`.
   The script parses your YAML, applies the selector to the HTML via
   BeautifulSoup, and prints KV output:
   ```
   entries=<N>
   first_names=name1|name2|name3|name4|name5
   status=<ok | fail>
   reason=<explanation on fail>
   ```
   Exit code 0 = validation passed. Exit code 1 = validation failed
   (yaml malformed / entries=0 / all names empty / etc).

   **`--write-back` on success ALSO writes a structured `self_test:` block
   back into the yaml**, capturing the run's date, entries_found, and
   first-5 sample_names. This gives every yaml a machine-readable record
   of its most recent self-test — used by W15's coverage report and by
   future drift-detection re-runs. You do NOT need to author this block
   yourself; the script writes it after the extractor's own validation
   passes.
8. **On self-test fail**: revise the selectors and re-run step 6-7. Max
   3 rounds. If round 3 also fails, emit a YAML with `entry_selector: null`
   and every field's `selector: null`, but STILL fill in
   `_generation_notes` explaining what DOM patterns you tried and why
   they didn't work — this preserves audit trail. Do NOT silently emit a
   plausible-but-wrong selector.
9. **Print the status summary line** (this is what the orchestrator parses
   from container stdout):
   ```
   [selector-gen] slug=${SLUG} status=<ok|no_selector_found|fail> entries=<N> output=<path>
   ```
   Use the `entries` and `status` values from the LAST self-test's output.

## Failure handling

- If self-test round 1 returns 0 rows OR first-3 names look non-company
  (e.g. "Home", "About", "Login"): revise selectors, try round 2.
- If round 2 also fails: try round 3 with a different anchor strategy
  (e.g. shift from `<div class>` to `<li>` to `<a data-*>`).
- If round 3 also fails: emit a YAML with `entry_selector: null` and
  fields all `selector: null`, plus a top-level `_generation_notes` key
  explaining the DOM patterns tried. The orchestrator flags this as
  `no_selector_found` and reports it. Do NOT silently emit a plausible-
  but-wrong selector.
- If HTML cannot be read (missing file / empty): fail-loud (raise via
  Bash `exit 1`) — the orchestrator has cache logic to retry the HTML fetch.

## Constraints

- **Do not fabricate**. Every selector must correspond to an element pattern
  actually present in the HTML you read. If you're not sure, self-test.
- **Do not invoke external tools** beyond Read / Write / Bash. This skill
  does not need Tavily / Grep / WebFetch.
- **One firm at a time**. The orchestrator loops; you focus on one HTML.
- **Deterministic output shape**. Even when a field isn't available,
  include its key with `selector: null` — future readers should not have
  to guess whether you inspected that field.
- **Confidence value**. Default to the tech_flag baseline (`static` 0.95,
  `js_required` 0.90, `unclear` 0.75). Override lower if your self-test
  count is inside sanity range but the selector is heuristic (e.g. matches
  by CSS class name substring that could collect nav items).

## What this skill does NOT do

- Fetch HTML from the web (orchestrator's job).
- Decide which firms to process (orchestrator's job — loops `top20_vc_portfolio.yaml`).
- Run the resulting selector at scale (W13 dispatcher's job).
- Cross-VC merge / normalize company names (W15's job).
- Choose the LLM model to use (Makefile's `run-generate-selectors` target
  controls this via env var).
