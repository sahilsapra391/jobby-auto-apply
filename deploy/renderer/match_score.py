#!/usr/bin/env python3
"""Match % — a rubric, not a model.

Every Found card shows a percentage, and every percentage has to be
defensible: the card's "why this %" panel is the same numbers this file
produces, and a score nobody can account for is worse than no score, because
it looks like a judgement when it is a mood.

So the rubric is deterministic and runs no LLM per job. Five components,
weights in one place for Phase 4 calibration:

  title / role          35    does the title match what is being searched for
  experience            20    is the stated requirement inside the ceiling
  location / workplace  15    right country, right arrangement
  keyword coverage      20    does the body speak the search's vocabulary
  freshness             10    how long it has been up

Two rules keep it honest.

**Unknown is neutral, never zero.** Phase 1 extracts none of the deep
attributes, so most jobs have an unknown required-experience figure. Scoring
those as zero would rank every posting that happens to state its requirements
below every posting that does not, which is a fact about the posting's prose
rather than about the job. Unknown attributes take half their weight and are
named in `unknowns`, so the card can draw an explicit `unknown` chip rather
than implying knowledge.

**A gate caps, it never silently drops.** Citizenship or clearance language, a
posting that failed liveness, a job already applied to — these cap the total at
25 and say which gate fired. Dropping them would mean a role vanishing with no
explanation, and the user could not tell that from a scan that missed it.

  python3 scripts/match_score.py --job job.json --config search-config.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ats_match  # noqa: E402  (the stemming the resume matcher already uses)
import jobs_store  # noqa: E402  (one tolerant timestamp parser, not two)
import search_config  # noqa: E402

# One place, so Phase 4 can calibrate against outcomes without hunting.
WEIGHTS = {
    "title": 35,
    "experience": 20,
    "location": 15,
    "keywords": 20,
    "freshness": 10,
}

# An unknown attribute is worth half its weight: neither rewarded for being
# unstated nor punished for it.
NEUTRAL = 0.5

# A gate does not reject, it caps. The role stays visible with the reason
# attached, because "not shown" and "shown and explained" are very different
# things to the person reading the board.
GATE_CAP = 25

# A posting is at full freshness for its first day and worth nothing after a
# month, which is also when an unacted-on job is pruned.
FRESH_FULL_DAYS = 1
FRESH_ZERO_DAYS = 30
# With no posted date, the date the scan first saw it is the best available
# evidence — and it is evidence about the scan, not about the posting, so it
# earns half.
SEEN_INSTEAD_OF_POSTED = 0.5

# Experience overshoot. A posting asking one year above the ceiling is worth
# looking at; one asking five above is a different band of role.
OVERSHOOT_BANDS = ((1, 0.5), (3, 0.25))

# Language that makes an application pointless regardless of fit. Deliberately
# narrow: these are phrases about eligibility to hold the role, not about
# sponsorship preferences, which the standing answers already handle.
CITIZENSHIP_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"\bmust be a (?:us|u\.s\.|united states) citizen",
        r"\b(?:us|u\.s\.|united states) citizenship (?:is )?(?:is required|required)",
        r"\bcitizenship (?:is )?required\b",
        r"\b(?:us|u\.s\.) citizens only\b",
        r"\bsecurity clearance\b",
        r"\bactive clearance\b",
        r"\bts/sci\b",
        r"\btop[- ]secret\b[^.]{0,30}\bclearance\b",
        r"\bpublic trust clearance\b",
        r"\bmust be able to obtain (?:a )?(?:security )?clearance",
    )
]

# "No security clearance is required for this role" contains the phrase and
# means the opposite of it. A gate that fires on that caps a perfectly good job
# at 25 and tells the user the reason is clearance, which is worse than not
# having the gate at all.
NEGATION_BEFORE = re.compile(
    r"\b(?:no|not|without|never|does not|do not|is not|are not|isn't|doesn't)\s+"
    r"(?:\w+\s+){0,3}$", re.I)
NEGATION_AFTER = re.compile(
    r"^\W*(?:is|are|will be|would be)?\s*(?:not|no longer)\s+"
    r"(?:required|needed|necessary|expected)", re.I)


def _negated(text, start, end):
    return bool(NEGATION_BEFORE.search(text[max(0, start - 40):start])
                or NEGATION_AFTER.match(text[end:end + 40]))


GATE_LABELS = {
    "citizenship": "citizenship or clearance required",
    "liveness": "the posting could not be verified live",
    "duplicate": "already applied to this one",
}


def _stems(text):
    return [t for t in ats_match.tokens(text) if t]


def _phrase_hit(phrase, haystack_stems):
    """How much of a search phrase the text actually says, 0.0 to 1.0.

    Stemmed, because "manager" and "management" are the same claim and matching
    on raw strings would miss most real titles.
    """
    wanted = [w for w in _stems(phrase) if w and w not in ats_match.STOP]
    if not wanted:
        return 0.0
    present = sum(1 for w in wanted if w in haystack_stems)
    return present / len(wanted)


def _best_phrase_hit(phrases, text):
    haystack = set(_stems(text))
    if not haystack:
        return 0.0
    return max((_phrase_hit(p, haystack) for p in phrases), default=0.0)


def score_title(job, config):
    keywords = config.get("keywords") or []
    if not keywords:
        return NEUTRAL, "no titles in the saved search", True
    hit = _best_phrase_hit(keywords, job.get("title") or "")
    if hit >= 1.0:
        return 1.0, "title matches a searched title", False
    if hit > 0:
        return hit, f"title partly matches ({int(hit * 100)}% of a searched title)", False
    return 0.0, "title matches none of the searched titles", False


def score_experience(job, config):
    ceiling = config.get("maxExperienceYears")
    if ceiling is None:
        return 1.0, "open to all experience levels", False
    extracted = job.get("extracted") or {}
    # The floor is what a screen rejects on. Nobody is ever turned down for
    # having too few years above a stated ceiling, so "5–8 years" is judged on
    # the 5.
    stated = extracted.get("yearsMin")
    if stated is None:
        stated = extracted.get("yearsMax")
    if not isinstance(stated, (int, float)):
        return NEUTRAL, "the posting does not state a requirement", True
    if stated <= ceiling:
        return 1.0, f"asks for {extracted.get('yearsText') or f'{stated:g} years'}", False
    over = stated - ceiling
    for limit, value in OVERSHOOT_BANDS:
        if over <= limit:
            return value, f"asks for {stated:g} years, {over:g} over the ceiling", False
    return 0.0, f"asks for {stated:g} years, well past the ceiling", False


def score_location(job, config):
    """Country and workplace, weighted 60/40 inside the component.

    Country is the one that decides whether an application is possible at all;
    the arrangement is a preference on top of it.
    """
    wanted = set(search_config.expand_locations(config))
    country = str(((job.get("location") or {}).get("country")) or "").upper()
    workplace = str(job.get("workplace") or "").lower()
    remote_counts = config.get("includeRemoteForSelection", True) and workplace == "remote"

    unknown = []
    if not wanted:
        place, place_note = 1.0, "every country is in scope"
    elif not country:
        place, place_note = (1.0, "remote, and remote is in scope") if remote_counts else (
            NEUTRAL, "the posting does not say which country")
        if not remote_counts:
            unknown.append("country")
    elif country in wanted or remote_counts:
        place, place_note = 1.0, f"{country} is in scope"
    else:
        place, place_note = 0.0, f"{country} is not in scope"

    allowed = config.get("workplace") or list(search_config.WORKPLACES)
    if not workplace:
        setup, setup_note = NEUTRAL, "the posting does not say remote, hybrid or onsite"
        unknown.append("workplace")
    elif workplace in allowed:
        setup, setup_note = 1.0, f"{workplace} is acceptable"
    else:
        setup, setup_note = 0.0, f"{workplace} is not on the list"

    return (place * 0.6 + setup * 0.4), f"{place_note}; {setup_note}", unknown


def score_keywords(job, config):
    keywords = config.get("keywords") or []
    if not keywords:
        return NEUTRAL, "no keywords in the saved search", True
    text = job.get("title") or ""
    if config.get("searchScope") != "title":
        text = f"{text}\n{job.get('descriptionExcerpt') or ''}"
    haystack = set(_stems(text))
    if not haystack:
        return NEUTRAL, "nothing to read yet", True
    hits = [_phrase_hit(k, haystack) for k in keywords]
    covered = sum(1 for h in hits if h >= 1.0)
    partial = sum(h for h in hits if h < 1.0)
    value = (covered + partial * 0.5) / len(keywords)
    return min(1.0, value), f"{covered} of {len(keywords)} search terms appear in full", False


def _age_days(job, now):
    """(days since the posting went up, whether that came from a sighting)."""
    posted = jobs_store.parse_stamp(job.get("postedAt"))
    if posted is not None:
        return (now - posted).total_seconds() / 86400.0, False
    seen = jobs_store.parse_stamp(job.get("firstSeenAt"))
    if seen is None:
        return None, True
    return (now - seen).total_seconds() / 86400.0, True


def score_freshness(job, now):
    age, from_sighting = _age_days(job, now)
    if age is None:
        return NEUTRAL, "no date on the posting and no sighting recorded", True
    age = max(0.0, age)
    if age <= FRESH_FULL_DAYS:
        value = 1.0
    elif age >= FRESH_ZERO_DAYS:
        value = 0.0
    else:
        value = 1.0 - (age - FRESH_FULL_DAYS) / (FRESH_ZERO_DAYS - FRESH_FULL_DAYS)
    if from_sighting:
        # Evidence about when the scan noticed it, not about when it went up.
        return value * SEEN_INSTEAD_OF_POSTED, f"first seen {age:.0f} days ago; no posted date", True
    return value, f"posted {age:.0f} days ago", False


def gates(job, flags=None):
    """Reasons this role is capped, each with the label the card shows."""
    flags = flags or {}
    found = []
    text = f"{job.get('title') or ''}\n{job.get('descriptionExcerpt') or ''}"
    for pattern in CITIZENSHIP_PATTERNS:
        for hit in pattern.finditer(text):
            if _negated(text, hit.start(), hit.end()):
                continue
            found.append({"gate": "citizenship", "label": GATE_LABELS["citizenship"],
                          "evidence": hit.group(0)[:80]})
            break
        if found:
            break
    if flags.get("livenessFailed"):
        found.append({"gate": "liveness", "label": GATE_LABELS["liveness"],
                      "evidence": str(flags.get("livenessReason") or "")[:80]})
    if flags.get("duplicateOfApplied"):
        found.append({"gate": "duplicate", "label": GATE_LABELS["duplicate"],
                      "evidence": str(flags.get("duplicateReason") or "")[:80]})
    return found


def score(job, config=None, now=None, flags=None):
    """The score object stored on the job and rendered on the card.

    `parts` always sums to `raw`, and `total` is `raw` unless a gate capped it.
    That relationship is what makes the "why this %" panel checkable rather
    than decorative.
    """
    now = now or datetime.now(timezone.utc)
    config = search_config.validate(config or {})

    unknowns = []
    title_v, title_why, title_unknown = score_title(job, config)
    if title_unknown:
        unknowns.append("title")
    exp_v, exp_why, exp_unknown = score_experience(job, config)
    if exp_unknown:
        unknowns.append("experience")
    loc_v, loc_why, loc_unknowns = score_location(job, config)
    unknowns.extend(loc_unknowns)
    kw_v, kw_why, kw_unknown = score_keywords(job, config)
    if kw_unknown:
        unknowns.append("keywords")
    fresh_v, fresh_why, fresh_unknown = score_freshness(job, now)
    if fresh_unknown:
        unknowns.append("postedAt")

    fractions = {"title": title_v, "experience": exp_v, "location": loc_v,
                 "keywords": kw_v, "freshness": fresh_v}
    parts = {name: int(round(WEIGHTS[name] * value)) for name, value in fractions.items()}
    raw = sum(parts.values())

    found = gates(job, flags)
    total = min(raw, GATE_CAP) if found else raw
    return {
        "total": total,
        "raw": raw,
        "parts": parts,
        "weights": dict(WEIGHTS),
        "why": {"title": title_why, "experience": exp_why, "location": loc_why,
                "keywords": kw_why, "freshness": fresh_why},
        "unknowns": sorted(set(unknowns)),
        "gates": found,
        "configVersion": config.get("version", 0),
    }


def explain(score):
    """One honest line summarising a score, for a card that has no fit read yet.

    Phase 1 promotes a job to Watching before any prose about it exists. The
    card has to say something, and the something has to be the rubric rather
    than a sentence implying somebody read the posting.
    """
    if not score:
        return "Not scored yet."
    parts = score.get("parts") or {}
    weights = score.get("weights") or WEIGHTS
    breakdown = ", ".join(f"{name} {parts.get(name, 0)}/{weights.get(name, 0)}"
                          for name in ("title", "experience", "location",
                                       "keywords", "freshness") if name in parts)
    line = f"Match {score.get('total', 0)}% — {breakdown}."
    unknowns = score.get("unknowns") or []
    if unknowns:
        line += f" Unknown: {', '.join(unknowns)}."
    for gate in score.get("gates") or []:
        line += f" Capped: {gate.get('label')}."
    return line + " Rubric only; no fit read written yet."


def main():
    ap = argparse.ArgumentParser(description="Score one job against a saved search.")
    ap.add_argument("--job", required=True, help="job record JSON, or - for stdin")
    ap.add_argument("--config", help="search config JSON; defaults when absent")
    args = ap.parse_args()
    job = json.load(sys.stdin) if args.job == "-" else json.loads(Path(args.job).read_text())
    config = json.loads(Path(args.config).read_text()) if args.config else {}
    print(json.dumps(score(job, config), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
