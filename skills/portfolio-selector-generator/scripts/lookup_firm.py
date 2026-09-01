#!/usr/bin/env python3
"""Look up one firm's metadata from top20_vc_portfolio.yaml by slug.

Called by the @portfolio-selector-generator skill (via container Bash) to
avoid having the LLM parse the whole yaml every time. Print a JSON object
so the skill's Claude can Read the stdout and use the values.

Usage:
  python3 lookup_firm.py --slug sequoia-capital
  python3 lookup_firm.py --slug founders-fund --config-path /work/project/configs/top20_vc_portfolio.yaml

Fail-loud (exit 1) if:
  - config yaml is unreadable
  - slug not found in yaml
  - firm row is malformed
"""

import argparse
import json
import sys
from pathlib import Path

import yaml


DEFAULT_CONFIG = "/work/project/configs/top20_vc_portfolio.yaml"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", required=True, help="Firm slug, e.g. sequoia-capital")
    ap.add_argument("--config-path", default=DEFAULT_CONFIG,
                    help=f"Path to top20_vc_portfolio.yaml (default: {DEFAULT_CONFIG})")
    args = ap.parse_args()

    cfg_path = Path(args.config_path)
    if not cfg_path.exists():
        print(f"ERROR: config yaml not found: {cfg_path}", file=sys.stderr)
        return 1
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    firms = cfg.get("firms", [])
    if not firms:
        print(f"ERROR: {cfg_path} has no `firms` list", file=sys.stderr)
        return 1

    match = None
    for firm in firms:
        inv_id = firm.get("investor_id", "")
        if ":" in inv_id and inv_id.split(":", 1)[1] == args.slug:
            match = firm
            break

    if not match:
        available = sorted(f.get("investor_id", "").split(":", 1)[-1] for f in firms)
        print(f"ERROR: slug {args.slug!r} not found in {cfg_path}.\n"
              f"Available slugs: {', '.join(available)}", file=sys.stderr)
        return 1

    # Emit clean subset (the fields the skill actually needs)
    output = {
        "slug":            args.slug,
        "investor_id":     match["investor_id"],
        "canonical_name":  match.get("canonical_name", ""),
        "portfolio_url":   match.get("portfolio_url"),  # may be null (no_public_portfolio firms)
        "tech_flag":       match.get("tech_flag"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
