#!/usr/bin/env python3
"""Build a tailored resume from a fixed template plus only the parts that change.

Two tailored resumes are 63% identical, and the 3KB stylesheet is the same in
every one. The agent was writing all 15.6KB from scratch each run: one Write
call that took a hundred seconds of generation, then edits on top of it. That is
the single largest block of time in the build phase.

It is also where the resume can go wrong. The employers, dates and job titles
are supposed to be frozen while the wording adapts, and "supposed to" means a
verification step that reads the finished document and hopes to catch drift.
Here the frozen parts are the template, so they cannot drift: the only things a
caller can set are the ones that are meant to vary.

    python3 scripts/resume_build.py extract --html applications/X/.resume.html \\
        --out-template templates/resume.html --out-content /tmp/content.json
    python3 scripts/resume_build.py render --template templates/resume.html \\
        --content /tmp/content.json --out applications/Y/.resume.html
    python3 scripts/resume_build.py slots --template templates/resume.html

Content is JSON:

    {"banner": "...", "summary": "...",
     "areasOfExpertise": ["...", ...],
     "technical": [{"label": "...", "items": "..."}, ...],
     "roles": [{"lede": "...", "bullets": ["...", ...]}, ...]}

Every list must be exactly as long as the template's. A resume that grows a line
reflows the page, and the layout check compares against the base, so a silently
shorter list would fail there instead of here with a worse message.

Standard library only.
"""

import argparse
import difflib
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BANNER = re.compile(r'(<div class="banner"[^>]*>)(.*?)(</div>)', re.S)
SUMMARY = re.compile(r'(<div class="body summary"[^>]*>)(.*?)(</div>)', re.S)
AOE = re.compile(r'(<div class="aoe"[^>]*>\s*<span class="b">&bull;</span>&nbsp;&nbsp;)(.*?)(</div>)', re.S)
TECH = re.compile(r'(<div class="tech"[^>]*><b>)(.*?)(:</b>)(.*?)(</div>)', re.S)
LEDE = re.compile(r'(<div class="body lede"[^>]*>)(.*?)(</div>)', re.S)
LIST = re.compile(r'(<ul>)(.*?)(</ul>)', re.S)
ITEM = re.compile(r'<li>(.*?)</li>', re.S)

# A bare & that is not already an entity. Agent-authored text says "R&D" and
# means it literally; the template's own text says &amp; and means the same
# thing, and re-escaping that would turn it into &amp;amp; on the page.
BARE_AMP = re.compile(r"&(?!#?\w+;)")
WORD = re.compile(r"[a-z0-9]+(?:[.+#/-][a-z0-9]+)*", re.I)
FACT = re.compile(
    r"(?<![A-Za-z0-9])(?:[$]\s*)?(?:~\s*)?\d+(?:[.,]\d+)*(?:\s*%|\+)?"
    r"|\bV\d+(?:/V\d+)*\b",
    re.I,
)
STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
    "of", "on", "or", "the", "to", "through", "using", "with",
}


class ResumeIntegrityError(ValueError):
    """Tailored wording changed the resume's facts or structural meaning."""


def _plain(value):
    return html.unescape(str(value or "")).replace("&ndash;", "-")


def _normalized(value):
    return " ".join(WORD.findall(_plain(value).lower()))


def _content_tokens(value):
    return [token for token in WORD.findall(_plain(value).lower())
            if token not in STOPWORDS]


# A word typed twice in a row is a generation artifact, never prose.
# Measured 2026-08-28 across all 79 rendered documents on this machine, the
# only adjacent duplicated alphabetic word any provider ever produced was
# "Dear Dear", sixteen times, in letters real employers received. Digits are
# excluded on the same measurement: figures repeat legitimately, 264 times.
# cover_letter.py imports this so both documents answer to one rule; it is
# deliberately provider-blind, sitting in the trusted pipeline every model's
# output must pass.
ADJACENT_WORD_DUP = re.compile(r"\b([A-Za-z][A-Za-z'-]*)\s+\1\b", re.I)


def doubled_word(text):
    """The word this text repeats back to back, or None."""
    found = ADJACENT_WORD_DUP.search(str(text or ""))
    return found.group(1) if found else None


def _text_fields(content, path=""):
    """Every human-readable string in the content, named for its place."""
    if isinstance(content, str):
        yield path, content
    elif isinstance(content, list):
        for index, item in enumerate(content):
            yield from _text_fields(item, f"{path}[{index + 1}]")
    elif isinstance(content, dict):
        for key, item in content.items():
            yield from _text_fields(item, f"{path}.{key}" if path else str(key))


def _duplicate_bullets(content):
    bullets = [
        (role_index, bullet_index, str(bullet))
        for role_index, role in enumerate(content.get("roles") or [])
        for bullet_index, bullet in enumerate((role or {}).get("bullets") or [])
    ]
    for left_index, left in enumerate(bullets):
        left_text = _normalized(left[2])
        left_words = set(_content_tokens(left[2]))
        for right in bullets[left_index + 1:]:
            right_text = _normalized(right[2])
            right_words = set(_content_tokens(right[2]))
            if not left_text or not right_text:
                continue
            sequence = difflib.SequenceMatcher(None, left_text, right_text).ratio()
            union = left_words | right_words
            overlap = len(left_words & right_words) / len(union) if union else 0.0
            if left_text == right_text or (sequence >= 0.80 and overlap >= 0.70):
                yield left[:2], right[:2]


def assert_integrity(base, tailored):
    """Reject structural drift, duplicate points, and unsupported rewrites.

    A changed bullet must retain its source facts and most of its meaningful
    vocabulary. That leaves room to weave in posting terms, while preventing a
    model from replacing the accomplishment with a different story.
    """
    base_roles = base.get("roles") or []
    roles = tailored.get("roles") or []
    if len(roles) != len(base_roles):
        raise ResumeIntegrityError("experience entry count changed")

    for role_index, (source_role, result_role) in enumerate(zip(base_roles, roles)):
        source_bullets = (source_role or {}).get("bullets") or []
        result_bullets = (result_role or {}).get("bullets") or []
        if len(result_bullets) != len(source_bullets):
            raise ResumeIntegrityError(
                f"experience entry {role_index + 1} changed bullet count"
            )
        for bullet_index, (source, result) in enumerate(
            zip(source_bullets, result_bullets)
        ):
            if _normalized(source) == _normalized(result):
                continue
            source_facts = Counter(match.group(0).replace(" ", "").lower()
                                   for match in FACT.finditer(_plain(source)))
            result_facts = Counter(match.group(0).replace(" ", "").lower()
                                   for match in FACT.finditer(_plain(result)))
            if source_facts != result_facts:
                raise ResumeIntegrityError(
                    f"experience entry {role_index + 1}, bullet {bullet_index + 1} "
                    "changed a number, version, or metric"
                )
            source_tokens = set(_content_tokens(source))
            result_tokens = set(_content_tokens(result))
            retained = (
                len(source_tokens & result_tokens) / len(source_tokens)
                if source_tokens else 1.0
            )
            if retained < 0.50:
                raise ResumeIntegrityError(
                    f"experience entry {role_index + 1}, bullet {bullet_index + 1} "
                    "changed too much of the source accomplishment"
                )
            if len(_plain(result)) > max(len(_plain(source)) + 24,
                                         int(len(_plain(source)) * 1.10)):
                raise ResumeIntegrityError(
                    f"experience entry {role_index + 1}, bullet {bullet_index + 1} "
                    "grew beyond a keyword-level rewrite"
                )

    duplicate = next(_duplicate_bullets(tailored), None)
    if duplicate:
        left, right = duplicate
        raise ResumeIntegrityError(
            "duplicate resume points at experience entries "
            f"{left[0] + 1}.{left[1] + 1} and {right[0] + 1}.{right[1] + 1}"
        )

    for where, text in _text_fields(tailored):
        word = doubled_word(text)
        if word:
            raise ResumeIntegrityError(
                f"{where} repeats the word {word!r} back to back"
            )

    # A list row printed twice is a duplicated line on the page. This was not
    # hypothetical: the shipped template itself carried the technical row
    # "Languages & Frameworks" twice, and every one of the ten newest rendered
    # resumes on 2026-08-28 showed that line doubled. Employers received them.
    for field in ("areasOfExpertise", "technical"):
        seen = {}
        for index, row in enumerate(tailored.get(field) or []):
            key = _normalized(json.dumps(row, sort_keys=True)
                              if isinstance(row, dict) else str(row))
            if key and key in seen:
                raise ResumeIntegrityError(
                    f"{field} rows {seen[key] + 1} and {index + 1} are the "
                    "same line twice"
                )
            seen[key] = index


def safe(text):
    """Text to HTML, leaving entities that are already correct alone."""
    text = str(text)
    text = BARE_AMP.sub("&amp;", text)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def extract(html):
    """Split a finished resume into its template and its content."""
    content = {"banner": "", "summary": "", "areasOfExpertise": [],
               "technical": [], "roles": []}
    template = html

    def take_one(pattern, key, groups=(1, 2, 3)):
        nonlocal template
        found = pattern.search(template)
        if not found:
            return
        content[key] = found.group(groups[1]).strip()
        template = template[:found.start()] + \
            found.group(groups[0]) + "{{%s}}" % key + found.group(groups[2]) + \
            template[found.end():]

    take_one(BANNER, "banner")
    take_one(SUMMARY, "summary")

    def take_many(pattern, build, slot):
        nonlocal template
        out, index = [], 0

        def swap(m):
            nonlocal index
            out.append(build(m))
            token = "{{%s%d}}" % (slot, index)
            index += 1
            if slot == "aoe":
                return m.group(1) + token + m.group(3)
            if slot == "tech":
                return m.group(1) + token + ":</b> " + "{{techitems%d}}" % (index - 1) + m.group(5)
            return m.group(1) + token + m.group(3)

        template = pattern.sub(swap, template)
        return out

    content["areasOfExpertise"] = take_many(AOE, lambda m: m.group(2).strip(), "aoe")
    content["technical"] = take_many(
        TECH, lambda m: {"label": m.group(2).strip(), "items": m.group(4).strip()}, "tech")
    ledes = take_many(LEDE, lambda m: m.group(2).strip(), "lede")
    bullets = take_many(
        LIST, lambda m: [i.strip() for i in ITEM.findall(m.group(2))], "bullets")

    # A role without a lede still has bullets, and the other way round. Pairing
    # by position across the two lists is what the document itself does.
    count = max(len(ledes), len(bullets))
    content["roles"] = [
        {"lede": ledes[i] if i < len(ledes) else "",
         "bullets": bullets[i] if i < len(bullets) else []}
        for i in range(count)
    ]
    return template, content


def redact(value):
    """Enough of a value to tell two apart, never enough to use.

    Verification output gets pasted into commit messages, issues and chat. The
    39 screenshots removed from this repository were verification artifacts too:
    proof that a form had been filled correctly, carrying the contents of the
    form. A check that prints the phone number to prove the phone number is
    right has the same shape and one step to the same outcome. Digits go, the
    structure stays, and a hash carries the actual comparison.
    """
    text = str(value or "")
    out = []
    for ch in text:
        if ch.isdigit():
            out.append("#")
        elif ch.isalpha():
            out.append(ch if len(out) < 2 or not out[-1].isalpha() else "\u00b7")
        else:
            out.append(ch)
    return "".join(out)


def fingerprint(value):
    import hashlib
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:12]


def identity_block(region=None, root=None):
    """The name and contact line, read from the profile at render time.

    Not stored in the template. The template is tracked and the contact line is
    a home address, a phone number and a personal email; keeping it there put
    all three in the repository, which is how they end up in every clone. It is
    also wrong per posting: the identity block is chosen by the posting's
    country, and a template can only hold one of them.
    """
    import json as _json
    import subprocess as _sub
    import sys as _sys
    args = [_sys.executable, str(REPO / "scripts" / "store.py"), "profile-get"]
    if root:
        args = args[:2] + ["--root", str(root)] + args[2:]
    out = _sub.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("resume_build: cannot read the profile for the contact block")
    profile = _json.loads(out.stdout or "{}")

    resumes = profile.get("resumes") or []
    # Selection moved into the store so one rule serves the renderer, the board
    # and anything later, rather than each growing its own idea of which base a
    # posting gets. It reads `selector` when present and `appliesTo` when not,
    # so a profile written before selectors existed behaves exactly as it did.
    try:
        import store as _store
        chosen, _why = _store.select_resume(resumes, region)
    except ImportError:
        chosen = None
    if chosen is None:
        want = (region or "").upper()
        for entry in resumes:
            if want and want in [str(a).upper() for a in (entry.get("appliesTo") or [])]:
                chosen = entry
                break
    if chosen is None:
        chosen = next((e for e in resumes if e.get("default")), resumes[0] if resumes else {})
    identity = chosen.get("identity") or {}

    name = " ".join(x for x in (profile.get("firstName"), profile.get("lastName")) if x)
    links = profile.get("links") or {}
    parts = []
    if identity.get("location"):
        parts.append(safe(identity["location"]))
    if identity.get("phone"):
        # The profile stores digits only, because that is what form fields want
        # (phoneNote says so). A resume shows it the way a reader expects.
        digits = "".join(c for c in str(identity["phone"]) if c.isdigit())
        if len(digits) == 10:
            shown = "(%s) %s-%s" % (digits[:3], digits[3:6], digits[6:])
        elif len(digits) == 11 and digits[0] == "1":
            shown = "(%s) %s-%s" % (digits[1:4], digits[4:7], digits[7:])
        else:
            shown = str(identity["phone"])
        parts.append(safe(shown))
    email = profile.get("email")
    if email:
        parts.append('<a href="mailto:%s">%s</a>' % (safe(email), safe(email)))
    site = links.get("website")
    if site:
        parts.append('<a href="%s">%s</a>'
                     % (safe(site), safe(site.rstrip("/").split("//")[-1])))
    if links.get("linkedin"):
        parts.append('<a href="%s">LinkedIn</a>' % safe(links["linkedin"]))
    if links.get("github"):
        parts.append('<a href="%s">GitHub</a>' % safe(links["github"]))
    return name.upper(), " | ".join(parts)


def merge(base, overrides):
    """Base content with this posting's changes laid over it.

    64% of the bullets across the resumes already written appear in more than
    one of them, because they describe the same real work. Making the caller
    restate all of it to change a third of it is most of what it spends its time
    generating, so anything it does not mention is inherited.

    Roles merge by position. Within a role, `bullets` may be a full list, or a
    map of index to replacement for changing one line and keeping the rest:

        {"roles": [{"bullets": {"2": "reworded third bullet"}}]}
    """
    out = json.loads(json.dumps(base))          # never mutate the caller's base
    for key in ("banner", "summary", "areasOfExpertise", "technical"):
        if overrides.get(key) is not None:
            out[key] = overrides[key]

    roles = out.get("roles") or []
    for i, patch in enumerate(overrides.get("roles") or []):
        if patch is None:
            continue
        if i >= len(roles):
            raise SystemExit(
                f"resume_build: the base has {len(roles)} experience entries and the "
                f"overrides reach entry {i + 1}. Entries are frozen; only their wording "
                f"changes.")
        if patch.get("lede") is not None:
            roles[i]["lede"] = patch["lede"]
        bullets = patch.get("bullets")
        if isinstance(bullets, list):
            roles[i]["bullets"] = bullets
        elif isinstance(bullets, dict):
            current = list(roles[i].get("bullets") or [])
            for index, text in bullets.items():
                spot = int(index)
                if spot >= len(current):
                    raise SystemExit(
                        f"resume_build: entry {i + 1} has {len(current)} bullets and the "
                        f"overrides replace number {spot + 1}. Use a full list to add one.")
                current[spot] = text
            roles[i]["bullets"] = current
    return out


def render(template, content):
    """Put content back into the template, refusing anything that does not fit."""
    def need(key, wanted, got):
        if wanted != got:
            raise SystemExit(
                f"resume_build: the template has {wanted} {key} and the content has "
                f"{got}. The layout is measured against the base resume, so a list "
                f"of a different length reflows the page. Supply exactly {wanted}.")

    out = template
    if "{{name}}" in out or "{{contact}}" in out:
        name, contact = identity_block(content.get("region"), content.get("storeRoot"))
        out = out.replace("{{name}}", safe(name)).replace("{{contact}}", contact)
    out = out.replace("{{banner}}", safe(content.get("banner", "")))
    out = out.replace("{{summary}}", safe(content.get("summary", "")))

    aoe = content.get("areasOfExpertise") or []
    need("areas of expertise", len(re.findall(r"\{\{aoe\d+\}\}", template)), len(aoe))
    for i, value in enumerate(aoe):
        out = out.replace("{{aoe%d}}" % i, safe(value))

    tech = content.get("technical") or []
    if "{{technical}}" in out:
        # Variable length on purpose. Regrouping Technical Proficiencies under
        # the posting's own names is part of the tailoring, and the resumes
        # written by hand used six, seven and eight rows depending on the
        # posting. Fixing the count would have quietly removed a row's worth of
        # that range. The layout gate still measures the result against the base,
        # so a section that grows too tall is caught there rather than here.
        rendered = "\n  ".join(
            '<div class="tech"><b>%s:</b> %s</div>'
            % (safe(row.get("label", "")), safe(row.get("items", "")))
            for row in tech)
        out = out.replace("{{technical}}", rendered)
    else:
        need("technical rows", len(re.findall(r"\{\{tech\d+\}\}", template)), len(tech))
        for i, row in enumerate(tech):
            out = out.replace("{{tech%d}}" % i, safe(row.get("label", "")))
            out = out.replace("{{techitems%d}}" % i, safe(row.get("items", "")))

    roles = content.get("roles") or []
    # Ledes and bullet lists are counted separately and the larger wins. An
    # entry under "Additional Experience" carries bullets with no lede above
    # them, so the two counts genuinely differ, and validating against the lede
    # count alone rejected every real resume.
    lede_slots = len(re.findall(r"\{\{lede\d+\}\}", template))
    bullet_slots = len(re.findall(r"\{\{bullets\d+\}\}", template))
    need("experience entries", max(lede_slots, bullet_slots), len(roles))
    for i, role in enumerate(roles):
        out = out.replace("{{lede%d}}" % i, safe(role.get("lede", "")))
        items = "".join("\n    <li>%s</li>" % safe(b) for b in (role.get("bullets") or []))
        out = out.replace("{{bullets%d}}" % i, items + "\n  ")

    left = re.findall(r"\{\{(\w+)\}\}", out)
    if left:
        raise SystemExit(f"resume_build: nothing supplied for {sorted(set(left))}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="split a finished resume into template + content")
    e.add_argument("--html", required=True)
    e.add_argument("--out-template", required=True)
    e.add_argument("--out-content", required=True)

    r = sub.add_parser("render", help="template + content -> resume HTML")
    r.add_argument("--template", required=True)
    r.add_argument("--content", required=True,
                   help="this posting's content, or only what it changes when "
                        "--base-content is given")
    r.add_argument("--base-content", help="content to inherit anything not mentioned")
    r.add_argument("--out", required=True)

    s = sub.add_parser("slots", help="what this template expects")
    s.add_argument("--template", required=True)

    k = sub.add_parser("check", help="does the rendered identity match the profile, "
                                     "without printing it")
    k.add_argument("--template", required=True)
    k.add_argument("--region", help="the posting's country, e.g. US or IN")

    args = ap.parse_args()

    if args.cmd == "extract":
        template, content = extract(Path(args.html).expanduser().read_text())
        Path(args.out_template).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_template).expanduser().write_text(template)
        Path(args.out_content).expanduser().write_text(json.dumps(content, indent=2))
        print(json.dumps({"template": args.out_template, "content": args.out_content,
                          "areasOfExpertise": len(content["areasOfExpertise"]),
                          "technical": len(content["technical"]),
                          "roles": len(content["roles"])}, indent=2))
        return

    if args.cmd == "check":
        template = Path(args.template).expanduser().read_text()
        name, contact = identity_block(args.region)
        rendered = template.replace("{{name}}", safe(name)).replace("{{contact}}", contact)
        block = ""
        if '<div class="contact">' in rendered:
            block = rendered.split('<div class="contact">')[1].split("</div>")[0]
        print(json.dumps({
            "region": args.region or "(default)",
            "name": redact(name),
            "contact": redact(re.sub(r"<[^>]+>", "", block)),
            "fingerprint": fingerprint(block),
            "matchesProfile": block.strip() == contact.strip(),
            "note": "values are redacted on purpose; the fingerprint is what "
                    "distinguishes one region's block from another",
        }, indent=2))
        return

    if args.cmd == "slots":
        t = Path(args.template).expanduser().read_text()
        print(json.dumps({
            "areasOfExpertise": len(re.findall(r"\{\{aoe\d+\}\}", t)),
            "technical": "any number" if "{{technical}}" in t
                         else len(re.findall(r"\{\{tech\d+\}\}", t)),
            "roles": len(re.findall(r"\{\{lede\d+\}\}", t)),
            "bulletLists": len(re.findall(r"\{\{bullets\d+\}\}", t)),
        }, indent=2))
        return

    content = json.loads(Path(args.content).expanduser().read_text())
    if args.base_content:
        base = json.loads(Path(args.base_content).expanduser().read_text())
        content = merge(base, content)
        assert_integrity(base, content)
    out = render(Path(args.template).expanduser().read_text(), content)
    Path(args.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).expanduser().write_text(out)
    print(json.dumps({"written": args.out, "bytes": len(out)}, indent=2))


if __name__ == "__main__":
    main()
