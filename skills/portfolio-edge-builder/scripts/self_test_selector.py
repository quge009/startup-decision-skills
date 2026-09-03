#!/usr/bin/env python3
"""Deterministic self-test for a candidate selector YAML against a real HTML.

Called by the portfolio-edge-builder skill to replace the previously
LLM-authored ad-hoc Bash + `pip install --user beautifulsoup4` self-test
(which was fragile: some LLM instances gave up on PEP-668 pip failures and
emitted null selectors).

Now the skill just runs:
  python3 self_test_selector.py --yaml <path> --html <path>

Container image has BeautifulSoup + lxml + PyYAML installed in the Dockerfile
(no runtime pip install needed).

Output (stdout, machine-parseable KV lines):
  entries=<N>
  first_names=name1|name2|name3|name4|name5
  status=<ok | fail>
  reason=<one-line explanation on fail; absent on ok>

Exit code:
  0 = validate OK — entries >= 1, first-3 names non-empty
  1 = validate FAIL — entries=0, malformed yaml, entry_selector null, etc.
      (Skill retry loop uses this to trigger a fresh generation attempt.)

With `--write-back`, ALSO writes a structured `self_test:` block into the
yaml on success (preserves the rest of the yaml). This gives every yaml a
machine-readable record of its most recent self-test:

  self_test:
    tested_at: '2026-07-29'
    html_source: <basename of html file>
    entries_found: 52
    sample_names: ["[24]7.ai", "100 Thieves", "Abby Care", "AdMob", "Agency"]
    status: ok

Sanity thresholds (also enforced by orchestrator; script here is a first line
of defense that flags obvious mistakes before writing to output):
  - MIN_ROWS = 1
  - MAX_ROWS = 20000

Usage:
  python3 self_test_selector.py --yaml /tmp/sequoia.yaml --html data/portfolio_html/2026-07-29/sequoia-capital.html
  python3 self_test_selector.py --yaml <path> --html <path> --write-back
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml
from bs4 import BeautifulSoup


MIN_ROWS = 1
MAX_ROWS = 20000
NAMES_TO_SHOW = 5


def _extract(el, extract_spec: str) -> str:
    """Apply an extract_spec (`text` / `attribute:X`) to a BS4 element."""
    if el is None:
        return ""
    if extract_spec == "text":
        return el.get_text(strip=True)
    if extract_spec.startswith("attribute:"):
        attr = extract_spec.split(":", 1)[1]
        return (el.get(attr) or "").strip()
    return el.get_text(strip=True)  # fallback


def emit(status: str, entries: int, first_names: list[str], reason: str = "") -> None:
    """Machine-parseable KV output to stdout."""
    print(f"entries={entries}")
    print(f"first_names={'|'.join(first_names[:NAMES_TO_SHOW])}")
    print(f"status={status}")
    if reason:
        print(f"reason={reason}")


def write_self_test_back(yaml_path: Path, html_path: Path, entries: int, first_names: list[str], status: str) -> None:
    """Append/replace `self_test:` block in the yaml on success.

    Round-trips via PyYAML (safe_load + safe_dump). Preserves all existing
    top-level fields; `self_test` is placed right after `confidence` if that
    field exists, else at the end.
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}
    data["self_test"] = {
        "tested_at": dt.date.today().isoformat(),
        "html_source": html_path.name,
        "entries_found": entries,
        "sample_names": first_names[:NAMES_TO_SHOW],
        "status": status,
    }
    with open(yaml_path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, default_flow_style=False, width=200)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yaml", required=True, help="Path to selector yaml under test")
    ap.add_argument("--html", required=True, help="Path to firm's portfolio-page HTML")
    ap.add_argument("--write-back", action="store_true",
                    help="On success, write a structured `self_test:` block back to the yaml")
    args = ap.parse_args()

    yaml_path = Path(args.yaml)
    html_path = Path(args.html)

    # Load yaml
    if not yaml_path.exists():
        emit("fail", 0, [], f"yaml missing: {yaml_path}")
        return 1
    try:
        with open(yaml_path) as f:
            sel = yaml.safe_load(f)
    except Exception as e:
        emit("fail", 0, [], f"yaml parse error: {type(e).__name__}: {e}")
        return 1

    selectors = sel.get("selectors", {}) if isinstance(sel, dict) else {}
    entry_selector = selectors.get("entry_selector")
    if entry_selector is None:
        emit("fail", 0, [], "entry_selector is null (no selector authored)")
        return 1

    fields = selectors.get("fields", {})
    name_field = fields.get("company_name_raw", {}) or {}
    name_selector = name_field.get("selector") if isinstance(name_field, dict) else None
    name_extract = (name_field.get("extract") if isinstance(name_field, dict) else None) or "text"
    if not name_selector:
        emit("fail", 0, [], "company_name_raw.selector is missing/null")
        return 1

    # Load HTML
    if not html_path.exists():
        emit("fail", 0, [], f"html missing: {html_path}")
        return 1
    try:
        with open(html_path, encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
    except Exception as e:
        emit("fail", 0, [], f"html parse error: {type(e).__name__}: {e}")
        return 1

    # Apply selector
    try:
        entries = soup.select(entry_selector)
    except Exception as e:
        emit("fail", 0, [], f"entry_selector invalid CSS: {type(e).__name__}: {e}")
        return 1

    n = len(entries)
    first_names: list[str] = []
    for e in entries[:NAMES_TO_SHOW]:
        try:
            el = e.select_one(name_selector)
        except Exception as ex:
            emit("fail", n, first_names, f"name_selector invalid CSS: {type(ex).__name__}: {ex}")
            return 1
        first_names.append(_extract(el, name_extract))

    if n < MIN_ROWS:
        emit("fail", n, first_names, f"entries={n} below MIN_ROWS={MIN_ROWS}")
        return 1
    if n > MAX_ROWS:
        emit("fail", n, first_names, f"entries={n} above MAX_ROWS={MAX_ROWS} (over-matched)")
        return 1
    if not any(first_names[:3]):
        emit("fail", n, first_names, "first-3 name extractions all empty (name_selector wrong)")
        return 1

    emit("ok", n, first_names)
    if args.write_back:
        try:
            write_self_test_back(yaml_path, html_path, n, first_names, "ok")
        except Exception as e:
            # Fail loud if we can't write back — the caller expected persistence
            print(f"reason=write-back failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
