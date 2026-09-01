"""Atomically write candidate_card.md + archetype.json to working dir.

Usage: python3 write_outputs.py --working-dir <path> \\
                                --card-text "<markdown>" \\
                                --archetype-json "<json>"

Or pass inputs via files:
       python3 write_outputs.py --working-dir <path> \\
                                --card-file <path> \\
                                --archetype-file <path>

Atomicity: writes via temp files + rename. If either write fails, partial state cleaned.
Working dir is created if missing.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def atomic_write(target: Path, content: str) -> None:
    """Write content to target via temp-then-rename for atomicity."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, target)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="Write candidate_card.md + archetype.json")
    parser.add_argument("--working-dir", required=True, help="Output directory")
    parser.add_argument("--card-text", help="Inline candidate card markdown")
    parser.add_argument("--card-file", help="Path to candidate card markdown")
    parser.add_argument("--archetype-json", help="Inline archetype JSON")
    parser.add_argument("--archetype-file", help="Path to archetype JSON")
    args = parser.parse_args()

    if args.card_text:
        card_text = args.card_text
    elif args.card_file:
        card_text = Path(args.card_file).read_text(encoding="utf-8")
    else:
        sys.exit("ERROR: provide --card-text or --card-file")

    if args.archetype_json:
        archetype_text = args.archetype_json
    elif args.archetype_file:
        archetype_text = Path(args.archetype_file).read_text(encoding="utf-8")
    else:
        sys.exit("ERROR: provide --archetype-json or --archetype-file")

    # Validate archetype JSON parses (defense in depth — main validation in validate_archetype.py)
    try:
        archetype_data = json.loads(archetype_text)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: archetype is not valid JSON: {e}")

    # Pretty-format archetype JSON for readability
    archetype_pretty = json.dumps(archetype_data, indent=2, ensure_ascii=False)

    working_dir = Path(args.working_dir).resolve()
    card_path = working_dir / "candidate_card.md"
    archetype_path = working_dir / "archetype.json"

    try:
        atomic_write(card_path, card_text)
        atomic_write(archetype_path, archetype_pretty + "\n")
    except OSError as e:
        sys.exit(f"ERROR: write failed: {e}")

    print(f"OK: wrote {card_path}")
    print(f"OK: wrote {archetype_path}")
    print(f"\nWorking dir: {working_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
