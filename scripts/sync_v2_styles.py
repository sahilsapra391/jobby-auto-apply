#!/usr/bin/env python3
"""Generate the v2 board's stylesheet from the board that exists.

    python3 scripts/sync_v2_styles.py            write apps/web/app/board.css
    python3 scripts/sync_v2_styles.py --check    exit 1 if it is stale

**The requirement is that v2 looks one for one identical to the current board,
and a hand-port cannot deliver that.** It delivers it on the day it is written
and drifts from then on, silently, because nothing compares the two. The first
attempt here was a hand-port and it had already introduced a difference nobody
would have noticed: it forbade `transform` in the panel keyframe, which the
real board uses. The rule CLAUDE.md states is no *fill mode* -- a transform
that is held on a 50,000px layer exceeds the max texture -- and the port had
turned a rule about holding into a rule about transforms.

So the stylesheet is generated rather than copied. `dashboard.build` composes
one `<style>` block from five sources:

    dashboard.py's own inline CSS   53,946 chars
    found_lane.CSS
    settings_panel.CSS
    profile_panel.CSS
    premium_ui.CSS                  24,655 chars, last so it overrides

and this takes that block whole. Identity is then a property of the build
rather than of anyone's care, and `tests/test_v2_styles_are_current.py` fails
the PR when the two drift, the way `sync_vercel_renderer.py` does for the
deployed renderer.

**Rendered from a synthetic state, never the device's.** A fixed empty state
produces byte-identical CSS to a render of the real store -- measured, 78,601
chars both ways -- so there is no reason to read anyone's applications to get
it, and a generator that reads the real store is a generator that can leak it.
"""

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

TARGET = REPO / "apps" / "web" / "app" / "board.css"
CHROME = REPO / "apps" / "web" / "app" / "chrome.generated.ts"
LANES = REPO / "apps" / "web" / "app" / "lanes.generated.ts"
STATIC_SRC = REPO / "deploy" / "public"
STATIC_DST = REPO / "apps" / "web" / "public"

#: The files the board's own head points at by URL rather than inlining.
#: `deploy/public/` is the board's copy and stays the source; v2 is a separate
#: Vercel project with its own static directory, so it needs its own copy and
#: a generator that keeps the two the same. Hand-copying them is how a manifest
#: ends up naming an icon that is not there.
STATIC_FILES = ("site.webmanifest", "favicon.ico", "apple-touch-icon.png",
                "icon-192x192.png", "icon-512x512.png",
                "icon-maskable-512x512.png")

#: Enough shape for `dashboard.build` to render, and nothing real. Keys come
#: from the published state's own contract; a missing one raises here rather
#: than producing a partial stylesheet.
SYNTHETIC_STATE = {
    "userId": "sync", "profile": {}, "memoryBank": {}, "sessions": {},
    "history": [], "roles": {}, "meta": {}, "jobs": [],
    "searchConfig": {}, "providers": {},
}

HEADER = """/*
 * GENERATED. Do not edit.
 *
 * `python3 scripts/sync_v2_styles.py` takes the complete <style> block that
 * `dashboard.build` emits -- dashboard's own CSS, then found_lane,
 * settings_panel, profile_panel and premium_ui in that order, premium_ui last
 * so it overrides -- and writes it here verbatim.
 *
 * The v2 board has to look one for one identical to the board that exists, and
 * a hand-port delivers that on the day it is written and drifts from then on.
 * This makes identity a property of the build. tests/test_v2_styles_are_
 * current.py fails when the two disagree.
 *
 * Change the styling in scripts/premium_ui.py and re-run the generator.
 */
"""


def board_css():
    """The board's complete stylesheet, exactly as the page carries it."""
    import dashboard
    html = dashboard.build(SYNTHETIC_STATE, "2026-01-01T00:00:00Z")
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)
    if len(blocks) != 1:
        raise SystemExit(
            f"expected exactly one <style> block, found {len(blocks)}. The "
            f"board's head changed shape and this generator has to be taught "
            f"the new one rather than guess which block is the stylesheet.")
    return blocks[0]


CHROME_HEADER = """// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.
//
// The board's static chrome, lifted whole rather than transcribed: the brand
// lockup, the SVG defs it references, and the mascot with its wipe clip. These
// are long hand-drawn paths, and a transcription of one is a difference nobody
// will ever spot by eye -- which is exactly the failure mode the identity
// requirement is about.
"""


def element_at(html, start, tag="div"):
    """The balanced `<tag>...</tag>` beginning at `start`.

    **A regex cannot balance tags and `.*?</div>\\s*</div>` looks like it
    can.** The first version used exactly that for the theme controls and
    captured the entire hero with them, so v2 rendered two heroes and two
    mascots -- obvious in a screenshot, invisible in a diff, and produced by a
    pattern that reads as careful.
    """
    depth, i = 0, start
    opener = re.compile(rf"<{tag}\b", re.I)
    closer = re.compile(rf"</{tag}\s*>", re.I)
    while i < len(html):
        o = opener.search(html, i)
        c = closer.search(html, i)
        if c is None:
            raise SystemExit(f"unbalanced <{tag}> from offset {start}")
        if o is not None and o.start() < c.start():
            depth += 1
            i = o.end()
            continue
        depth -= 1
        i = c.end()
        if depth == 0:
            return html[start:i]
    raise SystemExit(f"unbalanced <{tag}> from offset {start}")


def chrome_parts():
    """The pieces of the board's head and hero that are markup, not data."""
    import dashboard
    html = dashboard.build(SYNTHETIC_STATE, "2026-01-01T00:00:00Z")

    def between(pattern):
        found = re.search(pattern, html, re.S)
        if not found:
            raise SystemExit(
                f"could not find {pattern!r} in the rendered board. The markup "
                f"changed shape and this generator has to be taught it rather "
                f"than silently emit less.")
        return found.group(1)

    return {
        # <svg class="lockup"> ... </svg>, the wordmark inside the brand link.
        "LOCKUP": between(r'(<svg class="lockup".*?</svg>)'),
        # The <svg> holding <defs> that the lockup's <use> points at.
        "DEFS": between(r'(<svg[^>]*aria-hidden="true"[^>]*>\s*<defs>.*?</svg>)'),
        # **The whole inside of .jb-morph, not just the SVG.** The plate is a
        # sibling `<span class="jb-plate">` before it, and CLAUDE.md says why
        # it is not decoration: the linework is a single colour, so a dark
        # theme inks it dark and needs the cream oval behind it. Extracting
        # only the SVG produced dark ink on a dark page -- a mascot that was
        # technically present and visually absent.
        "MORPH": between(r'<div class="jb-morph"[^>]*>(.*?)</div>'),
        # The three icon-only theme controls, whole and balanced. Hand-drawing
        # a sun and a moon is how two boards end up with different suns.
        "THEMESEG": element_at(html, html.index('<div class="themeseg"')),
        # The Dropped dock: an archive control, not a workflow tab.
        "DROPPED_DOCK": between(r'(<button class="dropped-dock".*?</button>)'),
    }


def select_script():
    """`premium_ui.SCRIPT`, which is the styled-select face and nothing else.

    **The native `<select>` stays in the DOM owning value and events**, and
    this is the code that puts a face over it. Writing a custom listbox
    instead would look the same and behave differently in every way that
    matters: keyboard, form participation, and the two things CLAUDE.md says
    this face has to keep doing -- saved searches assign to `.value` and
    dispatch nothing, so the face redefines that property to stay in step, and
    an implicit `<label>` forwards clicks to a control that is now invisible.

    Carried rather than reimplemented, for the same reason the stylesheet is.
    """
    import premium_ui
    return premium_ui.SCRIPT


def lane_module():
    """The lane SQL as TypeScript. One spelling of each predicate, generated,
    for the reason `LogoContractTests` exists."""
    import v2_lanes
    return v2_lanes.as_typescript()


def _span(html, start_marker, end_marker, what):
    """The text from `start_marker` through `end_marker`, inclusive."""
    a = html.find(start_marker)
    if a < 0:
        raise SystemExit(f"could not find the start of {what} "
                         f"({start_marker!r}) in the rendered board.")
    b = html.find(end_marker, a)
    if b < 0:
        raise SystemExit(f"found the start of {what} but not its end. The "
                         f"markup changed shape and this generator has to be "
                         f"taught it rather than silently emit less.")
    return html[a:b + len(end_marker)]


def _script_around(html, marker, what):
    """The whole `<script>` element containing `marker`, without its tags."""
    at = html.find(marker)
    if at < 0:
        raise SystemExit(f"could not find {what} ({marker!r}) in the rendered board.")
    start = html.rfind("<script", 0, at)
    open_end = html.find(">", start)
    end = html.find("</script>", at)
    if start < 0 or open_end < 0 or end < 0:
        raise SystemExit(f"{what} is not inside a <script> element.")
    return html[open_end + 1:end].strip()


def _parses(js, what):
    """Refuse JavaScript that is not a program, using a real parser.

    **This is the check that was missing, and its absence cost the whole
    feature.** The first extractor took the theme boot as a span from
    `var t = localStorage...` to `classList.add('jb-intro')`. That begins
    inside a `try {` and ends before the closing `})();`, so what it produced
    carried `} catch (e) {}` with no opener. The browser answered
    `Unexpected token '}'`, both theme scripts died, and three controls
    rendered and did nothing -- the exact symptom the extraction was written
    to cure.

    Every test passed throughout, because they asserted the strings were
    *present*. Present and parseable are different claims, and only one of
    them is the one that matters.

    A hand-rolled bracket counter was tried first and reported the board's own
    155 KB script block as unbalanced, because JavaScript has regex literals
    and division and a counter that does not know the grammar cannot tell them
    apart. So the check is `node --check`, which does.
    """
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(js)
        path = handle.name
    try:
        done = subprocess.run(["node", "--check", path],
                              capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("node is required to verify the extracted scripts parse. "
                         "Emitting an unchecked fragment is what this guards.")
    finally:
        Path(path).unlink(missing_ok=True)
    if done.returncode:
        detail = (done.stderr or "").strip().splitlines()
        raise SystemExit(f"{what} does not parse as JavaScript, so the "
                         f"extraction is a fragment of a larger block rather "
                         f"than a whole one:\n  "
                         + "\n  ".join(detail[:6]))
    return js


def head_parts():
    """The board's own `<head>`, and the two scripts that make the theme work.

    **The favicon is not decoration and it is not four files.** The board
    inlines three icons as data URIs (SVG, ICO, PNG) and links a manifest, and
    it does that because `deploy/public/` is Vercel's static output directory
    and is not in the serverless bundle: a renderer that reads a path under it
    resolves on a laptop and 404s deployed. Carrying the rendered links means
    v2 inherits the resolution that outage already paid for.

    The theme is two scripts, not one, and they are separated on purpose. The
    boot runs before first paint or a dark reader gets a frame of cream. The
    wiring runs after the controls exist. v2 had the boot and not the wiring,
    which is exactly a control that renders and does nothing.
    """
    import dashboard
    html = dashboard.build(SYNTHETIC_STATE, "2026-01-01T00:00:00Z")

    start = html.index("<meta charset=")
    manifest = '<link rel="manifest" href="/site.webmanifest">'
    head = html[start:html.index(manifest) + len(manifest)]
    # The boot is its own small <script> in the head, so the element is the
    # unit. The wiring is one section inside the board's 155 KB main block, so
    # it has to be a span -- and both go through the parser either way.
    boot = _parses(_script_around(html, "localStorage.getItem('jobby.theme')",
                                  "the theme boot"), "the theme boot")
    wire = _parses(_span(html, "var themeBtns = document.querySelectorAll('.themebtn');",
                         "paintTheme(document.documentElement.dataset.theme || 'auto');",
                         "the theme wiring"), "the theme wiring")

    # The storage key is the whole point of carrying this rather than porting
    # it: v2 had written `jobby-theme`, the board writes `jobby.theme`, and a
    # theme saved on one board would not be read by the other.
    for name, body in (("boot", boot), ("wiring", wire)):
        if "jobby.theme" not in body:
            raise SystemExit(f"the theme {name} no longer names jobby.theme; "
                             f"the storage key moved and v2 would silently "
                             f"keep its own.")
    return {"HEAD": head, "THEME_BOOT": boot, "THEME_WIRE": wire}


def chrome_module():
    """A TypeScript module of raw markup strings.

    `json.dumps` rather than Python's own repr: these are 68 KB of SVG with
    quotes, backslashes and non-ASCII in them, and a repr that is valid Python
    is not necessarily valid TypeScript. JSON string syntax is a subset of both.
    """
    import json
    lines = [CHROME_HEADER]
    for name, markup in chrome_parts().items():
        lines.append(f"export const {name} = {json.dumps(markup)};")
    for name, markup in head_parts().items():
        lines.append(f"export const {name} = {json.dumps(markup)};")
    lines.append(f"export const SELECT_SCRIPT = {json.dumps(select_script())};")
    return "\n\n".join(lines) + "\n"


def sync_static():
    """Copy the board's served icons into v2's static directory.

    **Every one of these is reachable only by URL**, so nothing about a
    successful render says they arrived. `site.webmanifest` names three PNGs
    and the head links a fourth; a missing one is a 404 that no test of the
    HTML can see, which is the shape of the outage `deploy/public/` already
    caused once.

    Returns the names that changed, so the caller can report them and CI can
    fail on drift the same way it does for the stylesheet.
    """
    import shutil
    changed = []
    STATIC_DST.mkdir(parents=True, exist_ok=True)
    for name in STATIC_FILES:
        src = STATIC_SRC / name
        if not src.is_file():
            raise SystemExit(f"{src} is missing; the board serves it and v2 "
                             f"cannot be given a copy of a file that is not there.")
        dst = STATIC_DST / name
        if not dst.is_file() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
            changed.append(name)
    return changed


def rendered():
    return HEADER + board_css()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the file on disk is stale")
    args = ap.parse_args(argv)

    fresh = rendered()
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None

    if args.check:
        lanes_now = LANES.read_text(encoding="utf-8") if LANES.exists() else None
        if lanes_now != lane_module():
            print(f"{LANES.relative_to(REPO)} is stale. Run "
                  f"'python3 scripts/sync_v2_styles.py' and commit it.",
                  file=sys.stderr)
            return 1
        chrome_now = CHROME.read_text(encoding="utf-8") if CHROME.exists() else None
        if chrome_now != chrome_module():
            print(f"{CHROME.relative_to(REPO)} is stale. Run "
                  f"'python3 scripts/sync_v2_styles.py' and commit it.",
                  file=sys.stderr)
            return 1
        if current == fresh:
            print(f"{TARGET.relative_to(REPO)} is current ({len(fresh)} chars)")
            return 0
        print(f"{TARGET.relative_to(REPO)} is stale. Run "
              f"'python3 scripts/sync_v2_styles.py' and commit it, or v2 stops "
              f"matching the board it is replacing.", file=sys.stderr)
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(fresh, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)} ({len(fresh)} chars)")
    chrome = chrome_module()
    CHROME.write_text(chrome, encoding="utf-8")
    print(f"wrote {CHROME.relative_to(REPO)} ({len(chrome)} chars)")
    lanes = lane_module()
    LANES.write_text(lanes, encoding="utf-8")
    moved = sync_static()
    print(f"synced {len(STATIC_FILES)} static files into apps/web/public"
          + (f" ({', '.join(moved)} changed)" if moved else " (all current)"))
    print(f"wrote {LANES.relative_to(REPO)} ({len(lanes)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
