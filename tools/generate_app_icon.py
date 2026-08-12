"""Generate the approved minimal BLF2MDF app icon as PNG and ICO."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
PNG_PATH = ROOT / "assets" / "blf2mdf_icon.png"
ICO_PATH = ROOT / "assets" / "blf2mdf_icon.ico"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SCALE = 4
CANVAS = 1024


def _scaled(points):
    return tuple(tuple(int(value * SCALE) for value in point) for point in points)


def _round_line(draw, points, fill, width):
    points = _scaled(points)
    width *= SCALE
    radius = width // 2
    draw.line(points, fill=fill, width=width, joint="curve")
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def render() -> Image.Image:
    size = CANVAS * SCALE
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (24 * SCALE, 24 * SCALE, 1000 * SCALE, 1000 * SCALE),
        radius=200 * SCALE,
        fill="#087CF0",
    )

    # One symbol is enough: a substantial rounded arrow that survives the
    # 16 px Windows taskbar representation without decorative noise.
    white = "#FFFFFF"
    _round_line(draw, ((240, 512), (776, 512)), white, 96)
    _round_line(draw, ((560, 304), (776, 512), (560, 720)), white, 96)

    return image.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def main() -> int:
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    icon = render()
    icon.save(PNG_PATH, optimize=True)
    icon.save(
        ICO_PATH,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
    )
    print(PNG_PATH)
    print(ICO_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
