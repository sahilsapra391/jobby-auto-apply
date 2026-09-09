"""A class v2 renders must be able to reach the state the stylesheet styles it in.

**Three times now, v2 has shipped markup carrying a class whose styling exists
and whose activating state nothing sets.** Each was invisible in review, because
both halves are correct: the stylesheet is the board's, generated from
`premium_ui.py`, and the markup is the board's too. What is missing is the
third thing, the code that puts the state on, and no assertion about either
half can see it.

    .company-mark img.loaded    46 working logos painted at opacity 0
    .posted.fresh               the freshest role in Watching drawn like the oldest
    .yrs.over                   "7+ years" drawn like "2+ years"

The rule this checks: **if a CSS rule needs two or more classes on the element,
and v2's markup emits one of them, then v2 has to be able to set the others.**
A base class with no reachable state is markup dressed for a treatment it can
never receive.

`board.css` is generated from the board, which has script v2 does not, so a
long tail of board-only states is expected and each one is named below with the
reason. What is not expected, and what fails here, is a *new* one.

The emitted set is deliberately broad: every whitespace-separated token of
every string and template literal under `apps/web/app`. A narrower reader
missed `className={fresh ? "posted fresh" : "posted"}` and reported eleven
false positives, and a check that cries wolf is one nobody reads.
"""

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "apps" / "web" / "app"
CSS = APP / "board.css"

#: Classes v2 never sets, with the reason. **Keyed on the class that is
#: missing**, which is the one the reason is about: an earlier version keyed on
#: `(base, state)` pairs and the "base" it computed was whichever other class
#: happened to be rendered, so `.profile-message.bad` filed itself under
#: `bad`. Each entry is a claim that the markup is right and the behaviour is
#: deliberately elsewhere, not that the finding is noise.
ALLOWED = {
    "scan":
        "the board's Scan now button is `.btn.scan`. v2 renders that control "
        "as `jb-btn primary` inside the Found banner.",
    "marked":
        "the board dims a card its click-memory script has optimistically "
        "acted on. v2's equivalent is `useWrite`, which swaps the row for its "
        "new lane instead of dimming it in place.",
    "ref":
        "a board-only affordance for a card arrived at from a deep link.",
    "fault":
        "`parkClass: capability`, drawn red on the card and its chip to say "
        "the fault is Jobby's rather than the employer's. v2 does not surface "
        "park classification yet.",
    "done":
        "the board ticks off individual blockers within a Needs you card. v2 "
        "renders the list and the count and has no per-item done state.",
    "wide":
        "the board widens the filter row inside a table lane. v2's table lanes "
        "carry their own `LaneSearch` row instead.",
    "landed":
        "saved-search feedback. The control that produces it is Save search, "
        "which is item 21 and behind the write gate.",
    "lost":
        "the same control's failure state. Item 21.",
}

#: Classes carried by elements on the rendered Found lane, read out of the live
#: DOM on 2026-09-03 from a production build of `7c20da1` served against the
#: production database. **This is observed data with provenance, not an
#: assertion**, and it exists for one reason: five of these classes cannot be
#: found by reading the source at all. `className={`jb-chip ${kind}`}` renders
#: `place`, `years`, `pay` and `unknown`, and no static reader can know that.
#: Without them the inverted check below reports `.jb-chip.pay` as a treatment
#: nothing renders, which is false and would be the loudest entry on a list
#: meant to be read.
OBSERVED_ON_THE_PAGE = frozenset({
    "appbar", "boards", "brand", "btn", "chip", "company-cell", "company-mark",
    "danger", "dialog-close", "dialog-head", "dropped-count", "dropped-dialog",
    "dropped-dock", "dropped-label", "eyebrow", "filters", "ghost", "has-logo",
    "hero-copy", "hint", "jb-acts", "jb-addlink", "jb-adv",
    "jb-advbody", "jb-banner", "jb-basic", "jb-btn", "jb-card", "jb-check",
    "jb-checks", "jb-chip", "jb-chips", "jb-co", "jb-col", "jb-continent",
    "jb-continent-count", "jb-continent-list", "jb-continent-main", "jb-count",
    "jb-country", "jb-country-grid", "jb-country-row", "jb-defs", "jb-errored",
    "jb-fact", "jb-fact-fill", "jb-fact-name", "jb-fact-num", "jb-fact-track",
    "jb-facts", "jb-filters", "jb-grid", "jb-head", "jb-identity", "jb-ingest",
    "jb-lab", "jb-location-actions", "jb-location-chevron", "jb-location-clear",
    "jb-location-field", "jb-location-label", "jb-location-menu",
    "jb-location-picker", "jb-location-title", "jb-mascot2", "jb-morph",
    "jb-note", "jb-only", "jb-pills", "jb-plate", "jb-problist", "jb-probs",
    "jb-radio", "jb-radios", "jb-ring", "jb-row", "jb-savestate", "jb-select",
    "jb-select-btn", "jb-select-chevron", "jb-select-menu", "jb-select-opt",
    "jb-select-value", "jb-stack", "jb-sum", "jb-ti", "jb-world",
    "jm2-contour", "jm2-ink", "jm2-wipe-rect", "lane", "lede", "loaded",
    "lockup", "muted", "n", "note-cell", "on", "panel", "pay", "place",
    "primary", "primary-nav", "rowacts-inline", "seg", "segwrap", "sheet",
    "stamp", "tab", "tablewrap", "themebtn", "themeseg", "top", "undrop",
    "unknown", "when", "wrap", "years",
})

#: Styled in `board.css` and rendered by nothing in v2, grouped by the feature
#: they belong to. **This is the measured gap between the two boards, and it is
#: a ratchet**: a class that starts being rendered must be deleted from here,
#: and a class that starts being styled without being rendered fails the build.
#:
#: It is not an exemption list. `ALLOWED` above says "v2 renders this markup and
#: deliberately cannot reach this state"; this says "v2 does not have this
#: feature yet". Blending them would lose the distinction that makes either
#: useful, which is the same reason `answer_record` keeps five claims in five
#: fields.
#:
#: Item 22 is the cautionary entry. It was closed as "already matching" because
#: the stylesheet arrived, and the check that should have caught that could not:
#: it started from classes v2 emits, so a class v2 never emits was outside its
#: subject entirely.
#:
#: **Each group declares what it is waiting on, because that is what makes the
#: list a sequence rather than an inventory.** Three states, and the third is
#: not a smaller version of the second:
#:
#:   "gated"        needs something on the write path that does not exist yet.
#:                  `live_board` flipping on 2026-09-05 was the first half of
#:                  this and is done; what is left is per-feature, and each
#:                  group has to name its own.
#:   "buildable"    reads only. Nothing is stopping it now.
#:   "architectural" v2 does this another way and the class will never be
#:                  rendered. **Not a gap**, and counting it as one overstates
#:                  the remaining work permanently.
STATES = ("gated", "buildable", "architectural")

UNRENDERED = {
    "what is left of the card disclosure after item 22: the score breakdown "
    "bars, and the row of other places a posting was sighted": {
        "state": "buildable",
        "why": "both need columns the Found query does not select. "
               "`job_scores.parts` is already selected and `postings.sources` "
               "is not, so this is a change to the generated SQL in "
               "`scripts/v2_lanes.py` and then markup, with no write in it.",
        "classes": {"jb-bars", "jb-bar", "jb-sight", "jb-more", "jb-skipped",
                    "jb-footer"},
    },
    "what is left of the answer record modal after it landed: the four "
    "`ans-src-*` badge variants, which are built by concatenating the source "
    "onto a prefix": {
        "state": "buildable",
        "why": "**not a gap, a limit of the reader.** The badge class is "
               "`ans-src-` plus the field's own `source`, so it is constructed "
               "at runtime in both boards and no static scan can see it. The "
               "modal renders all four; `dynamic_bases` is the mechanism that "
               "would exempt them if they were reached through a template "
               "literal, and a string concatenation is outside what it reads. "
               "Kept here rather than deleted so the count is not quietly "
               "wrong in the other direction.",
        "classes": {'ans-src-provided', 'ans-src-profile', 'ans-src-generated', 'ans-src-stored'},
    },
    "the resume wording editor: the fields, the role blocks and the rendered "
    "frame beside them": {
        "state": "gated",
        "why": "**not the flag, and not a mirror decision anybody may take.** "
               "`class_b._PROFILE_EXCLUDED` keeps `resume-content.json` out of "
               "`profiles` for two reasons and only one was written down. The "
               "recorded one is integrity: the same wording in two places with "
               "no rule about which wins is the fault that served one "
               "application's wording in place of another's for as long as the "
               "feature existed. The other is that the wording travels to a "
               "provider inside the AES-256-GCM job bundle, so mirroring it "
               "puts a person's employment history in their own words in the "
               "clear in Postgres, on a connection that is the database owner "
               "today. That is Class 1 plaintext where the plan puts Class 2 "
               "ciphertext. Gated on Phase 5's per-account encryption keys, "
               "written as a requirement in `docs/plans/"
               "jobby-v2-rebuild-plan.md`. The rest of the Profile write half "
               "landed without it: the bank, the registry and `edit_outcomes` "
               "are live.",
        "classes": {"wording-field", "wording-list", "wording-role",
                    "resume-frame"},
    },
    "the Memory Bank's alternative answers, and the button that reveals them": {
        "state": "buildable",
        "why": "a read, and the column is the work. `class_b.answer_row` "
               "carries question, value, state, tags, sensitive, kind and "
               "category, and an entry's `alternatives` is not among them, so "
               "there is nothing in `answer_bank` to list. One field on the "
               "row and one column, then markup. No write in it.",
        "classes": {"bank-alts", "bank-more"},
    },
    "the board's Memory Bank grouped under category headings": {
        "state": "buildable",
        "why": "a layout the board chose and v2 did not: the board groups the "
               "bank under headings and v2 filters it with the category chips "
               "above the list, over the same rows from the same query. "
               "Buildable at any time and deliberately not built, because two "
               "ways of narrowing the same list on one page is worse than "
               "either.",
        "classes": {"bank-group", "bank-list"},
    },
    "uploading your own PDF for a new base resume": {
        "state": "buildable",
        "why": "the form is live and copies an existing base, which is what "
               "supplies the contact block and the document. Uploading a file "
               "instead needs the other half of `watch.install_uploaded_"
               "resume`: the board writes the PDF to its own KV key and the "
               "device takes it from there, and v2 has no route that writes "
               "that key. A file control before that route exists is a button "
               "with nothing behind it.",
        "classes": {"base-file"},
    },
    "provider connection state on the Settings page": {
        "state": "buildable",
        "why": "drawing whether a provider is connected is a read of "
               "`provider_links`, which v2 already queries. `provider-message` "
               "left this group when Connect was wired: the control needs a "
               "status line to report a refusal into, so the class the board "
               "already had is the one it reports into. `provider-active` is "
               "the remaining read.",
        "classes": {"provider-active"},
    },
    "the scan banner and its progress": {
        "state": "buildable",
        "why": "the banner is a read of the last sweep and the slot fleet: "
               "`board_meta`, `meta.slotsParked` and the ingest report all "
               "carry it already. Only the Scan now button is a write, and "
               "that is `scan`, which is in ALLOWED rather than here.",
        "classes": {"scanbar", "scanmsg", "sysbanner", "capacity"},
    },
    "the board's Found actions row, below the filters": {
        "state": "architectural",
        "why": "**not unbuilt: removed, and the row with it.** The board keeps "
               "Save search and a second Clear in a row below the filters. v2 "
               "saves the filters when they change rather than when somebody "
               "presses a button, and its one reset control sits in the filter "
               "row beside the thing it resets, so a reader counting Clear "
               "buttons finds one rather than two. With both controls gone the "
               "row has nothing to hold, and a container rendered around "
               "nothing is not a gap.",
        "classes": {"jb-actions"},
    },
    "the board's single-document navigation": {
        "state": "architectural",
        "why": "`.settings-page` and `.profile-page` are `display:none` until "
               "a class lands on `<body>`, because the board is one document "
               "whose panels toggle. v2 serves them as routes, so nothing "
               "sets those classes and nothing should. This is the 2026-08-30 "
               "finding from the other side: the same CSS that rendered a "
               "blank page there is correct to be inert here.",
        "classes": {"settings-view", "profile-view", "back-board",
                    "settings-link", "nav-n", "ui-separator"},
    },
    "board-only affordances with no v2 equivalent decided yet": {
        "state": "buildable",
        "why": "a long tail, each one small, none of them a write. Kept as "
               "one group rather than seven because splitting them would "
               "imply a sequencing decision nobody has made.",
        "classes": {"ago", "applymsg", "aside", "chip-sm", "contact",
                    "direct", "dropping", "err", "fault-note",
                    "jb-decidefail", "jb-linkmsg", "jb-out", "jb-pill",
                    "jb-vt", "jb-wide", "msg", "note", "ok", "pill", "stub",
                    "undo", "why"},
    },
    "states whose base class v2 does render, already carried by ALLOWED with "
    "their own reasons; listed here too because the inverted check sees them "
    "from the other side": {
        "state": "buildable",
        "why": "each has an entry in ALLOWED saying where the behaviour "
               "actually lives. They are here only so the two checks agree "
               "about what exists.",
        "classes": {"done", "fault", "gate", "landed", "lost", "marked",
                    "ref", "scan", "wide"},
    },
}

CLASS = re.compile(r"\.(-?[A-Za-z_][\w-]*)")
TOKEN = re.compile(r"[a-zA-Z][\w-]*")


def mentioned_names():
    """Every identifier-shaped token anywhere under `apps/web/app`.

    Deliberately the whole file rather than string literals: the mascot's
    classes live inside an SVG in a generated module with escaped quotes, and a
    literal-only reader reported `jm2-contour` as unreachable when it is right
    there. This set answers one question only, and answers it conservatively --
    **if a name appears nowhere in v2's source, v2 certainly does not set it.**
    """
    out = set()
    for path in sorted(APP.rglob("*.ts*")):
        if "node_modules" in str(path):
            continue
        out |= set(TOKEN.findall(path.read_text(encoding="utf-8")))
    return out


def _attribute_values(src):
    r"""Every literal assigned to a class attribute or property in one file.

    **The value is read with a balanced-brace scan, not a regex.** The previous
    reader was `class(?:Name)?=(\{[^}]*\}|"[^"]*")`, and `[^}]*` stops at the
    first `}` in the expression -- which in
    `className={kind ? \`jb-chip ${kind}\` : "jb-chip"}` is the one closing the
    interpolation. It captured a fragment ending mid-template, found no
    complete backtick pair inside it, and returned nothing, so `jb-chip` read
    as a class v2 never renders. Measured on 2026-09-03: that cost 8 rendered
    classes, and it is wrong in both directions at once -- a class wrongly
    absent from this set is a false finding in the forward check and a false
    pass in the inverted one.

    Four shapes, because v2 uses all four:

        className="a b"                 JSX literal
        className={x ? `a ${k}` : "a"}  JSX expression, braces balanced here
        btn.className = 'jb-select-btn' property assignment, spaces, single
                                        quotes, inside a generated script
        class=\"a b\"                    escaped HTML in a generated string

    The third is not hypothetical: the whole `jb-select-*` family is set that
    way inside `chrome.generated.ts`, and the old reader missed every one.
    """
    out = []
    for match in re.finditer(r"\bclass(?:Name)?\s*=\s*", src):
        i = match.end()
        if i >= len(src):
            continue
        ch = src[i]
        if ch in "\"'`":
            end = src.find(ch, i + 1)
            if end > 0:
                out.append(src[i + 1:end])
        elif ch == "\\" and src[i:i + 2] == '\\"':
            end = src.find('\\"', i + 2)
            if end > 0:
                out.append(src[i + 2:end])
        elif ch == "{":
            depth, j = 0, i
            while j < len(src):
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            body = src[i:j + 1]
            out += re.findall(r'"([^"\n]*)"', body)
            out += re.findall(r"'([^'\n]*)'", body)
            out += re.findall(r"`([^`]*)`", body)
    # `classList.add("a", "b")` and `setAttribute("class", "a b")`.
    for match in re.finditer(
            r'classList\.(?:add|remove|toggle)\s*\(([^)]*)\)', src):
        out += re.findall(r'["\']([^"\']+)["\']', match.group(1))
    for match in re.finditer(
            r'setAttribute\s*\(\s*["\']class["\']\s*,\s*["\']([^"\']*)', src):
        out.append(match.group(1))
    return out


def rendered_classes():
    """Classes v2 demonstrably puts on an element.

    Narrow where `mentioned_names` is broad, and the pair is the point: a rule
    is only interesting when v2 *renders* one of its classes, and "the word
    appears somewhere" is not that. Using the broad set for both halves made
    `.jb-linkmsg.ok` a finding because `ok` occurs in a comment.

    Verified against the live DOM rather than by reading: every class on the
    rendered Found lane is in this set. See
    `test_the_extractor_finds_the_classes_the_page_actually_renders`.
    """
    out = set()
    for path in sorted(APP.rglob("*.ts*")):
        if "node_modules" in str(path):
            continue
        for value in _attribute_values(path.read_text(encoding="utf-8")):
            # An interpolation is a hole, not a class. `dynamic_bases` is what
            # keeps rules about such elements out of the findings.
            value = re.sub(r"\$\{[^}]*\}", " ", value)
            out |= {t for t in re.split(r"\s+", value) if TOKEN.fullmatch(t)}
    return out


def dynamic_bases():
    """Base classes on an element whose class list is partly computed.

    **A class name built from data cannot be enumerated, and pretending
    otherwise is a false positive generator.** `className={`jb-chip ${kind}`}`
    renders `.jb-chip.years`, `.jb-chip.pay`, `.jb-chip.place` and three more,
    and no reader of the source can know that: it sees `jb-chip` and a hole.
    The honest response is to stop asking about elements whose classes are
    computed, rather than to list the six values here and have the list rot.

    It costs coverage and the cost is bounded: it exempts only rules whose
    subject shares a class with such an element. The three instances this file
    exists for are all plain string literals -- `"posted fresh"`,
    `"yrs over"`, `classList.add("loaded")` -- so none of them hides here.
    """
    css = CSS.read_text(encoding="utf-8")
    known = set(CLASS.findall(re.sub(r"/\*.*?\*/", " ", css, flags=re.S)))
    out = set()
    for path in sorted(APP.rglob("*.ts*")):
        if "node_modules" in str(path):
            continue
        src = path.read_text(encoding="utf-8")
        # Every template literal carrying an interpolation. Scanned over the
        # whole file rather than inside `className=`, because a `${...}` holds
        # a closing brace and any regex that captures the attribute first stops
        # in the middle of it -- which is why the first version of this
        # function matched nothing at all and changed no finding.
        for literal in re.findall(r"`([^`]*)`", src):
            if "${" not in literal:
                continue
            for token in re.split(r"\s+", re.sub(r"\$\{[^}]*\}", " ", literal)):
                if TOKEN.fullmatch(token or "") and token in known:
                    out.add(token)
    return out



def styled_class_names():
    """Every class `board.css` styles, comments removed."""
    css = re.sub(r"/\*.*?\*/", " ", CSS.read_text(encoding="utf-8"), flags=re.S)
    return set(CLASS.findall(css))


def seen_classes():
    """Everything v2 is known to render: read from source, plus observed.

    The union is the honest answer. Static reading misses what a template
    interpolation builds; the observed set misses whatever that one page state
    did not draw. Neither alone is complete and the check below would be wrong
    in a different direction with either.
    """
    return rendered_classes() | OBSERVED_ON_THE_PAGE


def unrendered_baseline():
    return set().union(*(g["classes"] for g in UNRENDERED.values()))


def unrendered_by_state(state):
    return set().union(*(g["classes"] for g in UNRENDERED.values()
                         if g["state"] == state), set())


def styled_rules():
    """Every rule, as `(subject classes, other classes, subject tag, example)`.

    **The subject is the last compound, and that distinction is the whole
    check.** A selector names one element to style and any number of ancestors
    to qualify it, and only the subject's classes are a *state* the markup has
    to be able to reach. Without the split this flags every container rule --
    `.wording-body .split-note` reads as "v2 renders split-note and never sets
    wording-body", which is true, unhelpful, and five of the eleven findings
    the first version produced.

    `.company-mark img.loaded` is the shape that matters and it survives the
    split: the subject is `img.loaded`, `loaded` is the state, and
    `.company-mark` upstream is what proves v2 renders this markup at all.
    """
    css = CSS.read_text(encoding="utf-8")
    # Comments first: a class named only in prose is not a rule.
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    out = []
    for selector in re.findall(r"([^{}]+)\{", css):
        for part in selector.split(","):
            part = part.strip()
            if not part or part.startswith("@"):
                continue
            # `:hover`, `::before` and friends are states the browser sets.
            bare = re.sub(r"::?[\w-]+(\([^)]*\))?", "", part)
            compounds = [c for c in re.split(r"[\s>+~]+", bare) if c.strip()]
            if not compounds:
                continue
            last = compounds[-1]
            subject = set(CLASS.findall(last))
            others = set(CLASS.findall(" ".join(compounds[:-1])))
            tag = re.match(r"^([a-zA-Z][\w-]*)", last)
            if subject:
                out.append((subject, others, tag.group(1) if tag else "",
                            " ".join(part.split())[:70]))
    return out


def _scan(rendered, dynamic, allowed):
    """Every (state, example) this configuration would report."""
    out = []
    for subject, others, tag, example in styled_rules():
        missing = sorted(subject - rendered)
        if not missing:
            continue
        if not (bool(tag) or bool((subject - set(missing)) & rendered)):
            continue
        if not ((subject | others) & rendered):
            continue
        if subject & dynamic:
            continue
        for state in missing:
            if state in allowed:
                continue
            out.append((state, example))
    return out


def _new_findings(case, dropped):
    """What deleting one allowance would surface."""
    rendered, dynamic = rendered_classes(), dynamic_bases()
    without = {k: v for k, v in ALLOWED.items() if k != dropped}
    return [f for f in _scan(rendered, dynamic, without)
            if f not in _scan(rendered, dynamic, ALLOWED)]


class EveryStyledStateIsReachable(unittest.TestCase):
    def findings(self, allowed=ALLOWED):
        """Every rule v2 renders markup for and cannot activate."""
        rendered, dynamic = rendered_classes(), dynamic_bases()
        return _scan(rendered, dynamic, allowed)

    def test_no_class_v2_emits_is_styled_only_in_a_state_it_cannot_set(self):
        rendered, dynamic = rendered_classes(), dynamic_bases()
        findings = []
        for subject, others, tag, example in styled_rules():
            # **`rendered_classes`, not `mentioned_names`, and this is the
            # whole check.** The first version asked whether the name appeared
            # anywhere in v2's source, which is conservative in the wrong
            # direction: `fresh`, `loaded` and `over` are ordinary English and
            # occur in the very comments explaining them. Mutation-tested
            # against all three historical instances, that version passed every
            # one -- a check for "shipped a class it cannot activate" that
            # could not detect the three cases it was written from.
            missing = sorted(subject - rendered)
            if not missing:
                continue
            # **The element has to exist in v2 for a missing state to matter.**
            # A subject that is class-only and whose class v2 never renders is
            # simply a component v2 does not have -- `.primary-nav .nav-n` is
            # the nav count badge, not a state anyone forgot. A subject with a
            # tag, or with a second class v2 does render, is markup that is on
            # the page wearing a treatment it can never receive.
            element_exists = bool(tag) or bool((subject - set(missing)) & rendered)
            if not element_exists:
                continue
            if not ((subject | others) & rendered):
                continue
            # This element's class list is computed, so what it carries at
            # runtime is not knowable from the source. See `dynamic_bases`.
            if subject & dynamic:
                continue
            for state in missing:
                if state in ALLOWED:
                    continue
                findings.append(f"  `{state}` is never set by v2, and this "
                                f"rule styles markup it renders: {example}")
        self.assertEqual(sorted(set(findings)), [],
                         "\n" + "\n".join(sorted(set(findings)))
                         + "\n\nEither set the state, or add it to ALLOWED "
                           "with the reason it lives elsewhere.")


    def test_the_extractor_finds_what_the_page_actually_renders(self):
        """The control on `rendered_classes`, and it has already failed once.

        Every class observed on the rendered page must either be found in the
        source or be one a template interpolation builds. The reader this
        replaced captured `className={...}` with `\\{[^}]*\\}`, which stops at
        the `}` closing a `${...}`, and it silently lost `jb-chip` and the
        whole `jb-select-*` family. That is wrong in both directions at once:
        a class missing from this set is a false finding in the forward check
        and a false pass in the inverted one, in the same run.
        """
        interpolated = {"place", "years", "pay", "unknown", "boards"}
        missed = sorted(OBSERVED_ON_THE_PAGE - rendered_classes() - interpolated)
        self.assertEqual(missed, [],
                         "these classes are on the rendered page and the "
                         "source reader cannot find them: %s" % missed)

    def test_no_class_is_styled_and_rendered_by_nothing(self):
        """The inverted check: start from the stylesheet, not from the markup.

        **The forward check cannot see this and never could.** It starts from
        classes v2 emits and asks whether each can reach its styled state, so a
        class v2 never emits is outside its subject by construction. Item 22
        shipped its entire stylesheet with no markup and no route, and this
        file passed on it for a day.

        What it reports is the measured gap between the two boards. New entries
        fail; the baseline is a to-do list that only shrinks.
        """
        extra = sorted(styled_class_names() - seen_classes() - unrendered_baseline())
        self.assertEqual(extra, [],
                         "\nboard.css styles these and nothing in v2 renders "
                         "them:\n  " + "\n  ".join(extra) +
                         "\n\nEither render them, or add each to UNRENDERED "
                         "under the feature it belongs to.")

    def test_the_unrendered_list_only_shrinks(self):
        """A baseline entry that is now rendered has to be deleted.

        Without this the list is a graveyard: work lands, the class starts
        being rendered, and the entry stays, so the next reader takes a
        finished feature for an outstanding one. Same ratchet as
        `population.py` and for the same reason.
        """
        landed = sorted(unrendered_baseline() & seen_classes())
        self.assertEqual(landed, [],
                         "v2 now renders these, so they are no longer a gap "
                         "and must come out of UNRENDERED: %s" % landed)

    def test_every_group_says_what_it_is_and_what_it_waits_on(self):
        """A group with no state is an inventory entry, not a sequence entry.

        The list is only worth keeping if it can be sequenced from, and that
        needs each group to say whether the write gate is what is stopping it.
        `architectural` is the one that has to be declared rather than
        inferred: those classes will never be rendered, so counting them as
        outstanding work overstates the remainder for as long as the file
        exists.
        """
        for label, group in UNRENDERED.items():
            self.assertGreater(len(label), 30,
                               "a group needs a label that says what feature "
                               "it is: %r" % (label,))
            self.assertIn(group["state"], STATES,
                          "%r declares state %r, which is not one of %s"
                          % (label, group.get("state"), list(STATES)))
            self.assertGreater(len(group.get("why", "")), 60,
                               "%r has no reason long enough to check" % label)
            self.assertTrue(group["classes"], "%r groups nothing" % label)

    def test_the_gated_group_is_the_one_that_writes(self):
        """The `gated` claim is checkable and it is checked.

        A group is gated when its controls save something, and the sharpest
        evidence available here is that its classes belong to Profile's write
        half -- the answer bank and the wording editor, both of which parity
        compares. Anything else claiming to be gated is a claim nobody has
        tested, and a wrong `gated` is worse than a wrong `buildable`: it
        parks work behind a gate that was never in its way.
        """
        gated = unrendered_by_state("gated")
        stray = sorted(c for c in gated
                       if not (c.startswith("bank-") or c.startswith("wording-")
                               or c == "resume-frame"))
        self.assertEqual(stray, [],
                         "these are marked gated and are not part of a write "
                         "surface anyone has named: %s" % stray)

    def test_every_allowance_carries_a_reason(self):
        for key, why in ALLOWED.items():
            self.assertTrue(why and why.strip(),
                            f"{key} is allowed with no reason")
            self.assertGreater(len(why), 40,
                               f"{key}'s reason is too short to check")

    def test_no_allowance_is_stale(self):
        """An allowance that suppresses nothing is a stale exemption, and a
        stale exemption is how the next real one gets waved through.

        **Asked by removing it and looking, not by checking whether the name
        appears.** The name is the wrong question for the same reason it was
        the wrong question in the main check: `open` styles both `.jb-card`
        and `.bank-row`, so the moment Profile started rendering
        `bank-row open` a name-based test called the allowance stale while
        `.jb-card.open` was still unreachable. An entry earns its place if
        deleting it produces a finding.
        """
        stale = sorted(name for name in ALLOWED
                       if not _new_findings(self, name))
        self.assertEqual(stale, [],
                         f"these allowances suppress nothing any more and "
                         f"should be deleted: {stale}")
    def test_the_scan_actually_reads_the_stylesheet(self):
        """The control. A regex that quietly stops matching reports success."""
        rules = styled_rules()
        self.assertGreater(len(rules), 100,
                           f"only {len(rules)} multi-class rules found in "
                           f"board.css; the selector scan has stopped working")
        self.assertGreater(len(mentioned_names()), 200,
                           "the emitted-class scan found almost nothing")
