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
RUBRIC = REPO / "apps" / "web" / "app" / "rubric.generated.ts"
LOGOS = REPO / "apps" / "web" / "app" / "logos.generated.ts"
LANE_CHROME = REPO / "apps" / "web" / "app" / "lane_chrome.generated.ts"
ICONS = REPO / "apps" / "web" / "app" / "icons.generated.ts"
BANK = REPO / "apps" / "web" / "app" / "bank.generated.ts"
PROVIDER_LABELS = REPO / "apps" / "web" / "app" / "providers.generated.ts"
SEARCH = REPO / "apps" / "web" / "app" / "search.generated.ts"
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


def _jsx_props(tag_html):
    """The opening tag's attributes as JSX props.

    **The element carrying the class has to be the one React renders**, not a
    wrapper around it. `.appbar` is `grid-template-columns: 1fr auto 1fr` and
    `.themeseg` is placed with `justify-self: end`, which applies to a *grid
    item*: wrapping the whole element in a `<span>` made the span the item, so
    the rule selected a child and did nothing, and the theme controls sat
    against the nav instead of the right edge. Same shape as dropping cards
    straight into `.jb-grid`, which is a flex row of `.jb-col`.

    So the attributes travel too, rather than being retyped in the TSX where
    they can drift from what the board actually emits.
    """
    import re
    RENAME = {"class": "className", "tabindex": "tabIndex",
              "for": "htmlFor", "readonly": "readOnly"}
    open_tag = tag_html[:tag_html.index(">") + 1]
    out = {}
    for name, value in re.findall(r'([a-zA-Z-]+)="([^"]*)"', open_tag):
        out[RENAME.get(name, name)] = value
    return out


def split_element(html, start, tag="div"):
    """`(props, inner_html)` for the element at `start`."""
    whole = element_at(html, start, tag)
    inner = whole[whole.index(">") + 1:whole.rindex("</")]
    return _jsx_props(whole), inner


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


def _themeseg_split():
    """The theme controls' own attributes and children, separately.

    v2 has to render `div.themeseg` itself so that the element the stylesheet
    places is the element React puts in the grid. See `_jsx_props`.
    """
    import dashboard
    html = dashboard.build(SYNTHETIC_STATE, "2026-01-01T00:00:00Z")
    return split_element(html, html.index('<div class="themeseg"'))


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
    # The hero mark's intro and its replay. `html.jb-intro` is added by the
    # boot above and gates the contour-draw and ink-wipe; without this nothing
    # ever removes it, so the flourish never ends, and clicking the mark does
    # nothing. The replay needs the reflow between removing and re-adding the
    # class or the browser coalesces the two, which CLAUDE.md records.
    intro = _parses(_span(
        html, "var heroMark = document.querySelector('.jb-morph');",
        "event.preventDefault(); playIntro(); }\n    });\n  }",
        "the hero intro"), "the hero intro")
    # **`_parses` catches taking too little, not too much.** A loose end marker
    # matched 41 KB later and swallowed the rest of the board's script; the
    # result was balanced, so it parsed, and it would have shipped the whole
    # board's wiring inside a hero-intro effect. A span that grew past its own
    # section is not a section.
    if len(intro) > 1500:
        raise SystemExit(f"the hero intro extraction is {len(intro)} chars, "
                         f"which is far past the end of that block; the end "
                         f"marker is matching something later.")
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
    return {"HEAD": head, "THEME_BOOT": boot, "THEME_WIRE": wire,
            "HERO_INTRO": intro}


def rubric_module():
    """The match rubric's component order and weights, as TypeScript.

    Generated because the card's breakdown draws `earned/possible` for each
    component, and `possible` is `match_score.WEIGHTS`. Retyping five integers
    into a TSX file is how the two boards end up disagreeing about what a
    perfect title match is worth, silently, in a bar that still renders.
    """
    import found_lane
    import json
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// The rubric's own component order and weights, from\n"
            "// found_lane.COMPONENTS and match_score.WEIGHTS.\n\n"
            f"export const COMPONENTS = {json.dumps(list(found_lane.COMPONENTS))} as const;\n\n"
            f"export const WEIGHTS: Record<string, number> = "
            f"{json.dumps(dict(found_lane.MATCH_WEIGHTS))};\n")


def logos_module():
    """The trusted logo hosts, as TypeScript.

    **The third copy of this list would have been the one that drifts.**
    `company_logos.TRUSTED_SOURCE_HOSTS` is the source of truth and
    `deploy/api/board.js` already restates it; v2's own proxy needs the same
    set, and a host removed from Python and left in one of the copies is an
    open redirect target nobody is looking at.

    The candidate TLDs travel with it for the same reason. They are a constant
    in the route because spelling them out per job put the Python board's Found
    payload over its page-weight cap, and v2 inherits the shape rather than
    re-deciding it.
    """
    import company_logos
    import json
    hosts = sorted(company_logos.TRUSTED_SOURCE_HOSTS)
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// The hosts a feed logo may be fetched from, from\n"
            "// company_logos.TRUSTED_SOURCE_HOSTS. A third hand-written copy\n"
            "// of this set is an open redirect nobody is watching.\n\n"
            f"export const LOGO_HOSTS: ReadonlySet<string> =\n"
            f"  new Set({json.dumps(hosts, indent=4)});\n\n"
            "// `alt=1` means try the other TLDs of the same label. A constant\n"
            "// here rather than in each URL, which is what kept the Python\n"
            "// board's Found payload inside its cap.\n"
            "export const CANDIDATE_TLDS = "
            f"{json.dumps(['.com', '.io', '.ai', '.co'])} as const;\n")


def lane_chrome_module():
    """The lane subtitles and empty states, from the board's own dictionaries.

    **Copy is data here, not decoration.** Every one of these strings names the
    control that fills the lane -- "Press Apply on something in Watching",
    "Press Scan now and it will go looking" -- and a blank panel saying "Nothing
    in this lane." is the version that reads as broken. v2 shipped that version
    for all five.

    Generated because these are `dashboard.py`'s own `EMPTY` dict and the four
    `hint` lines beside it, and a retyped copy is a copy that drifts. The hints
    live in an f-string rather than a dict, so they are read out of the source
    by the marker each one is written with; a marker that stops matching fails
    here rather than silently emitting nothing.
    """
    import json
    import re
    import dashboard
    source = (REPO / "scripts" / "dashboard.py").read_text(encoding="utf-8")

    # `EMPTY` is local to `build`, so it is read out of the source rather than
    # imported. **Parsed with `ast`, not a regex**: the values are implicitly
    # concatenated string literals split across lines, and the regex written
    # first matched three of the five and silently dropped two.
    import ast
    block = re.search(r"    EMPTY = (\{.*?\n    \})", source, re.S)
    if not block:
        raise SystemExit("dashboard.py's EMPTY dict could not be found; v2 "
                         "would emit no empty states at all")
    empty = {k: list(v) for k, v in ast.literal_eval(block.group(1)).items()}
    if len(empty) < 5:
        raise SystemExit(f"only {len(empty)} empty states parsed out of "
                         f"dashboard.py; a lane with none renders the blank "
                         f"panel this exists to replace")

    hints = {}
    for key, marker in (("act", "Each opens straight to where it is blocked."),
                        ("done", "Already out. Check here before applying again."),
                        ("watch", "Newest first. Search or filter to narrow.")):
        if marker not in source:
            raise SystemExit(f"dashboard.py no longer says {marker!r}, so v2 "
                             f"would ship a subtitle the board does not have")
        hints[key] = marker

    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// The lane subtitles and empty states, from dashboard.py's own\n"
            "// EMPTY dict and hint lines. Copy that names the control which\n"
            "// fills a lane is data, and a retyped copy drifts.\n\n"
            "export const LANE_HINTS: Record<string, string> = "
            + json.dumps(hints, indent=2) + ";\n\n"
            "export const LANE_EMPTY: Record<string, [string, string]> = "
            + json.dumps(empty, indent=2) + ";\n\n"
            "// The board's own placeholder, so the two searches read alike.\n"
            "export const LANE_SEARCH_PLACEHOLDER = "
            + json.dumps("Search company, role, skill, location...") + ";\n")


def icons_module():
    """The five action icons, from `found_lane.SCRIPT`'s own `ICONS`.

    **Redrawing them is how two boards end up with different eyes.** The board
    builds each with `createElementNS` inside its script; what matters is the
    path data, which is parsed out here so v2 renders the same geometry through
    JSX. The comments on each in `found_lane.py` say why the shape is that
    shape -- the eye is drawn because the emoji is a photoreal eyeball on
    macOS, the struck circle means "no, and not deleted".
    """
    import json
    import re
    source = (REPO / "scripts" / "found_lane.py").read_text(encoding="utf-8")
    block = re.search(r"  var ICONS = \{(.*?)\n  \};", source, re.S)
    if not block:
        raise SystemExit("found_lane.py's ICONS block could not be found; v2 "
                         "would ship five identical unlabelled pills")
    icons = {}
    for name, body in re.findall(
            r"(\w+): function \(\) \{\s*return svg\((.*?)\);\s*\}",
            block.group(1), re.S):
        icons[name] = {
            "paths": re.findall(r"'([^']*)'", body),
            "circles": [[float(x) for x in c] for c in re.findall(
                r"\[(\d+(?:\.\d+)?), (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)\]", body)],
        }
    missing = {"watch", "apply", "pass", "posting", "details"} - set(icons)
    if missing:
        raise SystemExit(f"the board's ICONS no longer define {sorted(missing)}, "
                         f"so v2 would ship buttons the board draws with a mark")
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// The five action icons, from found_lane.SCRIPT's own ICONS.\n"
            "// Redrawing them is how two boards end up with different eyes.\n\n"
            "export type Icon = { paths: string[]; circles: number[][] };\n\n"
            "export const ICONS: Record<string, Icon> =\n"
            + json.dumps(icons, indent=2) + ";\n")


def providers_module():
    """What each provider is called, from `settings_panel.PROVIDERS`.

    **The label travels and the model does not**, and the reason is a real
    disagreement between two files. `settings_panel.PROVIDERS` says
    `gemini-3.7-flash`; `providers.MODEL_IDS`, which is the pin every agent
    task actually invokes and which CLAUDE.md records, says
    `gemini-3.7-flash-medium`. The board's Settings card has been naming a
    model it does not run.

    So the display name of the tool comes from the board -- "Gemini CLI", not
    the "agy" a fixture invented -- and the model comes through the mirror from
    the pin, because a card that names the wrong model is worse than one that
    disagrees with the other board.
    """
    import json
    import settings_panel
    import providers as provider_registry
    labels = {pid: {"name": name, "agent": agent}
              for pid, name, agent, _ in settings_panel.PROVIDERS}
    shown = {pid: model for pid, _, _, model in settings_panel.PROVIDERS}
    pinned = dict(provider_registry.MODEL_IDS)
    disagree = sorted(k for k in shown if shown[k] != pinned.get(k))
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// Provider display names, from settings_panel.PROVIDERS.\n"
            "// The model is NOT here: it comes through the mirror from\n"
            "// providers.MODEL_IDS, which is the pin that actually runs.\n"
            f"// The two disagree today for: {', '.join(disagree) or 'nothing'}.\n\n"
            "export const PROVIDER_LABELS: Record<string, "
            "{ name: string; agent: string }> =\n"
            + json.dumps(labels, indent=2) + ";\n")


def search_module():
    """Advanced search's option lists and the location taxonomy.

    **199 countries in six groups, and not a line of it retyped.** The board's
    picker is the storage contract as much as it is a control -- CLAUDE.md:
    empty saved `locations` means Worldwide, a partial selection stores explicit
    country codes, and `CONTINENT_COUNTRIES` assigns each of the 199 to exactly
    one display group. A second copy of that in TypeScript is a second answer to
    "which continent is Turkey in", and the two would part on the first edit.

    The select options come across for the same reason. `EXPERIENCE_OPTIONS`,
    `DEGREE_OPTIONS`, `ROLE_CATEGORIES`, `EMPLOYMENT_TYPES` and `JOB_TYPES` are
    the board's own wording and order; v2 renders the same controls and must not
    invent a seventh way to spell "Full time".
    """
    import json
    import found_lane
    lists = {
        "SCOPE_OPTIONS": found_lane.SCOPE_OPTIONS,
        "WORKPLACES": found_lane.WORKPLACES,
        "EXPERIENCE_OPTIONS": found_lane.EXPERIENCE_OPTIONS,
        "DEGREE_OPTIONS": found_lane.DEGREE_OPTIONS,
        "ROLE_CATEGORIES": found_lane.ROLE_CATEGORIES,
        "EMPLOYMENT_TYPES": found_lane.EMPLOYMENT_TYPES,
        "JOB_TYPES": found_lane.JOB_TYPES,
        "UNKNOWN_TOGGLES": found_lane.UNKNOWN_TOGGLES,
    }
    body = ["// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.",
            "//",
            "// Advanced search's options and the location taxonomy, from",
            "// scripts/found_lane.py. The picker is the storage contract as",
            "// much as a control: empty `locations` means Worldwide and a",
            "// partial selection stores explicit country codes.",
            "",
            "export type Option = [string, string];", ""]
    for name, value in lists.items():
        body.append(f"export const {name}: Option[] = "
                    + json.dumps([list(v) for v in value]) + ";")
    body.append("")
    body.append("/** Every selectable country, by code. */")
    body.append("export const COUNTRY_NAMES: Record<string, string> = "
                + json.dumps(found_lane.COUNTRY_NAMES, sort_keys=True) + ";")
    body.append("")
    body.append("/** Each of the 199 in exactly one display group:")
    body.append("    `[slug, visible name, country codes]`. */")
    body.append("export const CONTINENT_COUNTRIES: [string, string, string[]][] = "
                + json.dumps([[slug, name, list(codes)]
                              for slug, name, codes
                              in found_lane.CONTINENT_COUNTRIES])
                + ";")
    body.append("")
    return "\n".join(body)


def bank_module():
    """The Memory Bank's taxonomy labels, from `answer_categories`.

    The classification itself is stored per row by the mirror -- migration 0014
    says why a TypeScript copy of those word-bounded regexes would be a fourth
    chance at an afternoon this project has lost three times. What travels here
    is only the labels and their order, so the chips read the way the module
    names them and appear in the order it ranks them.

    `other` is emitted even though it can be empty, because the module's own
    docstring says it stays in the UI at zero: a category set that cannot miss
    has stopped classifying.
    """
    import json
    import answer_categories
    labels = [(key, label) for key, label, _ in answer_categories.CATEGORIES]
    labels.append(("other", "Other"))
    kinds = [("question", "Questions"), ("policy", "Your policies"),
             ("story", "Story bank")]
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// The Memory Bank's category labels and order, from\n"
            "// answer_categories.CATEGORIES. The classification itself is a\n"
            "// stored column; only the naming travels.\n\n"
            "export const BANK_CATEGORIES: [string, string][] = "
            + json.dumps(labels) + ";\n\n"
            "// The three kinds of record that share answers.json.\n"
            "export const BANK_KINDS: [string, string][] = "
            + json.dumps(kinds) + ";\n")


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
    props, inner = _themeseg_split()
    lines.append(f"export const THEMESEG_PROPS = {json.dumps(props)};")
    lines.append(f"export const THEMESEG_INNER = {json.dumps(inner)};")
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


#: **One list, walked by the writer, the check and CI.** They were three, and
#: the check named three of the four files this script wrote while CI's
#: porcelain list named three of the five: `rubric.generated.ts` could be stale
#: and both said current. That is the same shape as every other half-check in
#: this repository, and the fix is to stop having several lists rather than to
#: add to the shortest.
#:
#: `outputs()` is called rather than held, because each entry's second half is
#: a renderer and importing this module must not run them.
def outputs():
    return [
        (TARGET, rendered),
        (CHROME, chrome_module),
        (LANES, lane_module),
        (RUBRIC, rubric_module),
        (LOGOS, logos_module),
        (LANE_CHROME, lane_chrome_module),
        (ICONS, icons_module),
        (BANK, bank_module),
        (SEARCH, search_module),
        (PROVIDER_LABELS, providers_module),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the file on disk is stale")
    args = ap.parse_args(argv)

    written = outputs()

    if args.check:
        stale = []
        for path, render in written:
            now = path.read_text(encoding="utf-8") if path.exists() else None
            if now != render():
                stale.append(path.relative_to(REPO))
        if stale:
            print("stale, run 'python3 scripts/sync_v2_styles.py' and commit: "
                  + ", ".join(str(p) for p in stale), file=sys.stderr)
            return 1
        print(f"{len(written)} generated files are current")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    for path, render in written:
        body = render()
        path.write_text(body, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)} ({len(body)} chars)")
    moved = sync_static()
    print(f"synced {len(STATIC_FILES)} static files into apps/web/public"
          + (f" ({', '.join(moved)} changed)" if moved else " (all current)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
