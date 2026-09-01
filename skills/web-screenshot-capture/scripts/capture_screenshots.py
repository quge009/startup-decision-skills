"""
Capture full-page screenshots of any public website with Playwright.

Given a list of URLs, opens Chromium, navigates to each page (networkidle
wait with a load-based fallback), settles, scrolls to the bottom to trigger
lazy / virtual-scroll rendering, and saves one full-page PNG per URL.

Output: <--out>/<domain>__<path>__<yyyy-mm-dd>.png
  e.g.  ~/screenshots/example.com__docs__2026-09-01.png

Usage (on Mac):
    pip install playwright pillow
    playwright install chrome
    python3 capture_screenshots.py --urls https://example.com https://example.com/docs

Optional flags:
    python3 capture_screenshots.py --out ~/screenshots --urls https://example.com
    python3 capture_screenshots.py --wait 4 --urls https://example.com      # longer settle time
    python3 capture_screenshots.py --composite --urls https://example.com/models   # virtual-scroll pages
    python3 capture_screenshots.py --headless --urls https://example.com    # no visible browser window
    python3 capture_screenshots.py --no-resume                              # re-capture even if file exists

For smaller files, post-convert the PNGs to webp:
    python3 convert_to_webp.py --input ~/screenshots
"""

import argparse
import asyncio
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

WAIT_SECS = 2.0  # default per-page settle time (seconds)
TODAY = date.today().isoformat()


def surface_slug(url: str) -> tuple[str, str]:
    """Derive (domain, path) surface slugs from a URL hostname + path.

    https://example.com/docs/quickstart -> ("example.com", "docs_quickstart")
    https://example.com/                -> ("example.com", "home")
    """
    parsed = urlparse(url)
    domain = parsed.netloc.replace(":", "_")
    path = parsed.path.strip("/").replace("/", "_")
    path = re.sub(r"[^A-Za-z0-9_-]+", "_", path).strip("_") or "home"
    return domain, path


async def slow_scroll_to_bottom(page, step_px: int = 400, settle_ms: int = 350, max_iters: int = 80):
    """Incrementally scroll to bottom to trigger lazy / virtual-scroll rendering.

    A single scrollTo(bottom) call doesn't render the intervening items on
    react-virtualized pages. Stops when bottom reached OR no new content for
    3 consecutive iters.
    """
    no_progress = 0
    for _ in range(max_iters):
        prev_height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate(f"window.scrollBy(0, {step_px})")
        await page.wait_for_timeout(settle_ms)
        new_height = await page.evaluate("document.body.scrollHeight")
        current_y = await page.evaluate("window.pageYOffset + window.innerHeight")
        if current_y >= new_height - 50:
            break  # reached bottom
        if new_height == prev_height:
            no_progress += 1
            if no_progress >= 3:
                break  # no new content loaded — likely done
        else:
            no_progress = 0
    await page.wait_for_timeout(800)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)


async def capture_composite(page, out_path: Path):
    """Capture long pages with virtual-scroll: scroll viewport-by-viewport and stitch.

    For pages where react-virtualized unmounts off-screen items, a single
    full_page screenshot misses content. This function scrolls in viewport-
    sized steps, captures each viewport with content rendered at that scroll
    position, then stitches tiles into a final composite PNG.

    Requires Pillow (`pip install pillow`).
    """
    from PIL import Image
    import io

    # First, slow-scroll to bottom to trigger total page height stabilization
    await slow_scroll_to_bottom(page, step_px=600, settle_ms=400)

    page_h = await page.evaluate("document.body.scrollHeight")
    viewport_h = await page.evaluate("window.innerHeight")
    viewport_w = await page.evaluate("window.innerWidth")

    print(f"           [composite] page={page_h}px viewport={viewport_w}x{viewport_h}")

    # Capture tiles: scroll position 0, viewport_h, 2*viewport_h, ...
    tiles = []  # list of (y_pos, png_bytes)
    y = 0
    while y < page_h:
        await page.evaluate(f"window.scrollTo(0, {y})")
        await page.wait_for_timeout(500)  # let items at this position render
        # If page grew during scroll, update
        new_page_h = await page.evaluate("document.body.scrollHeight")
        if new_page_h > page_h:
            page_h = new_page_h
        png = await page.screenshot(type="png", full_page=False)
        tiles.append((y, png))
        y += viewport_h
        if len(tiles) > 200:  # safety cap
            break

    # Stitch tiles vertically
    final_h = max(page_h, tiles[-1][0] + viewport_h)
    composite = Image.new("RGB", (viewport_w, final_h), (255, 255, 255))
    for y_pos, png_bytes in tiles:
        img = Image.open(io.BytesIO(png_bytes))
        composite.paste(img, (0, y_pos))
    composite.save(str(out_path), "PNG")
    print(f"           [composite] {len(tiles)} tiles → {final_h}px tall")


async def capture(page, out_path: Path, url: str, extra_wait_s: float = WAIT_SECS, composite_mode: bool = False):
    """Navigate + wait + screenshot a single URL to out_path.

    composite_mode=True: use viewport-by-viewport tile capture (for virtual-
    scroll / lazy-rendering pages). Slower (~5-30s extra) but captures
    everything the page renders.
    """
    try:
        await page.goto(url, wait_until="networkidle", timeout=45_000)
    except Exception:
        # Fallback for slow / long-polling pages
        try:
            await page.goto(url, wait_until="load", timeout=30_000)
        except Exception as e:
            raise RuntimeError(f"Navigate failed: {e}")

    await page.wait_for_timeout(int(extra_wait_s * 1000))

    if composite_mode:
        await capture_composite(page, out_path)
    else:
        # Standard pages: incremental scroll + full_page screenshot
        await slow_scroll_to_bottom(page)
        await page.screenshot(path=str(out_path), full_page=True, type="png")


async def ensure_page(context, page):
    """If page is dead/closed, return a fresh page; otherwise return the same."""
    try:
        await page.evaluate("1")
        return page
    except Exception:
        print("           (page dead — recreating)")
        try:
            await page.close()
        except Exception:
            pass
        return await context.new_page()


async def capture_urls(context, page_ref: list, urls: list[str], output_dir: Path, wait: float, composite: bool, no_resume: bool):
    """Capture every URL in order. page_ref holds the page so it can be replaced on death."""
    print(f"\n=== Capturing {len(urls)} URLs ===")
    succeeded = 0
    failed = 0
    skipped = 0
    for i, url in enumerate(urls, 1):
        domain, path = surface_slug(url)
        out = output_dir / f"{domain}__{path}__{TODAY}.png"
        if out.exists() and not no_resume:
            skipped += 1
            print(f"  [{i}/{len(urls)}] {domain}/{path}  ✓ exists, skip")
            continue
        mode_tag = " [composite]" if composite else ""
        print(f"  [{i}/{len(urls)}] {domain}/{path}{mode_tag}  {url}")
        try:
            await capture(page_ref[0], out, url, extra_wait_s=wait, composite_mode=composite)
            succeeded += 1
            print(f"           saved {out.stat().st_size // 1024} KB")
        except Exception as e:
            failed += 1
            print(f"           ✗ FAIL {e}")
            page_ref[0] = await ensure_page(context, page_ref[0])

    print(f"\nCapture done: {succeeded} captured, {failed} failed, {skipped} skipped (already exist)")


async def main(args):
    output_dir = args.out
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {output_dir}")
    print(f"Date stamp: {TODAY}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=args.headless)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        # Use list to allow page replacement on death (Python pass-by-reference workaround)
        page_ref = [await context.new_page()]

        await capture_urls(context, page_ref, args.urls, output_dir, args.wait, args.composite, args.no_resume)

        await context.close()
        await browser.close()

    # Final summary
    today_files = sorted(output_dir.glob(f"*__{TODAY}.png"))
    total_kb = sum(f.stat().st_size for f in today_files) // 1024
    print(f"\n=== Final summary ===")
    print(f"Output dir: {output_dir}")
    print(f"Files captured today ({TODAY}): {len(today_files)}")
    print(f"Total disk: {total_kb // 1024} MB ({total_kb} KB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture full-page screenshots of public web pages with Playwright"
    )
    parser.add_argument(
        "--urls", nargs="+", required=True,
        help="URLs to capture, e.g. --urls https://a.com https://b.com",
    )
    parser.add_argument(
        "--out", type=Path, default=Path.home() / "screenshots",
        help="Output directory (default: ~/screenshots)",
    )
    parser.add_argument(
        "--wait", type=float, default=WAIT_SECS,
        help=f"Per-page settle time in seconds (default: {WAIT_SECS})",
    )
    parser.add_argument(
        "--composite", action="store_true",
        help="Use viewport-by-viewport composite capture (for virtual-scroll pages)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser headless (no visible window)",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="Re-capture even if file exists",
    )
    args = parser.parse_args()
    asyncio.run(main(args))