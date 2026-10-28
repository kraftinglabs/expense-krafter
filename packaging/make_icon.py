"""Draw the ExpenseKrafter icon: a pixel-art wallet with a banknote, in the
Kraftinglabs pixel style and palette (https://kraftinglabs.com).

    .venv/Scripts/python packaging/make_icon.py

Writes packaging/icon.ico (the exe icon, used by build.py), packaging/icon.png
and expensekrafter/ui/logo.svg (sidebar logo and favicon). Edit PIXELS and
re-run to change it.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

COLORS = {
    "D": "#1E1D1B",  # Kraftinglabs background
    "P": "#FFAB75",  # peach
    "L": "#FFBE94",  # light peach
    "B": "#624B00",  # brown outline
    "G": "#537C24",  # moss green
    "g": "#7FA845",  # light green
}
# 16 x 16; "." is transparent (the tile's chipped corners).
PIXELS = [
    ".DDDDDDDDDDDDDD.",
    "DDDDDDDDDDDDDDDD",
    "DDDDGGGGGGGGGDDD",
    "DDDDGgggGgggGDDD",
    "DDBBBBBBBBBBBBDD",
    "DBLLLLLLLLLLLLBD",
    "DBPPPPPPPPPPPPBD",
    "DBPPPPPPPBBBBBBD",
    "DBPPPPPPPBLLGLBD",
    "DBPPPPPPPBLLGLBD",
    "DBPPPPPPPBBBBBBD",
    "DBPPPPPPPPPPPPBD",
    "DBPPPPPPPPPPPPBD",
    "DDBBBBBBBBBBBBDD",
    "DDDDDDDDDDDDDDDD",
    ".DDDDDDDDDDDDDD.",
]
GRID = len(PIXELS)
assert all(len(row) == GRID for row in PIXELS)


def _rgba(hex_color: str) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255


def image(size: int) -> Image.Image:
    """Pixel art scales with nearest-neighbour, so edges stay crisp."""
    base = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    for y, row in enumerate(PIXELS):
        for x, ch in enumerate(row):
            if ch != ".":
                base.putpixel((x, y), _rgba(COLORS[ch]))
    return base.resize((size, size), Image.NEAREST)


def svg() -> str:
    rects = [f'<rect x="{x}" y="{y}" width="1" height="1" fill="{COLORS[ch]}"/>'
             for y, row in enumerate(PIXELS) for x, ch in enumerate(row) if ch != "."]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" '
            f'shape-rendering="crispEdges">{"".join(rects)}</svg>\n')


def main() -> None:
    big = image(256)
    # Multiples of 16 only, so every icon size stays pixel-perfect.
    big.save(ROOT / "packaging" / "icon.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    big.save(ROOT / "packaging" / "icon.png")
    (ROOT / "expensekrafter" / "ui" / "logo.svg").write_text(svg(), encoding="utf-8")
    print("Wrote packaging/icon.ico, packaging/icon.png, expensekrafter/ui/logo.svg")


if __name__ == "__main__":
    main()
