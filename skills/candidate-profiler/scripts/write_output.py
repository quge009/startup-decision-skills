"""Atomically write candidate_card.md to a working directory.

Usage: python3 write_output.py --working-dir <path> --card-text "<markdown>"

Or pass inputs via files:
       python3 write_output.py --working-dir <path> --card-file <path>

Atomicity: writes via a temporary file and rename.
Working dir is created if missing.
"""

import argparse
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
    parser = argparse.ArgumentParser(description="Write candidate_card.md")
    parser.add_argument("--working-dir", required=True, help="Output directory")
    parser.add_argument("--card-text", help="Inline candidate card markdown")
    parser.add_argument("--card-file", help="Path to candidate card markdown")
    args = parser.parse_args()

    if args.card_text:
        card_text = args.card_text
    elif args.card_file:
        card_text = Path(args.card_file).read_text(encoding="utf-8")
    else:
        sys.exit("ERROR: provide --card-text or --card-file")

    working_dir = Path(args.working_dir).resolve()
    card_path = working_dir / "candidate_card.md"

    try:
        atomic_write(card_path, card_text)
    except OSError as e:
        sys.exit(f"ERROR: write failed: {e}")

    print(f"OK: wrote {card_path}")
    print(f"\nWorking dir: {working_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
