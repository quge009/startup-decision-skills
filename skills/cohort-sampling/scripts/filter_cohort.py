"""Filter a source company table into a bounded research cohort.

Streams source CSV chunks and applies a caller-supplied category allow-list,
an optional reverse-recall description regex, and a founding-year window.
Outcome classification remains a separate deterministic mapping applied to
every selected row.

Output schema: id, name, website, short_description, categories,
founded_on, plus all raw fields used by outcome_label_mapping
(operating_status, ipo_status, last_funding_type, last_funding_at,
growth_insight_description, locations, permalink, url) plus the
computed outcome_label.

Input directory, filename pattern, output path, and founding-year bounds are
explicit command-line arguments.
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from outcome_label import outcome_label_mapping, VALID_LABELS

csv.field_size_limit(sys.maxsize)

# Default founding window used by the published method; callers may override it.
FOUNDED_YEAR_MIN = 2010
FOUNDED_YEAR_MAX = 2026


# Output column schema. Includes all raw fields used by
# outcome_label_mapping for downstream verification + sensitivity, plus
# the computed outcome_label.
OUTPUT_COLS = [
    "id",
    "name",
    "website",
    "short_description",
    "categories",
    "founded_on",
    "operating_status",
    "ipo_status",
    "last_funding_type",
    "last_funding_at",
    "funding_total",
    "growth_insight_description",
    "locations",
    "permalink",
    "url",
    "outcome_label",          # computed via outcome_label_mapping
    "tier1_match_path",       # 'category' or 'reverse_recall_keyword'
]


def parse_year(s):
    if not s:
        return None
    s = s.strip()
    if len(s) < 4:
        return None
    try:
        return int(s[:4])
    except ValueError:
        return None


def passes_tier_1(row, categories, keyword_re=None):
    """Return ('category', None) if any AI category matches; ('reverse_recall_keyword', None)
    if no AI category but description matches infra keyword regex; else (None, None)."""
    cats_str = row.get("categories") or ""
    row_cats = {c.strip() for c in cats_str.split(",") if c.strip()}
    if row_cats & categories:
        return "category"
    desc = row.get("short_description") or ""
    if keyword_re is not None and keyword_re.search(desc):
        return "reverse_recall_keyword"
    return None


def passes_tier_3(row, year_min=FOUNDED_YEAR_MIN, year_max=FOUNDED_YEAR_MAX):
    yr = parse_year(row.get("founded_on") or "")
    if yr is None:
        return False
    return year_min <= yr <= year_max


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--glob", default="*.csv", help="Input filename pattern")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--founded-year-min", type=int, default=FOUNDED_YEAR_MIN)
    parser.add_argument("--founded-year-max", type=int, default=FOUNDED_YEAR_MAX)
    parser.add_argument("--category", action="append", required=True,
                        help="Allowed category; repeat for multiple values")
    parser.add_argument("--keyword-regex",
                        help="Optional case-insensitive description regex for reverse recall")
    args = parser.parse_args()
    if args.founded_year_min > args.founded_year_max:
        parser.error("--founded-year-min cannot exceed --founded-year-max")
    try:
        keyword_re = re.compile(args.keyword_regex, re.IGNORECASE) if args.keyword_regex else None
    except re.error as exc:
        parser.error(f"invalid --keyword-regex: {exc}")
    categories = frozenset(args.category)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    chunks = sorted(args.input_dir.glob(args.glob))
    if not chunks:
        raise FileNotFoundError(f"no inputs matching {args.glob!r} in {args.input_dir}")
    print(f"scanning {len(chunks)} chunks", file=sys.stderr)

    funnel = Counter()
    label_dist = Counter()
    tier1_path_counts = Counter()
    cohort_rows = 0

    with args.output.open("w", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=OUTPUT_COLS, extrasaction="ignore")
        writer.writeheader()

        for fi, path in enumerate(chunks, 1):
            with path.open() as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    funnel["total"] += 1

                    t1_path = passes_tier_1(row, categories, keyword_re)
                    if t1_path is None:
                        continue
                    funnel["after_tier_1"] += 1

                    if not passes_tier_3(row, args.founded_year_min, args.founded_year_max):
                        continue
                    funnel["after_tier_3"] += 1

                    label = outcome_label_mapping(row)
                    label_dist[label] += 1
                    tier1_path_counts[t1_path] += 1

                    out_row = {k: row.get(k, "") for k in OUTPUT_COLS if k not in ("outcome_label", "tier1_match_path")}
                    out_row["outcome_label"] = label
                    out_row["tier1_match_path"] = t1_path
                    writer.writerow(out_row)
                    cohort_rows += 1

            print(f"  chunk {fi}/{len(chunks)} done; total scanned {funnel['total']:,} cohort {cohort_rows:,}",
                  file=sys.stderr)

    print()
    print("=== Cohort filter funnel ===")
    print(f"  total rows scanned                  : {funnel['total']:,}")
    print(f"  Tier 1 (AI category OR keyword)     : {funnel['after_tier_1']:,}")
    print(f"  + founding window ({args.founded_year_min}-{args.founded_year_max}) : {funnel['after_tier_3']:,}")
    print()
    print(f"Cohort emitted: {cohort_rows:,} rows")
    print(f"Output: {args.output}")
    print()
    print("Tier 1 match path:")
    for path, n in tier1_path_counts.most_common():
        print(f"  {n:>6}  {path}")
    print()
    print("outcome_label distribution within cohort:")
    for label in VALID_LABELS:
        n = label_dist.get(label, 0)
        pct = 100 * n / cohort_rows if cohort_rows else 0
        print(f"  {n:>6}  {label}  ({pct:.2f}%)")
    print()

    # Aggregate roll-up
    success = sum(label_dist.get(l, 0) for l in (
        "POSITIVE_IPO", "POSITIVE_LATE_STAGE_FUNDED", "POSITIVE_ACQUIRED",
    ))
    failure = sum(label_dist.get(l, 0) for l in ("NEGATIVE_CLOSED", "NEGATIVE_NO_TRACTION"))
    equivocal = label_dist.get("EQUIVOCAL_DELISTED", 0)
    indet = label_dist.get("INDETERMINATE", 0) + label_dist.get("UNKNOWN", 0)
    if cohort_rows:
        print(f"  Aggregate (cohort_rows={cohort_rows:,}):")
        print(f"    SUCCESS    : {success:>6,} ({100 * success / cohort_rows:.2f}%)")
        print(f"    FAILURE    : {failure:>6,} ({100 * failure / cohort_rows:.2f}%)")
        print(f"    EQUIVOCAL  : {equivocal:>6,} ({100 * equivocal / cohort_rows:.2f}%)")
        print(f"    INDETERM.  : {indet:>6,} ({100 * indet / cohort_rows:.2f}%)")


if __name__ == "__main__":
    main()
