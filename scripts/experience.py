#!/usr/bin/env python3
"""Lift a single role's content out of a tailored resume.

Application forms that ask for work history usually put a free-text box under
each entry: "Description", "Responsibilities", "Summary". Agents left those
blank, which is the worst outcome available, because a recruiter reading the
form sees an empty job while the attached resume is full.

The content has to come from the resume built for *that* posting, not from a
generic blurb. The tailored resume already weaves the posting's own vocabulary
into each role, so anything else would contradict the attachment sitting next to
it in the same application.

We read `.resume.html`, the source the PDF is rendered from, rather than
extracting text back out of the PDF. The HTML keeps role boundaries as markup,
so a role is looked up rather than guessed at from reflowed text.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCK = re.compile(
    r'<div class="(?P<cls>org|jobtitle|body lede)"[^>]*>(?P<inner>.*?)</div>\s*(?=<)',
    re.S,
)
LIST = re.compile(r"<ul>(.*?)</ul>", re.S)
ITEM = re.compile(r"<li>(.*?)</li>", re.S)
# Dates ride inside the org line in their own span, and inside the title in
# parentheses. Both are wanted as labels, neither as part of the name.
WHEN = re.compile(r'<span class="when"[^>]*>(.*?)</span>', re.S)
TRAILING_DATES = re.compile(r"\s*\((\d{4}\s*(?:[-–—]\s*(?:\d{4}|present))?)\)\s*$", re.I)
# "ADDITIONAL EXPERIENCE" entries have no org heading of their own; the company
# sits in a span inside the title. Without this the role inherits whichever
# company was listed above it, which is how an internship ended up filed under
# the wrong employer.
INLINE_ORG = re.compile(r'<span(?![^>]*class="when")[^>]*>(.*?)</span>', re.S)


def text_of(fragment):
    """Markup to plain text, entities resolved.

    Entity handling is the point. A resume carries &rsquo; and &ndash; because
    it is typeset; pasting those raw into a form is a visible defect.
    """
    fragment = re.sub(r"<br\s*/?>", " ", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def split_org(raw):
    when = ""
    found = WHEN.search(raw)
    if found:
        when = text_of(found.group(1))
        raw = WHEN.sub("", raw)
    return text_of(raw), when


def parse(source):
    """Roles in document order, each inheriting the org heading above it.

    One org heading can cover several roles (a promotion within one company
    lists the company once), so the heading is carried down rather than read
    per role.
    """
    body = re.sub(r"<style.*?</style>", "", source, flags=re.S)
    start = body.find("EXPERIENCE")
    if start != -1:
        body = body[start:]

    roles = []
    org, org_when = "", ""
    for match in BLOCK.finditer(body):
        cls, inner = match.group("cls"), match.group("inner")
        if cls == "org":
            org, org_when = split_org(inner)
        elif cls == "jobtitle":
            company = org
            dates = org_when
            inline = INLINE_ORG.search(inner)
            if inline:
                company = text_of(inline.group(1))
                inner = INLINE_ORG.sub("", inner)
                dates = ""
            title = text_of(inner).rstrip(",").strip()
            found = TRAILING_DATES.search(title)
            if found:
                dates = found.group(1)
                title = TRAILING_DATES.sub("", title)
            roles.append(
                {
                    "title": title,
                    "company": company,
                    "dates": dates,
                    "lede": "",
                    "bullets": [],
                    "_at": match.end(),
                }
            )
        elif cls == "body lede" and roles:
            roles[-1]["lede"] = text_of(inner)

    # Bullets attach to the last role opened before them. Matching by position
    # keeps a two-role company from collecting both lists onto one role.
    for block in LIST.finditer(body):
        owner = None
        for role in roles:
            if role["_at"] <= block.start():
                owner = role
        if owner is not None:
            owner["bullets"] = [text_of(i) for i in ITEM.findall(block.group(1))]

    for role in roles:
        role.pop("_at", None)
    return roles


def render(role, style="text", max_chars=0):
    lede, bullets = role["lede"], list(role["bullets"])
    if style == "prose":
        parts = ([lede] if lede else []) + bullets
        out = " ".join(parts)
    else:
        lines = [lede] if lede else []
        lines += ["- " + b for b in bullets]
        out = "\n".join(lines)

    if max_chars and len(out) > max_chars:
        # Drop whole bullets from the end. A box with a character cap is common
        # on Workday, and cutting mid-sentence reads worse than saying less.
        while bullets and len(out) > max_chars:
            bullets.pop()
            if style == "prose":
                out = " ".join(([lede] if lede else []) + bullets)
            else:
                out = "\n".join(([lede] if lede else []) + ["- " + b for b in bullets])
        if len(out) > max_chars:
            clipped = out[:max_chars]
            stop = max(clipped.rfind(". "), clipped.rfind("; "))
            out = clipped[: stop + 1] if stop > max_chars * 0.5 else clipped.rstrip()
    return out.strip()


def resume_for(args):
    if args.resume:
        return Path(args.resume)
    folder = Path(args.app)
    if not folder.is_absolute():
        folder = ROOT / "applications" / args.app
    resume = folder / ".resume.html"
    if resume.exists():
        return resume
    # Older folders, or a run that named the render source differently. Prefer
    # anything with "resume" in the name over the cover letter, which parses
    # cleanly and returns no roles at all.
    candidates = sorted(folder.glob("*.html")) if folder.is_dir() else []
    for candidate in candidates:
        if "resume" in candidate.name.lower():
            return candidate
    return candidates[0] if candidates else resume


def pick(roles, wanted):
    """Match on title or company, and require the match to be unambiguous.

    Guessing between two roles at the same company would put the wrong five
    years of history into a form, so an ambiguous request is an error.
    """
    def norm(value):
        # "&" and "and" are the same word to a reader, and a form's stored title
        # spells it one way while the resume spells it the other.
        value = value.lower().replace("&", " and ")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    needle = norm(wanted)
    # Exact title first. A caller usually passes the title it just typed into
    # the form, and "Product Manager & Business Analyst" is both one role's full
    # title and a substring of "Technical Product Manager & Business Analyst II".
    # Without this the precise request is the one that fails as ambiguous.
    exact = [r for r in roles if norm(r["title"]) == needle]
    if len(exact) == 1:
        return exact

    hits = [r for r in roles if needle in norm(r["title"])]
    if not hits:
        hits = [r for r in roles if needle in norm(r["company"])]
    if not hits:
        for r in roles:
            words = [w for w in re.split(r"\W+", needle) if len(w) > 3]
            haystack = norm(r["title"] + " " + r["company"])
            if words and all(w in haystack for w in words):
                hits.append(r)
    return hits


def work_history(root=None):
    """The profile's employment record, which is where real dates live.

    The resume prints years, because that is what a resume does. A form wants a
    month and a year per position, and it wants them to be true.
    """
    import subprocess
    args = [sys.executable, str(ROOT / "scripts" / "store.py"), "profile-get"]
    if root:
        args = args[:2] + ["--root", str(root)] + args[2:]
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return (json.loads(out.stdout or "{}") or {}).get("workHistory") or []


def month_year(value):
    """A store date to the parts a form asks for, or None."""
    text = str(value or "").strip()
    if not text:
        return None
    found = re.match(r"(\d{4})-(\d{2})", text)
    if not found:
        return None
    year, month = int(found.group(1)), int(found.group(2))
    return {"year": year, "month": month, "text": f"{year}-{month:02d}"}


def plan(html, root=None, max_chars=0, style="text"):
    """Every position, newest first, ready to fill a repeatable form section.

    Forms that take employment history almost always let you add another entry,
    and the whole history is supposed to go in. Runs were filling the first one
    and moving on, which leaves a candidate looking like he has held one job.

    Two sources, joined on the job title. The profile carries the dates and the
    city and is the record of what is true. The resume carries the wording
    tailored to this posting, so the description in the form says what the
    attached document says.
    """
    roles = parse(html)
    history = work_history(root)

    def key(value):
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    remaining = list(history)
    positions = []
    for role in roles:
        match = None
        for entry in remaining:
            if key(entry.get("title")) == key(role["title"]):
                match = entry
                break
        if match is None:                      # fall back to the company
            for entry in remaining:
                if key(entry.get("company"))[:12] and \
                        key(entry.get("company"))[:12] in key(role["company"]):
                    match = entry
                    break
        if match is not None:
            remaining.remove(match)

        start = month_year((match or {}).get("start"))
        end = month_year((match or {}).get("end"))
        positions.append({
            "title": role["title"],
            "company": (match or {}).get("company") or role["company"].split(",")[0].strip(),
            "location": (match or {}).get("city") or "",
            "startDate": start,
            "endDate": end,
            "isCurrent": match is not None and not (match or {}).get("end"),
            "description": render(role, style, max_chars),
            "datesFromProfile": match is not None,
        })

    # Newest first, which is the order every form expects. Anything without a
    # start date sorts last rather than being dropped.
    positions.sort(key=lambda p: (p["startDate"] or {}).get("text") or "", reverse=True)
    for i, position in enumerate(positions, 1):
        position["order"] = i
    return positions


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--app", help="company folder under applications/")
    src.add_argument("--resume", help="path to a .resume.html")
    ap.add_argument("--role", help="title or company fragment to look up")
    ap.add_argument("--list", action="store_true", help="show every role")
    ap.add_argument("--plan", action="store_true",
                    help="every position, newest first, with dates from the profile and "
                         "the description from this posting's resume. What a repeatable "
                         "employment section needs.")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    ap.add_argument("--format", choices=["text", "prose", "json"], default="text")
    ap.add_argument("--max-chars", type=int, default=0)
    args = ap.parse_args()

    path = resume_for(args)
    if not path.exists():
        raise SystemExit(f"no tailored resume at {path}")
    roles = parse(path.read_text())
    if not roles:
        raise SystemExit(f"no experience entries found in {path}")

    if args.plan:
        positions = plan(path.read_text(), args.root, args.max_chars, args.format
                         if args.format in ("text", "prose") else "text")
        print(json.dumps({"positions": positions, "count": len(positions),
                          "note": "Fill every one of these. If the form only shows one "
                                  "block, look for Add another before moving on."},
                         indent=2))
        return

    if args.list or not args.role:
        if args.format == "json":
            print(json.dumps(roles, indent=2))
        else:
            for r in roles:
                mark = "" if (r["lede"] or r["bullets"]) else "   (no content)"
                print(f"{r['title']} | {r['company']} | {r['dates']}{mark}")
        return

    hits = pick(roles, args.role)
    if not hits:
        print(f"no role matching {args.role!r}. available:", file=sys.stderr)
        for r in roles:
            print(f"  {r['title']} | {r['company']}", file=sys.stderr)
        raise SystemExit(2)
    if len(hits) > 1:
        print(f"{args.role!r} matches {len(hits)} roles, be specific:", file=sys.stderr)
        for r in hits:
            print(f"  {r['title']} | {r['company']} | {r['dates']}", file=sys.stderr)
        raise SystemExit(2)

    role = hits[0]
    if args.format == "json":
        print(json.dumps(role, indent=2))
        return
    body = render(role, args.format, args.max_chars)
    if not body:
        # Surfaced rather than printed empty, because the caller's whole job is
        # to not leave the box blank.
        raise SystemExit(
            f"the resume carries no description for {role['title']!r}. "
            "Write one factual line from the title, company and dates. "
            "Do not invent metrics."
        )
    print(body)


if __name__ == "__main__":
    main()
