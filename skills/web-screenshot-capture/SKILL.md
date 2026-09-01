---
name: web-screenshot-capture
description: "Capture full-page screenshots of any public web page with Playwright (async browser, networkidle wait with load fallback, incremental scroll to trigger lazy/virtual-scroll rendering, optional viewport-by-viewport composite mode for long lists, --no-resume idempotency, date-stamped filenames) and shrink the resulting PNGs to webp with Pillow (quality 85, RGBA flattened on white, oversize-safe >16383px, idempotent skip + --force). Two standalone scripts, minimal deps (playwright, pillow). Use for visual archives of public sites, competitor pricing/pages research, docs snapshots. Trigger on: 'screenshot this website', 'capture web pages for archive', 'convert screenshots to webp'."
---

# Web Screenshot Capture — Playwright full-page capture + PNG→webp

Capture full-page screenshots of any public website with Playwright, then
shrink the PNGs to webp to save ~60-80% disk space. Two standalone scripts
with no shared state.

## Features

`scripts/capture_screenshots.py` takes a URL list and saves one
date-stamped full-page PNG per URL:

- Filename derives from the URL hostname + path, e.g.
  `https://example.com/docs` → `example.com__docs__2026-09-01.png`.
- `networkidle` navigation with a `load`-based fallback for slow /
  long-polling pages; configurable per-page settle time (`--wait`).
- Incremental scroll-to-bottom before capture triggers lazy /
  virtual-scroll rendering so long pages are fully captured.
- `--composite` mode stitches viewport-sized tiles into one tall PNG for
  pages whose virtual lists unmount off-screen items (very tall pages are
  otherwise truncated by full_page capture).
- `--no-resume` re-captures even when the dated output file already exists.
- Dead-page detection recreates the browser page mid-run instead of
  aborting the batch.

`scripts/convert_to_webp.py` converts PNG screenshots in a directory to webp:

- Quality 85, `method=6` (good for screenshots with text).
- RGBA/LA flattened on white (webp doesn't handle alpha cleanly).
- Images taller/wider than the 16383px webp limit are left as PNG.
- Deletes originals after conversion (`--keep-png` to retain), idempotent
  skip when the `.webp` already exists (`--force` to re-convert).

## Usage

```bash
# Capture a set of public pages (default output: ~/screenshots/)
python3 scripts/capture_screenshots.py \
    --urls https://example.com https://example.com/docs/pricing \
    --out ~/screenshots

# Long virtual-scroll pages: stitch viewport tiles into one composite PNG
python3 scripts/capture_screenshots.py \
    --urls https://example.com/models \
    --out ~/screenshots --composite

# Server / CI environments: no visible browser window
python3 scripts/capture_screenshots.py --headless --urls https://example.com

# Re-capture even if today's dated file already exists
python3 scripts/capture_screenshots.py --no-resume --urls https://example.com

# Convert the captured PNGs to webp
python3 scripts/convert_to_webp.py --input ~/screenshots
python3 scripts/convert_to_webp.py --input ~/screenshots --keep-png  # keep originals
```

Flag reference:

| Flag | Script | Default | Purpose |
|---|---|---|---|
| `--urls URL [URL ...]` | capture | (required) | URLs to capture, space-separated |
| `--out DIR` | capture | `~/screenshots` | Output directory |
| `--wait SECS` | capture | `2.0` | Per-page settle time after load |
| `--composite` | capture | off | Viewport-tile stitching for virtual-scroll pages |
| `--headless` | capture | off | Run Chromium without a visible window |
| `--no-resume` | capture | off | Re-capture even if the dated PNG exists |
| `--input DIR` | convert | `~/screenshots` | Directory of PNGs to convert |
| `--keep-png` | convert | off | Keep original PNGs after conversion |
| `--force` | convert | off | Re-convert even if the `.webp` exists |

## Requirements

- Python 3.10+ (uses `list[dict]` / `str | None`-style typing).
- `playwright` + installed Chromium: `pip install playwright && playwright install chrome`.
- `pillow` (used by composite capture and the webp converter).
- Capture runs Chromium visible by default (Mac), matching the original
  design; use `--headless` for headless servers.

## Self-test

Quick end-to-end smoke check:

```bash
python3 -m py_compile scripts/capture_screenshots.py scripts/convert_to_webp.py
python3 scripts/capture_screenshots.py --headless \
    --urls https://example.com --out /tmp/ws-shot-test
python3 scripts/convert_to_webp.py --input /tmp/ws-shot-test
```

Expect two files in `/tmp/ws-shot-test/`
(`example.com__home__<date>.png` converted to `.webp`), a successful
capture summary, and a webp conversion summary showing the size ratio.
Run this before batch jobs depend on the capture.