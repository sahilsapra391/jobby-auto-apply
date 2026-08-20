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
import html
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


def document_text(path):
    """Text of a tailored resume, from the PDF or from the HTML it renders from.

    The content gate used to read PDFs only, so every wording fix had to be
    pushed through Chrome before it could be checked: 42 of 141 re-renders
    across 84 runs existed for no other reason. Reading the render source turns
    that loop into an edit-and-check with no render in it.

    The HTML is a proxy, and the direction it can be wrong in matters. The page
    box clips overflow, so HTML can hold a line the PDF does not show; it cannot
    show a term the HTML lacks. So a term found here but clipped from the PDF
    reads as present when it is absent, which is the safe direction for a gate
    whose job is to catch terms that should NOT be there. The final check still
    runs against the PDF that gets uploaded.
    """
    path = Path(path).expanduser()
    if path.suffix.lower() in (".html", ".htm"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"<[^>]+>", " ", raw)
        return re.sub(r"\s+", " ", html.unescape(raw))
    return pdf_text(path)


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


# Pairs that mean the same thing on a resume. The left side is how a posting
# tends to say it; the right side is how the base resume or the story bank
# already says it. A match here licenses rewriting the resume into the posting's
# words, because the underlying fact is already the candidate's.
#
# This is the whole point of the aggressive pass, and also its limit: a term with
# no evidence on the right is a term he cannot claim, however much the posting
# wants it.
EQUIVALENTS = [
    ("microservice", "monolith decomposition service extraction"),
    ("service decomposition", "monolith microservice migration"),
    ("experimentation", "a/b test split test feature flag launchdarkly"),
    ("feature flag", "launchdarkly release toggle rollout"),
    ("observability", "monitoring logging tracing metric dashboard"),
    ("stakeholder management", "cross functional partner alignment"),
    ("roadmap", "prioritisation prioritization backlog planning"),
    ("discovery", "user research interview customer conversation"),
    ("llm", "language model gpt claude genai generative"),
    ("rag", "retrieval augmented retrieval-augmented vector embedding"),
    ("eval", "evaluation golden set benchmark accuracy measurement"),
    ("prompt engineering", "prompt design prompt tuning"),
    ("agentic", "agent autonomous workflow orchestration"),
    ("api design", "endpoint contract sdk interface"),
    ("developer experience", "developer tooling dx self serve"),
    ("platform", "infrastructure internal tooling shared service"),
    ("data pipeline", "etl ingestion warehouse"),
    ("sql", "query database analytics"),
    ("go to market", "gtm launch positioning"),
    ("b2b saas", "enterprise saas subscription"),
    ("churn", "retention renewal attrition"),
    ("onboarding", "activation first run time to value"),
    ("technical product manager", "product manager engineering background"),
    ("ci/cd", "continuous integration deployment pipeline release"),
    ("qa", "quality assurance testing test automation"),
    ("migration", "replatform cutover port"),
    ("latency", "performance response time p95 p99"),
    ("scalability", "scale throughput load"),
    ("compliance", "audit governance policy soc2 gdpr"),
    ("mentorship", "mentor coaching managing report"),
]


def evidence_for(term, source_tokens):
    """Where the base resume or story bank already supports this term.

    Returns the matching evidence, or None. Direct presence counts, and so does
    a known equivalent: the candidate having done the thing under a different
    name is exactly the case worth rewriting into the posting's vocabulary.
    """
    if contains(source_tokens, term):
        return term
    haystack = " ".join(source_tokens)
    for posting_word, mine in EQUIVALENTS:
        if term == posting_word or term in posting_word.split():
            for alt in mine.split():
                if alt in haystack:
                    return alt
        # and the other direction, for a posting using the candidate's word
        if term in mine.split() and posting_word in haystack:
            return posting_word
    # A bigram whose halves are both evidenced is evidenced.
    parts = term.split()
    if len(parts) == 2 and all(contains(source_tokens, x) for x in parts):
        return " + ".join(parts)
    return None


# Words that pass the stop list but carry no ATS signal. Weaving "four" or
# "gaps" into Areas of Expertise makes the resume worse, not more matched.
NOISE = set("""
four five six seven eight nine ten first second third next last full part high low big small
fit gap gaps ask asks down drag entirely expect fatal background thing things area areas
lot lots way ways day days week weeks month months time times point points level levels
need needs needed want wants right good better best able
""".split())


def useful(term):
    """Is this worth weaving into a resume at all?"""
    if any(part in NOISE for part in term.split()):
        return False
    if len(term) < 4 and " " not in term:
        return False
    if term.isdigit():
        return False
    return True


def surface_forms(text):
    """Stem -> how the posting actually spells it.

    Matching is done on stems, so "management" and "manages" collapse together.
    But the suggested terms get woven into a resume verbatim, and writing
    "manag" or "kubernet" into Areas of Expertise is worse than writing nothing.
    Keep the posting's own spelling to put back.
    """
    forms = {}
    for raw in TOKEN.findall(text or ""):
        stem = normalise(raw)
        if not stem:
            continue
        # Same punctuation normalise strips, or "platform." ends up in Areas of
        # Expertise with the full stop attached.
        clean = raw.strip(".,;:()[]{}\"'")
        if not clean:
            continue
        # Prefer the longest spelling seen: "management" over "manage".
        if stem not in forms or len(clean) > len(forms[stem]):
            forms[stem] = clean
    return forms


def spell(term, forms):
    return " ".join(forms.get(part, part) for part in term.split())


def suggest(jd_text, resume_text, base_text, facts_text, limit):
    """What the resume could honestly say in the posting's words, and cannot.

    `rewritable` is the aggressive half: terms the posting uses, the resume does
    not, and the base resume or story bank already supports. Those are a rewrite,
    not a claim. `notClaimable` is the residue, and it stays missing.
    """
    jd_terms = [t for t in keywords(jd_text, limit * 2) if useful(t)][:limit]
    resume_tokens = tokens(resume_text)
    source_tokens = tokens(base_text) + tokens(facts_text or "")
    # The posting's spelling for the terms, and the candidate's for the evidence.
    jd_forms = surface_forms(jd_text)
    mine_forms = surface_forms((base_text or "") + " " + (facts_text or ""))
    rewritable, not_claimable, already = [], [], []
    for term in jd_terms:
        if contains(resume_tokens, term):
            already.append(spell(term, jd_forms))
            continue
        found = evidence_for(term, source_tokens)
        if found:
            rewritable.append({"term": spell(term, jd_forms),
                               "evidence": spell(found, mine_forms)})
        else:
            not_claimable.append(spell(term, jd_forms))
    reachable = len(already) + len(rewritable)
    return {
        "postingTerms": len(jd_terms),
        "alreadyCovered": already,
        "rewritable": rewritable,
        "notClaimable": not_claimable,
        "coverageNow": round(100 * len(already) / max(1, len(jd_terms))),
        "coverageIfRewritten": round(100 * reachable / max(1, len(jd_terms))),
        "honestCeiling": round(100 * reachable / max(1, len(jd_terms))),
    }


def main():
    ap = argparse.ArgumentParser(description="Keyword coverage, and what it cost in truth.")
    ap.add_argument("--jd", required=True, help="job description text file")
    ap.add_argument("--resume", help="tailored resume, PDF or the .html it renders "
                                     "from (not needed with --suggest). Use the HTML "
                                     "while iterating on wording; check the PDF once at "
                                     "the end.")
    ap.add_argument("--base", required=True, help="base resume PDF, the source of truth")
    ap.add_argument("--facts", help="optional text file of confirmed story facts")
    ap.add_argument("--limit", type=int, default=60, help="how many JD keywords to score")
    ap.add_argument("--suggest", action="store_true",
                    help="before writing: which posting terms the base resume already "
                         "supports under a different name, and are therefore a rewrite "
                         "rather than a claim")
    args = ap.parse_args()
    if not args.suggest and not args.resume:
        ap.error("--resume is required unless you are running --suggest")

    jd = Path(args.jd).expanduser().read_text(encoding="utf-8", errors="replace")
    # --suggest runs BEFORE the tailored resume exists; that is the point of it.
    tailored_text = document_text(args.resume) if args.resume else ""
    base_text = pdf_text(args.base)
    facts_text = Path(args.facts).expanduser().read_text(encoding="utf-8", errors="replace") if args.facts else ""

    kws = keywords(jd, args.limit)
    tail_tok = tokens(tailored_text)
    if args.suggest:
        out = suggest(jd, tailored_text, base_text, facts_text, args.limit)
        out["howToUse"] = (
            "Rewrite Areas of Expertise, Technical Proficiencies and bullet wording into "
            "the posting's words for every entry in `rewritable`: the fact is already his, "
            "only the vocabulary changes. Never add anything from `notClaimable`."
        )
        print(json.dumps(out, indent=2))
        return 0

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
