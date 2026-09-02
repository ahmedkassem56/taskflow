#!/usr/bin/env python3
"""Taskflow icon generator — DESIGN.md §5.3.

Art: rounded-square vertical gradient #5E6AD2 -> #4A56C6 (accent -> accent-hover),
a 1px inner lighter top highlight, and a white check mark drawn as a thick
polyline (no text -> no font dependency). Rendered at 4x supersample and
LANCZOS-downscaled for crisp edges.

Outputs (into <project root>/static/icons/):
  icon-32.png, icon-192.png, icon-512.png   — "any" purpose: rounded square,
                                               transparent canvas corners
  icon-maskable-512.png                     — full-bleed square background,
                                               glyph inside the central 66% safe zone
  apple-touch-icon.png                      — 180x180, full-bleed, no transparency

The script self-verifies pixel dimensions of every written file and exits
non-zero on any mismatch. Idempotent; safe to run repeatedly.
"""

import os
import sys

from PIL import Image, ImageDraw

ACCENT_TOP = (94, 106, 210)     # #5E6AD2
ACCENT_BOTTOM = (74, 86, 198)   # #4A56C6
WHITE = (255, 255, 255)

SUPERSAMPLE = 4

# (filename, final size px, kind) — kind: 'any' | 'maskable' | 'apple'
TARGETS = [
    ("icon-32.png", 32, "any"),
    ("icon-192.png", 192, "any"),
    ("icon-512.png", 512, "any"),
    ("icon-maskable-512.png", 512, "maskable"),
    ("apple-touch-icon.png", 180, "apple"),
]

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "icons")


def _lerp(c1, c2, t):
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _gradient(size):
    """Vertical gradient image (RGB) accent-top -> accent-hover at bottom."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        color = _lerp(ACCENT_TOP, ACCENT_BOTTOM, y / max(1, size - 1))
        for x in range(size):
            px[x, y] = color
    return img


def _shape_mask(size, kind):
    """Alpha mask for the glyph background shape."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    if kind == "any":
        radius = int(size * 0.22)
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    else:  # maskable / apple: full-bleed square
        d.rectangle([0, 0, size - 1, size - 1], fill=255)
    return mask


def _top_highlight(size, kind):
    """1px inner lighter top highlight, clipped to the shape."""
    # Row-wise white alpha fading from the top edge down to ~14% height.
    hl = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(hl)
    fade = max(1, int(size * 0.14))
    for y in range(fade):
        alpha = round(110 * (1 - y / fade))
        d.line([(0, y), (size, y)], fill=alpha)
    shape = _shape_mask(size, kind)
    return Image.composite(
        Image.new("RGB", (size, size), WHITE),
        Image.new("RGB", (size, size), (0, 0, 0)),
        Image.composite(shape, Image.new("L", (size, size), 0), hl),
    )


def _check_mark(size):
    """White check polyline with round joints/caps."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pts = [
        (size * 0.315, size * 0.545),
        (size * 0.445, size * 0.665),
        (size * 0.695, size * 0.375),
    ]
    width = max(2, int(size * 0.085))
    d.line(pts, fill=WHITE + (255,), width=width, joint="curve")
    # Round caps/joints: PIL line caps are square — paint circles at each vertex.
    r = width / 2.0
    for (x, y) in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE + (255,))
    return layer


def render(kind, final_size):
    """Render one icon at 4x supersample, then LANCZOS-downscale."""
    size = final_size * SUPERSAMPLE
    # Background shape (rounded square for 'any'; full square otherwise).
    base = _gradient(size)
    base.putalpha(_shape_mask(size, kind))
    base = base.convert("RGBA")
    # Top highlight (RGB composite already shaped) blended over the base.
    hl = _top_highlight(size, kind).convert("RGBA")
    base = Image.alpha_composite(base, hl)
    # Check glyph: contained within the central 66% safe zone (17%..83%) for
    # every variant, so the maskable icon's glyph stays in its safe area too.
    check = _check_mark(size)
    base = Image.alpha_composite(base, check)
    # Downscale.
    out = base.resize((final_size, final_size), Image.LANCZOS)
    if kind == "apple":
        out = out.convert("RGB")  # full-bleed, no transparency
    else:
        out = out.convert("RGBA")
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    failures = []
    written = []
    for fname, final_size, kind in TARGETS:
        path = os.path.join(OUT_DIR, fname)
        img = render(kind, final_size)
        img.save(path, format="PNG")
        written.append(path)
        # Self-verify dimensions.
        with Image.open(path) as check:
            actual = check.size
            mode = check.mode
        expected_mode = "RGB" if kind == "apple" else "RGBA"
        if actual != (final_size, final_size) or mode != expected_mode:
            failures.append(f"{fname}: got {actual} {mode}, expected {final_size}x{final_size} {expected_mode}")
            print(f"[FAIL] {fname} -> {actual} {mode} (expected {final_size}x{final_size} {expected_mode})")
        else:
            print(f"[ OK ] {fname} -> {actual} {mode}")

    if failures:
        print("Icon generation FAILED:")
        for f in failures:
            print("  " + f)
        sys.exit(1)
    print(f"All {len(TARGETS)} icons written and verified in {OUT_DIR}")
    sys.exit(0)


if __name__ == "__main__":
    main()
