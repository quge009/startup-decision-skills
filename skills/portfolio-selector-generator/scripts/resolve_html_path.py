#!/usr/bin/env python3
"""Resolve the newest cached HTML file path for one firm.

Called by the @portfolio-selector-generator skill to avoid the LLM
listing directories to find the latest YYYY-MM-DD subdir. Prints the
absolute path to stdout.

Usage:
  python3 resolve_html_path.py --slug sequoia-capital
  python3 resolve_html_path.py --slug founders-fund --html-dir data/portfolio_html

Fail-loud (exit 1) if:
  - html-dir does not exist
  - no YYYY-MM-DD subdirs
  - no <slug>.html file in any subdir (i.e. orchestrator forgot to fetch first)
"""

import argparse
import os
import re
import sys
from pathlib import Path


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", required=True, help="Firm slug, e.g. sequoia-capital")
    ap.add_argument("--html-dir", default=os.environ.get("PORTFOLIO_HTML_DIR"),
                    help="Base HTML cache directory (or set PORTFOLIO_HTML_DIR)")
    args = ap.parse_args()

    if not args.html_dir:
        ap.error("--html-dir is required unless PORTFOLIO_HTML_DIR is set")

    base = Path(args.html_dir)
    if not base.is_dir():
        print(f"ERROR: html-dir not found: {base}", file=sys.stderr)
        return 1

    date_dirs = sorted(
        (d for d in base.iterdir() if d.is_dir() and DATE_RE.match(d.name)),
        reverse=True,
    )
    if not date_dirs:
        print(f"ERROR: no YYYY-MM-DD subdirs under {base}", file=sys.stderr)
        return 1

    for date_dir in date_dirs:
        candidate = date_dir / f"{args.slug}.html"
        if candidate.exists():
            print(candidate)
            return 0

    tried = " | ".join(str(d / f"{args.slug}.html") for d in date_dirs[:3])
    print(f"ERROR: no {args.slug}.html in any date subdir under {base}. "
          f"Tried: {tried}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
