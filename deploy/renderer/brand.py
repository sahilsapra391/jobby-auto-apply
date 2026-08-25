#!/usr/bin/env python3
"""Jobby brand marks, lifted out of the vendored logo pack at build time.

The board ships as one self-contained HTML file (see publish.py, which inlines
it into the serverless function), so every mark has to travel inside the page.
Nothing here links to a sibling file.

assets/brand/ is the single source of truth. The outlines are read from those
SVGs rather than pasted into this module, so regenerating the pack updates the
board without anyone editing Python.

The mark is the same in every theme: white sock on an ink circle, the pack's
primary. The circle and the knockout are painted with var(--brand-bg) and
var(--brand-fg) so the pair lives in one place, but dashboard.py holds them
fixed rather than flipping them. Only the wordmark moves, on currentColor, so
it stays readable against whichever background it sits on.

The mark outline is ~7KB and appears twice (app bar lockup and hero icon), so
it is emitted once into <defs> and referenced with <use>.
"""

import base64
import re
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1] / "assets" / "brand"

MARK_ID = "jb-mark"

# Transforms that place the shared outline inside each container. Both are the
# pack's own numbers, not recomputed: the mark is optically centred (its mass
# sits left of its bounding box) and rebuilding that by eye would drift.
LOCKUP_VIEWBOX = "0 0 628.15 200"
LOCKUP_MARK_TRANSFORM = "translate(66.670 38.617) scale(0.075178)"
ICON_VIEWBOX = "0 0 512 512"
ICON_MARK_TRANSFORM = "translate(170.675 98.859) scale(0.192456)"


class BrandError(RuntimeError):
    pass


def _read(rel):
    path = BRAND / rel
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise BrandError(f"brand asset missing: {path} ({err})") from err


def _read_bytes(rel):
    path = BRAND / rel
    try:
        return path.read_bytes()
    except OSError as err:
        raise BrandError(f"brand asset missing: {path} ({err})") from err


def _first_path_d(svg, where):
    m = re.search(r'<path[^>]*\sd="([^"]+)"', svg)
    if not m:
        raise BrandError(f"no <path> found in {where}")
    return m.group(1)


def mark_outline():
    """The bare mark, in its own untransformed coordinate space."""
    return _first_path_d(_read("03-mark/jobby-mark-ink.svg"), "03-mark/jobby-mark-ink.svg")


def wordmark_glyphs():
    """The five outlined 'Jobby' glyphs, with the group transform that sets them.

    Poppins Bold converted to curves, so the page carries no font dependency
    and the wordmark renders identically regardless of what is installed.
    """
    svg = _read("04-lockup/jobby-lockup-horizontal-ink.svg")
    # The wordmark is the one group with a flipped y scale (font coordinates).
    m = re.search(
        r'<g[^>]*transform="(translate\([^)]*\)\s*scale\([^)]*-[^)]*\))"[^>]*>(.*?)</g>',
        svg,
        re.S,
    )
    if not m:
        raise BrandError("wordmark group not found in 04-lockup/jobby-lockup-horizontal-ink.svg")
    transform, inner = m.group(1), m.group(2)
    glyphs = re.findall(r'<path[^>]*/>', inner)
    if not glyphs:
        raise BrandError("wordmark group holds no glyphs")
    # Drop per-glyph fills; the group paints them with currentColor.
    glyphs = [re.sub(r'\s*fill="[^"]*"', "", g) for g in glyphs]
    return transform, "".join(glyphs)


def defs():
    """The shared outline, emitted once per page."""
    return (
        '<svg class="jb-defs" aria-hidden="true" focusable="false">'
        f'<defs><path id="{MARK_ID}" fill-rule="evenodd" d="{mark_outline()}"/></defs>'
        "</svg>"
    )


def lockup(cls="lockup"):
    """Mark and wordmark, horizontal. Only the wordmark tracks the theme."""
    transform, glyphs = wordmark_glyphs()
    return (
        f'<svg class="{cls}" viewBox="{LOCKUP_VIEWBOX}" role="img" aria-label="Jobby">'
        '<circle cx="100" cy="100" r="100" fill="var(--brand-bg)"/>'
        f'<use href="#{MARK_ID}" transform="{LOCKUP_MARK_TRANSFORM}" fill="var(--brand-fg)"/>'
        f'<g fill="currentColor" transform="{transform}">{glyphs}</g>'
        "</svg>"
    )


def icon(cls="icon", label="Jobby"):
    """The circular app icon on its own."""
    aria = f'role="img" aria-label="{label}"' if label else 'aria-hidden="true"'
    return (
        f'<svg class="{cls}" viewBox="{ICON_VIEWBOX}" {aria}>'
        '<circle cx="256" cy="256" r="256" fill="var(--brand-bg)"/>'
        f'<use href="#{MARK_ID}" transform="{ICON_MARK_TRANSFORM}" fill="var(--brand-fg)"/>'
        "</svg>"
    )


def mascot(cls="jb-mascot2"):
    """The line-art mascot, for the hover morph on the hero mark.

    Two layers, and the hover animation depends on both. `jm2-contour` is a
    single silhouette path with pathLength="1" that draws itself by running
    stroke-dashoffset from 1 to 0. `jm2-ink` is the filled linework underneath,
    revealed a beat later by a clip-path wipe. Drawn on a 1260x1628 canvas, so
    keep it around 176px tall or the linework stops being crisp.
    """
    svg = _read("06-mascot/jobby-mascot-lineart.svg")
    return svg.replace('class="jb-mascot2"', f'class="{cls}"', 1)


# The pack ships favicon.svg with a dark-chrome flip built in. Jobby shows one
# mark everywhere, so the flip is stripped and the ink circle stays put.
_DARK_FLIP = re.compile(
    r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}"
)


def _pinned_favicon_svg():
    svg = _read("01-favicon/favicon.svg")
    pinned, n = _DARK_FLIP.subn("", svg)
    if n != 1:
        raise BrandError(
            f"expected one prefers-color-scheme block in favicon.svg, found {n}; "
            "the pack format changed, so the icon would flip in dark chrome"
        )
    return pinned


def _favicon_bytes(name):
    """Read a binary favicon out of the brand pack.

    This used to fall back to deploy/public/ "on Vercel", which had it exactly
    backwards. public/ is the static output directory: Vercel serves those
    files at the edge and leaves them out of the serverless bundle, so the
    fallback resolved on any dev machine and raised FileNotFoundError on every
    deployed request. Reading only from BRAND keeps local and deployed on one
    path, which is why sync_vercel_renderer carries the .ico and .png too.
    """
    return _read_bytes(f"01-favicon/{name}")


def favicon_links():
    """Favicon markup, inlined so a saved copy of the board keeps its icon.

    The ICO and the touch icon are already the primary mark, white on ink, so
    only the SVG needs its flip removed. The ICO stays as the fallback for
    browsers that ignore SVG icons.
    """
    svg = base64.b64encode(_pinned_favicon_svg().encode("utf-8")).decode()
    ico = base64.b64encode(_favicon_bytes("favicon.ico")).decode()
    touch = base64.b64encode(_favicon_bytes("apple-touch-icon.png")).decode()
    return (
        f'<link rel="icon" href="data:image/svg+xml;base64,{svg}" type="image/svg+xml">\n'
        f'<link rel="icon" href="data:image/x-icon;base64,{ico}" sizes="32x32">\n'
        f'<link rel="apple-touch-icon" href="data:image/png;base64,{touch}">'
    )


if __name__ == "__main__":
    import json
    import sys

    transform, glyphs = wordmark_glyphs()
    json.dump(
        {
            "brandDir": str(BRAND),
            "markOutlineChars": len(mark_outline()),
            "wordmarkGlyphs": glyphs.count("<path"),
            "faviconBytes": len(favicon_links()),
        },
        sys.stdout,
        indent=2,
    )
    print()
