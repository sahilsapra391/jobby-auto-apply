#!/usr/bin/env python3
"""Measure keyword coverage between a job description and a tailored resume.

Two numbers, and the second one matters more than the first:

  coverage   how much of the posting's vocabulary the resume speaks back
  unsourced  terms present in the tailored resume but absent from the BASE
             resume and the story bank

Chasing a high coverage number is exactly the pressure that invents skills, so
this reports both. A high coverage with a non-empty unsourced list is not a
good resume, it is a resume that started lying. The unsourced list is the one
to drive to zero.

  python3 scripts/ats_match.py --jd posting.txt --resume out.pdf --base base.pdf
  python3 scripts/ats_match.py --jd posting.txt --resume out.pdf --base base.pdf --facts stories.txt

Standard library plus pymupdf.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Job-posting boilerplate. These words are everywhere and mean nothing, so
# counting them inflates coverage without the resume saying anything real.
STOP = set("""
a an the and or but if then than that this these those there here of in on at to for with by from as
is are was were be been being do does did doing have has had having will would shall should can could
may might must we you they he she it our your their his her its us them who whom which what when where
why how all any both each few more most other some such no nor not only own same so too very just also
about above after again against because before below between during into more once out over under until
while your role team teams work working works job jobs position role roles company companies opportunity
opportunities candidate candidates applicant applicants apply application hiring hire hired employee
employees employment benefits salary compensation equity insurance health dental vision paid time off
pto holiday holidays remote hybrid onsite office location locations year years experience experienced
required requirements requirement preferred preferably qualification qualifications responsibility
responsibilities skills skill ability abilities strong excellent great good proven track record demonstrated
plus bonus nice must ideal ideally looking seeking join us new help support ensure drive deliver build
building built work across within including include includes included etc using use used uses able
who what where when why how many much well like well-versed
""".split())

# Two-word phrases are where the real signal lives ("feature flags", "release
# management"), so bigrams are scored alongside single words.
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{1,}")


def normalise(word):
    w = word.lower().strip(".,;:()[]{}\"'")
    for suffix in ("'s", "ing", "ers", "er", "es", "s"):
        if len(w) > 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def tokens(text):
    return [normalise(t) for t in TOKEN.findall(text or "")]


def keywords(text, limit=60):
    """The posting's distinctive vocabulary: unigrams plus bigrams, ranked."""
    words = [w for w in tokens(text) if w and w not in STOP and len(w) > 2]
    uni = Counter(words)
    bi = Counter()
    raw = [w for w in tokens(text) if w]
    for a, b in zip(raw, raw[1:]):
        if a not in STOP and b not in STOP and len(a) > 2 and len(b) > 2:
            bi[f"{a} {b}"] += 1
    scored = [(c * 2, k) for k, c in bi.items() if c >= 2]
    scored += [(c, k) for k, c in uni.items()]
    scored.sort(key=lambda t: (-t[0], t[1]))
    seen, out = set(), []
    for _, k in scored:
        if k in seen:
            continue
        # Skip a unigram already represented by a kept bigram.
        if any(k in phrase.split() for phrase in out if " " in phrase):
            continue
        seen.add(k)
        out.append(k)
        if len(out) >= limit:
            break
    return out


def pdf_text(path):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            print("ats_match: pymupdf is not installed "
                  "(python3 -m pip install pymupdf)", file=sys.stderr)
            raise SystemExit(2)
    doc = pymupdf.open(str(Path(path).expanduser()))
    text = "\n".join(p.get_text() for p in doc)
    doc.close()
    return text


def contains(haystack_tokens, phrase):
    parts = phrase.split()
    if len(parts) == 1:
        return parts[0] in haystack_tokens
    joined = " ".join(haystack_tokens)
    return phrase in joined


def main():
    ap = argparse.ArgumentParser(description="Keyword coverage, and what it cost in truth.")
    ap.add_argument("--jd", required=True, help="job description text file")
    ap.add_argument("--resume", required=True, help="tailored resume PDF")
    ap.add_argument("--base", required=True, help="base resume PDF, the source of truth")
    ap.add_argument("--facts", help="optional text file of confirmed story facts")
    ap.add_argument("--limit", type=int, default=60, help="how many JD keywords to score")
    args = ap.parse_args()

    jd = Path(args.jd).expanduser().read_text(encoding="utf-8", errors="replace")
    tailored_text = pdf_text(args.resume)
    base_text = pdf_text(args.base)
    facts_text = Path(args.facts).expanduser().read_text(encoding="utf-8", errors="replace") if args.facts else ""

    kws = keywords(jd, args.limit)
    tail_tok = tokens(tailored_text)
    matched = [k for k in kws if contains(tail_tok, k)]
    missing = [k for k in kws if k not in matched]

    # The risk worth flagging is narrow and specific: a term that appears in
    # BOTH the posting and the tailored resume but in neither the base resume
    # nor the story facts. That is the shape of a keyword lifted off the
    # posting. Flagging every unfamiliar word instead just buries it in
    # ordinary English.
    allowed = set(tokens(base_text)) | set(tokens(facts_text))
    allowed_joined = " ".join(sorted(allowed))
    unsourced = []
    for k in matched:
        if contains(list(allowed), k):
            continue
        if all(part in allowed for part in k.split()):
            continue
        if k in allowed_joined:
            continue
        unsourced.append(k)

    coverage = round(100 * len(matched) / len(kws), 1) if kws else 0.0
    result = {
        "coverage": coverage,
        "keywordsScored": len(kws),
        "matched": matched,
        "missing": missing,
        "unsourcedTerms": sorted(unsourced),
        "verdict": (
            "unsourced terms present: these are in the resume but in neither the base "
            "resume nor the story facts. Cut them or trace them before sending."
            if unsourced else
            "every word in the tailored resume traces to the base resume or the story facts."
        ),
    }
    print(json.dumps(result, indent=2))
    return 1 if unsourced else 0


if __name__ == "__main__":
    sys.exit(main())
