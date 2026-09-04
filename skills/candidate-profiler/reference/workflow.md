# Candidate profiling workflow

## Inputs and output

Input: a free-text startup proposal and a working directory.

Output: `candidate_card.md`, a five-dimension profile containing required
metadata, including a four-digit founding year. This workflow does not assign a
business archetype and does not perform web research.

## Procedure

1. Preserve the original proposal verbatim in the card metadata.
2. Extract the candidate name, slug, public/private status, and founding year.
   Use `0000` only when the founding year cannot be recovered from the supplied
   material; do not browse to fill it.
3. Complete all five sections in `candidate_card_template.md`: revenue model,
   customer segmentation, cost structure, differentiation/moat, and strategic
   vulnerabilities.
4. Distinguish explicit proposal claims from cautious inference. Use `not
   disclosed` where the proposal supplies no basis for a value. Do not invent
   customers, financials, dates, or later company outcomes.
5. Include at least one strategic vulnerability.
6. Validate before writing:

   ```bash
   python3 ~/.claude/skills/candidate-profiler/scripts/validate_card.py \
     --card-file candidate_card.md
   ```

7. Write the validated output atomically:

   ```bash
   python3 ~/.claude/skills/candidate-profiler/scripts/write_output.py \
     --working-dir <working_dir> --card-file candidate_card.md
   ```

## Boundaries

- The card is a structured representation of the proposal, not a success/fail
  label and not an investment recommendation.
- Do not assign a business archetype. Historical archetype analysis, when
  needed for research benchmarking, belongs to `benchmark-validation` and is
  outside this proposal-evaluation workflow.
- Do not add post-founding outcome evidence. Market and moat evidence is
  collected later by the independent `market-check` and `moat-check` skills.

See `../examples/example_d2_proposal.txt` and
`../examples/example_d2_card.md` for a complete example.
