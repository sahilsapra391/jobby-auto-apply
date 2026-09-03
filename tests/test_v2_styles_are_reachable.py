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


def rendered_classes():
    """Classes v2 demonstrably puts on an element.

    Narrow where `mentioned_names` is broad, and the pair is the point: a rule
    is only interesting when v2 *renders* one of its classes, and "the word
    appears somewhere" is not that. Using the broad set for both halves made
    `.jb-linkmsg.ok` a finding because `ok` occurs in a comment.
    """
    out = set()
    for path in sorted(APP.rglob("*.ts*")):
        if "node_modules" in str(path):
            continue
        src = path.read_text(encoding="utf-8")
        # `className="a b"`, `className={cond ? "a b" : "a"}`, and the
        # generated markup's own `class="..."`.
        for match in re.finditer(r'class(?:Name)?=(\{[^}]*\}|"[^"]*")', src):
            for literal in re.findall(r'"([^"\n]*)"|`([^`]*)`', match.group(1)):
                for chunk in literal:
                    out |= {t for t in re.split(r"\s+", chunk) if TOKEN.fullmatch(t)}
        for match in re.finditer(
                r'classList\.(?:add|remove|toggle)\(\s*"([^"]+)"', src):
            out.add(match.group(1))
        # The generated board markup arrives as escaped HTML in a string.
        for match in re.finditer(r'class=\\"([^"\\]*)\\"', src):
            out |= {t for t in re.split(r"\s+", match.group(1)) if TOKEN.fullmatch(t)}
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


if __name__ == "__main__":
    unittest.main()
