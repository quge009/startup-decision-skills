"""
Convert PNG screenshots in a directory to webp to save disk space.

Strategy:
- For PNG within webp dimension limit (max 16383x16383 px): convert with quality=85
- For oversized PNG: keep as PNG (webp can't handle)
- After successful conversion: delete original PNG (use --keep-png to retain both)
- Idempotent: skip files where webp already exists (unless --force)

Usage:
    pip install pillow              # if not already installed
    python3 convert_to_webp.py                        # point at the capture output dir
    python3 convert_to_webp.py --input ~/screenshots  # default input is ~/screenshots
    python3 convert_to_webp.py --keep-png             # convert but don't delete originals
    python3 convert_to_webp.py --force                # re-convert even if .webp exists

Expected: ~60-80% size reduction (PNG -> webp quality 85).
"""

import argparse
from pathlib import Path
from PIL import Image

# webp max dimension per WebP spec
WEBP_MAX_DIM = 16383

# Quality setting (85 = good for screenshots with text; 75 = smaller; 90 = larger)
WEBP_QUALITY = 85


def main(args):
    screenshots_dir = args.input

    if not screenshots_dir.exists():
        print(f"ERROR: {screenshots_dir} not found")
        return

    png_files = sorted(screenshots_dir.glob("*.png"))
    print(f"Found {len(png_files)} PNG files in {screenshots_dir}\n")

    converted = 0
    skipped_oversized = 0
    skipped_existing = 0
    failed = 0
    total_png_bytes = 0
    total_webp_bytes = 0

    for i, png_path in enumerate(png_files, 1):
        webp_path = png_path.with_suffix(".webp")

        if webp_path.exists() and not args.force:
            skipped_existing += 1
            continue

        try:
            img = Image.open(png_path)
            w, h = img.size

            if w > WEBP_MAX_DIM or h > WEBP_MAX_DIM:
                skipped_oversized += 1
                if i <= 30 or i % 50 == 0:
                    print(f"  [{i}/{len(png_files)}] {png_path.name}: {w}x{h} OVERSIZED, kept as PNG")
                continue

            # Convert RGB (webp doesn't always handle RGBA cleanly; flatten on white)
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            img.save(webp_path, "WEBP", quality=WEBP_QUALITY, method=6)

            png_size = png_path.stat().st_size
            webp_size = webp_path.stat().st_size
            total_png_bytes += png_size
            total_webp_bytes += webp_size
            converted += 1

            if not args.keep_png:
                png_path.unlink()

            if i <= 5 or i % 50 == 0:
                ratio = webp_size / png_size * 100
                print(f"  [{i}/{len(png_files)}] {png_path.name}: "
                      f"{png_size//1024}KB → {webp_size//1024}KB ({ratio:.0f}%)")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(png_files)}] {png_path.name}: FAILED {e}")

    print(f"\n=== Summary ===")
    print(f"  Converted:       {converted}")
    print(f"  Skipped (exists):{skipped_existing}")
    print(f"  Oversized (PNG): {skipped_oversized}")
    print(f"  Failed:          {failed}")
    if converted > 0:
        ratio = total_webp_bytes / total_png_bytes * 100
        print(f"\n  Total PNG  → webp: "
              f"{total_png_bytes//1024//1024} MB → {total_webp_bytes//1024//1024} MB "
              f"({ratio:.0f}%, saved {(total_png_bytes - total_webp_bytes)//1024//1024} MB)")
    if not args.keep_png and converted > 0:
        print(f"  Original PNGs deleted (use --keep-png to retain).")
    if skipped_oversized > 0:
        print(f"\n  {skipped_oversized} file(s) too tall for webp ({WEBP_MAX_DIM}px max), "
              f"kept as PNG.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert PNG screenshots to webp")
    parser.add_argument(
        "--input", type=Path, default=Path.home() / "screenshots",
        help="Directory of PNG screenshots to convert (default: ~/screenshots)",
    )
    parser.add_argument("--keep-png", action="store_true", help="Keep original PNG files after conversion")
    parser.add_argument("--force",    action="store_true", help="Re-convert even if .webp exists")
    args = parser.parse_args()
    main(args)