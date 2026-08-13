from __future__ import annotations

import argparse
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
PNG_PATH = ASSET_DIR / "app_icon.png"
ICO_PATH = ASSET_DIR / "app_icon.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _scaled(points, scale: int):
    return [(x * scale, y * scale) for x, y in points]


def generate() -> None:
    from PIL import Image, ImageDraw

    scale = 4
    canvas_size = 512
    image = Image.new(
        "RGBA",
        (canvas_size * scale, canvas_size * scale),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)

    charcoal = "#25282D"
    paper = "#F4F1E8"
    fold = "#D8D5CD"

    draw.rounded_rectangle(
        (8 * scale, 8 * scale, 504 * scale, 504 * scale),
        radius=92 * scale,
        fill=charcoal,
    )

    document = _scaled(
        ((126, 78), (318, 78), (392, 152), (392, 430), (126, 430)),
        scale,
    )
    draw.polygon(document, fill=paper)
    draw.line(document + [document[0]], fill=charcoal, width=10 * scale, joint="curve")

    fold_shape = _scaled(((318, 78), (318, 152), (392, 152)), scale)
    draw.polygon(fold_shape, fill=fold)
    draw.line(fold_shape, fill=charcoal, width=10 * scale, joint="curve")

    arrow = _scaled(
        (
            (226, 184),
            (286, 184),
            (286, 278),
            (338, 278),
            (256, 366),
            (174, 278),
            (226, 278),
        ),
        scale,
    )
    draw.polygon(arrow, fill=charcoal)

    image = image.resize((canvas_size, canvas_size), Image.Resampling.LANCZOS)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    image.save(PNG_PATH, format="PNG", optimize=True)
    image.save(ICO_PATH, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
    validate()


def validate() -> None:
    png_header = PNG_PATH.read_bytes()[:24]
    if png_header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("app_icon.png is not a PNG")
    width, height = struct.unpack(">II", png_header[16:24])
    if (width, height) != (512, 512):
        raise RuntimeError(f"unexpected PNG dimensions: {width}x{height}")

    ico = ICO_PATH.read_bytes()
    reserved, image_type, count = struct.unpack("<HHH", ico[:6])
    if reserved != 0 or image_type != 1:
        raise RuntimeError("app_icon.ico has an invalid header")
    sizes = set()
    for index in range(count):
        width_byte, height_byte = struct.unpack_from("BB", ico, 6 + index * 16)
        width = width_byte or 256
        height = height_byte or 256
        if width == height:
            sizes.add(width)
    missing = set(ICO_SIZES) - sizes
    if missing:
        raise RuntimeError(f"app_icon.ico is missing sizes: {sorted(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the original application icon.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate existing assets without importing Pillow",
    )
    args = parser.parse_args()
    if args.check:
        validate()
    else:
        generate()
    print(f"PNG: {PNG_PATH}")
    print(f"ICO: {ICO_PATH}")
    print("ICO sizes: " + ", ".join(map(str, ICO_SIZES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
