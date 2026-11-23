"""Generate the original Tempo brand images (abstract calendar + clock motif).

Run with any Python that has Pillow installed::

    python scripts/make_brand.py

It writes ``custom_components/tempo/brand/icon.png`` (256x256), ``icon@2x.png`` (512x512),
and their ``dark_`` variants. The drawing is purely geometric: no font, no third-party logo.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "custom_components" / "tempo" / "brand"
SCALE = 4  # supersampling factor for smooth edges

TEAL = (14, 116, 144, 255)
TEAL_DARK = (8, 80, 102, 255)
AMBER = (245, 158, 11, 255)
WHITE = (255, 255, 255, 255)
INK = (15, 35, 52, 255)


def draw(size: int, dark: bool) -> Image.Image:
    """Draw the icon at ``size`` pixels (transparent background)."""
    big = size * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def px(value: float) -> int:
        return round(value * big)

    # Calendar body (rounded square) with a header band.
    body = (px(0.06), px(0.12), px(0.94), px(0.94))
    radius = px(0.14)
    face = (26, 40, 52, 255) if dark else WHITE
    d.rounded_rectangle(body, radius=radius, fill=TEAL)
    d.rounded_rectangle((px(0.11), px(0.30), px(0.89), px(0.89)), radius=px(0.10), fill=face)
    # Binder rings.
    for x in (0.30, 0.70):
        d.rounded_rectangle(
            (px(x - 0.045), px(0.04), px(x + 0.045), px(0.22)),
            radius=px(0.045),
            fill=TEAL_DARK,
        )

    # Clock face on the calendar page.
    cx, cy, r = 0.5, 0.595, 0.23
    ring = WHITE if dark else INK
    d.ellipse((px(cx - r), px(cy - r), px(cx + r), px(cy + r)), outline=ring, width=px(0.035))
    # Hour ticks at 12, 3, 6, 9.
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        x0, y0 = cx + dx * (r - 0.06), cy + dy * (r - 0.06)
        x1, y1 = cx + dx * (r - 0.025), cy + dy * (r - 0.025)
        d.line((px(x0), px(y0), px(x1), px(y1)), fill=ring, width=px(0.025))
    # Hands: minute hand to 12, hour hand towards 4 (amber accent).
    d.line((px(cx), px(cy), px(cx), px(cy - r + 0.07)), fill=ring, width=px(0.035))
    d.line((px(cx), px(cy), px(cx + 0.12), px(cy + 0.07)), fill=AMBER, width=px(0.04))
    d.ellipse((px(cx - 0.03), px(cy - 0.03), px(cx + 0.03), px(cy + 0.03)), fill=AMBER)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    """Write every brand image."""
    OUT.mkdir(parents=True, exist_ok=True)
    for dark in (False, True):
        prefix = "dark_" if dark else ""
        draw(256, dark).save(OUT / f"{prefix}icon.png", optimize=True)
        draw(512, dark).save(OUT / f"{prefix}icon@2x.png", optimize=True)


if __name__ == "__main__":
    main()
