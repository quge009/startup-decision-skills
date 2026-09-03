"""Build M/U-check tavily queries from candidate placeholders (v1.5).

Reads <working_dir>/placeholders.json + uses uniform templates from
templates.py, substitutes mustache `{{name}}` placeholders, writes
<working_dir>/queries.json.

v1.5: archetype-agnostic — same uniform M-check + U-check templates
applied to every candidate. No per-archetype branching. archetype.json
is read only for the `primary` / `secondary` audit trail in the output,
NOT to look up templates.

Usage:
  python3 build_queries.py --working-dir <path> [--placeholders-file <path>]

Defaults: placeholders-file = <working_dir>/placeholders.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Import templates from sibling file
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from templates import (  # noqa: E402
    M_CHECK_TEMPLATES,
    U_CHECK_TEMPLATES,
    CANONICAL_PLACEHOLDERS,
)


def substitute_placeholders(text: str, placeholders: dict) -> str:
    """Replace `{{name}}` with placeholders[name]; leave `<null:name>` token if missing."""

    def replace(match):
        name = match.group(1).strip()
        value = placeholders.get(name)
        if value is None or value == "":
            return f"<null:{name}>"
        return str(value)

    return re.sub(r"\{\{(\w+)\}\}", replace, text)


def build_queries(placeholders: dict, archetype_metadata: dict) -> dict:
    """Build M-check + U-check queries (archetype-agnostic, v1.5)."""
    output = {
        "candidate": placeholders.get("candidate", "<unknown>"),
        "archetype": {
            # Recorded for audit only; does not affect template selection.
            "primary": archetype_metadata.get("primary"),
            "secondary": archetype_metadata.get("secondary"),
        },
        "m_check": {
            "queries": [substitute_placeholders(q, placeholders) for q in M_CHECK_TEMPLATES["queries"]],
            "extract_targets": M_CHECK_TEMPLATES.get("extract_targets", []),
            "note": M_CHECK_TEMPLATES.get("note", ""),
        },
        "u_check": {
            "queries": [substitute_placeholders(q, placeholders) for q in U_CHECK_TEMPLATES["queries"]],
            "focus_notes": U_CHECK_TEMPLATES.get("focus_notes", []),
            "note": U_CHECK_TEMPLATES.get("note", ""),
        },
        "placeholders": placeholders,
        "warnings": [],
    }

    # Flag any unsubstituted placeholders
    for section_key in ("m_check", "u_check"):
        for q in output[section_key]["queries"]:
            if "<null:" in q:
                missing = re.findall(r"<null:(\w+)>", q)
                output["warnings"].append(
                    f"{section_key}: query '{q}' has unresolved placeholder(s): {missing}. "
                    "Either fill placeholder in placeholders.json or remove that query manually."
                )

    return output


def main():
    parser = argparse.ArgumentParser(description="Build tavily queries from placeholders (v1.5)")
    parser.add_argument("--working-dir", required=True, help="Working directory")
    parser.add_argument(
        "--placeholders-file",
        help="Path to placeholders.json (default: <working_dir>/placeholders.json)",
    )
    parser.add_argument(
        "--archetype-file",
        help="Path to archetype.json (default: <working_dir>/archetype.json; "
             "read for audit-trail only in v1.5, not for template selection)",
    )
    args = parser.parse_args()

    working_dir = Path(args.working_dir).resolve()
    placeholders_path = Path(args.placeholders_file) if args.placeholders_file else (
        working_dir / "placeholders.json"
    )
    archetype_path = Path(args.archetype_file) if args.archetype_file else (
        working_dir / "archetype.json"
    )

    if not placeholders_path.exists():
        sys.exit(f"ERROR: placeholders file not found: {placeholders_path}")
    if not archetype_path.exists():
        sys.exit(f"ERROR: archetype file not found: {archetype_path}")

    placeholders = json.loads(placeholders_path.read_text(encoding="utf-8"))
    archetype_metadata = json.loads(archetype_path.read_text(encoding="utf-8"))

    output = build_queries(placeholders, archetype_metadata)

    out_path = working_dir / "queries.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    n_m = len(output["m_check"]["queries"])
    n_u = len(output["u_check"]["queries"])
    print(f"OK: wrote {out_path}")
    print(f"  M-check queries: {n_m}")
    print(f"  U-check queries: {n_u}")
    if output["warnings"]:
        print(f"  Warnings: {len(output['warnings'])}", file=sys.stderr)
        for w in output["warnings"]:
            print(f"    - {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
