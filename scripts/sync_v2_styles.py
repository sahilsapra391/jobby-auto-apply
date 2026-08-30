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
    lines.append(f"export const SELECT_SCRIPT = {json.dumps(select_script())};")
    return "\n\n".join(lines) + "\n"


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
