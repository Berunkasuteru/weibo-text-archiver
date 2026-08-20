from __future__ import annotations

import argparse
import io
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
PNG_PATH = ASSET_DIR / "app_icon.png"
ICO_PATH = ASSET_DIR / "app_icon.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


BODY_CYAN = "#13B8B2"
FACE_WHITE = "#F7F8F4"
FEATURE_INK = "#18353B"


def _render_mini_eater(size: int):
    """Render the dark-mode-first Mini Eater mascot at one target size."""
    from PIL import Image, ImageDraw

    supersample = 8 if size <= 24 else 4
    scale = size * supersample / 256
    q = lambda value: round(value * scale)
    image = Image.new(
        "RGBA",
        (size * supersample, size * supersample),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)
    tiny = size <= 24
    draw.rounded_rectangle(
        (q(8), q(8), q(248), q(248)),
        radius=q(54 if not tiny else 42),
        fill=BODY_CYAN,
    )
    draw.rounded_rectangle(
        (q(50), q(42 if tiny else 44), q(206), q(192 if tiny else 190)),
        radius=q(38 if not tiny else 30),
        fill=FACE_WHITE,
    )
    eye_y1, eye_y2 = ((72, 116) if tiny else (76, 112))
    left_eye = (68, eye_y1, 106, eye_y2) if tiny else (76, eye_y1, 104, eye_y2)
    right_eye = (150, eye_y1, 188, eye_y2) if tiny else (152, eye_y1, 180, eye_y2)
    for eye in (left_eye, right_eye):
        draw.rounded_rectangle(
            tuple(q(value) for value in eye),
            radius=q(15 if tiny else 12),
            fill=FEATURE_INK,
        )
    intake = (82, 126, 174, 180) if tiny else (88, 130, 168, 176)
    draw.rounded_rectangle(
        tuple(q(value) for value in intake),
        radius=q(18 if tiny else 16),
        fill=FEATURE_INK,
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _write_ico(images: dict[int, object]) -> None:
    payloads = []
    for size in ICO_SIZES:
        stream = io.BytesIO()
        images[size].save(stream, format="PNG", optimize=True)
        payloads.append((size, stream.getvalue()))

    offset = 6 + 16 * len(payloads)
    entries = []
    for size, payload in payloads:
        dimension = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)

    with ICO_PATH.open("wb") as handle:
        handle.write(struct.pack("<HHH", 0, 1, len(payloads)))
        for entry in entries:
            handle.write(entry)
        for _, payload in payloads:
            handle.write(payload)


def generate() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    _render_mini_eater(512).save(PNG_PATH, format="PNG", optimize=True)
    _write_ico({size: _render_mini_eater(size) for size in ICO_SIZES})
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
    sizes = []
    for index in range(count):
        width_byte, height_byte, _, _, _, _, length, offset = struct.unpack_from(
            "<BBBBHHII",
            ico,
            6 + index * 16,
        )
        width = width_byte or 256
        height = height_byte or 256
        if width != height:
            raise RuntimeError(f"app_icon.ico contains non-square size: {width}x{height}")
        payload = ico[offset:offset + length]
        if payload[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"app_icon.ico {width}px frame is not PNG-backed")
        png_width, png_height = struct.unpack(">II", payload[16:24])
        if (png_width, png_height) != (width, height):
            raise RuntimeError(
                f"app_icon.ico frame mismatch: entry {width}x{height}, "
                f"payload {png_width}x{png_height}"
            )
        sizes.append(width)
    if tuple(sizes) != ICO_SIZES:
        raise RuntimeError(f"unexpected app_icon.ico sizes: {sizes}")


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
