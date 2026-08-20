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
import json
import re
import sys
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
    out = out.replace("{{banner}}", safe(content.get("banner", "")))
    out = out.replace("{{summary}}", safe(content.get("summary", "")))

    aoe = content.get("areasOfExpertise") or []
    need("areas of expertise", len(re.findall(r"\{\{aoe\d+\}\}", template)), len(aoe))
    for i, value in enumerate(aoe):
        out = out.replace("{{aoe%d}}" % i, safe(value))

    tech = content.get("technical") or []
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

    if args.cmd == "slots":
        t = Path(args.template).expanduser().read_text()
        print(json.dumps({
            "areasOfExpertise": len(re.findall(r"\{\{aoe\d+\}\}", t)),
            "technical": len(re.findall(r"\{\{tech\d+\}\}", t)),
            "roles": len(re.findall(r"\{\{lede\d+\}\}", t)),
            "bulletLists": len(re.findall(r"\{\{bullets\d+\}\}", t)),
        }, indent=2))
        return

    content = json.loads(Path(args.content).expanduser().read_text())
    if args.base_content:
        base = json.loads(Path(args.base_content).expanduser().read_text())
        content = merge(base, content)
    out = render(Path(args.template).expanduser().read_text(), content)
    Path(args.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).expanduser().write_text(out)
    print(json.dumps({"written": args.out, "bytes": len(out)}, indent=2))


if __name__ == "__main__":
    main()
