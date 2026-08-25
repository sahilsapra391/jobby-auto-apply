#!/usr/bin/env python3
"""The onboarding question taxonomy, and the accounting that keeps it honest.

A stranger has to reach useful coverage in twenty minutes. The person this was
built for reached it across a hundred applications over months, one parked
question at a time, so the question is which twenty minutes.

That is answerable rather than guessable, because a hundred real applications
have already been answered and every answer is stored with the question text an
employer actually used. Those hundred questions collapse hard: sponsorship was
asked six ways, relocation five, political contributions four, sexual
orientation four. **Topics are what a person can answer. Questions are what
employers ask.** The taxonomy in references/questionnaire.json is the first, and
this file checks it against the second.

The check is the point, and it is the same accounting sources_registry.py does
for channels. "Every real question is covered" is not a claim anyone can verify
by reading a JSON file. It is only meaningful if an observed question that
matches no topic is countable, so this exits non-zero when one is.

Two properties worth defending if they are ever proposed away.

**The taxonomy holds no answers.** It is the shared half of the product's
privacy line: that a form asks about sponsorship is public and every account
benefits from recognising it, while what a person said is theirs alone. This is
what lets the file ship in the repository and survive an open-source publish.

**Coverage is reported per tier, not as one number.** A taxonomy that covers
95% of observed questions while missing a blocking one is worse than it sounds,
because the miss is what stops an application rather than what annoys someone.

  python3 scripts/questionnaire.py check           # exit 1 on an uncovered question
  python3 scripts/questionnaire.py plan            # the onboarding order
  python3 scripts/questionnaire.py coverage --store DIR
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TAXONOMY = REPO / "references" / "questionnaire.json"

TIERS = (1, 2, 3)
# A topic that names none of these is a schema error rather than a new idea.
TYPES = {"country-multi", "choice", "multi", "text-short", "text-long", "number",
         "money", "phone", "address", "links", "policy", "per-application"}


class TaxonomyError(ValueError):
    """The taxonomy file is malformed or self-inconsistent."""


def load(path=None):
    path = Path(path or TAXONOMY)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaxonomyError(f"cannot read {path}") from exc
    except ValueError as exc:
        raise TaxonomyError(f"{path} is not valid JSON") from exc
    topics = payload.get("topics")
    if not isinstance(topics, list) or not topics:
        raise TaxonomyError("taxonomy has no topics")
    seen = set()
    for topic in topics:
        for field in ("id", "tier", "ask", "type", "match", "why"):
            if field not in topic:
                raise TaxonomyError(f"topic {topic.get('id', '?')} has no {field}")
        if topic["id"] in seen:
            raise TaxonomyError(f"duplicate topic id {topic['id']}")
        seen.add(topic["id"])
        if topic["tier"] not in TIERS:
            raise TaxonomyError(f"topic {topic['id']} has tier {topic['tier']}")
        if topic["type"] not in TYPES:
            raise TaxonomyError(f"topic {topic['id']} has unknown type {topic['type']}")
        if not topic["match"]:
            raise TaxonomyError(f"topic {topic['id']} has no match terms")
    return payload


def assert_no_answers(payload):
    """The taxonomy must never carry what anyone said.

    Checked rather than trusted, because the failure is silent and permanent:
    an answer value committed here ships in the repository, and the
    open-source publish squashes history so it cannot be quietly removed later.
    """
    banned = {"value", "answer", "answers", "response", "state"}
    def walk(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in banned:
                    raise TaxonomyError(
                        f"taxonomy carries an answer-shaped key at {path}.{key}; "
                        "this file holds questions only")
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
    walk(payload, "$")


def normalise(text):
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def match_topic(question, topics):
    """The topic an observed question belongs to, or None.

    A topic matches when any one of its `match` entries has ALL its terms
    present. Terms are ANDed inside an entry and entries are ORed, so a topic
    can require a pair ("authorized" AND "work") without also claiming every
    question that merely says "work".

    Longest match wins on ties. "sponsorship" and "authorized work" both appear
    in some questions, and the more specific phrase is the better label.
    """
    text = normalise(question)
    best = None
    best_weight = -1
    for topic in topics:
        for entry in topic["match"]:
            terms = [entry] if isinstance(entry, str) else entry
            if all(normalise(term) in text for term in terms):
                weight = sum(len(normalise(term)) for term in terms)
                if weight > best_weight:
                    best, best_weight = topic, weight
    return best


def observed_questions(root=None):
    """Real employer questions from a stored answer file. Questions only.

    Policy rules and story-bank entries are excluded: neither is a question an
    employer asked, and counting them would inflate coverage with material the
    taxonomy is not trying to cover.
    """
    root = Path(root).expanduser() if root else Path(
        os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()
    path = root / "answers.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    questions = []
    for key, entry in (payload.items() if isinstance(payload, dict) else []):
        question = entry.get("question") or key
        tags = entry.get("tags") or []
        if "policy" in tags or normalise(question).startswith("policy:"):
            continue
        if normalise(question).startswith("story:"):
            continue
        questions.append(question)
    return questions


def is_excluded(question, payload):
    """True when a stored entry is not an employer question at all.

    Stated as rules with reasons rather than dropped quietly. An exclusion
    nobody can count is indistinguishable from a gap nobody noticed.
    """
    text = normalise(question)
    for rule in (payload.get("excluded") or {}).get("rules") or []:
        if any(normalise(term) in text for term in rule.get("match") or []):
            return rule
    return None


def coverage(questions, payload):
    topics = payload["topics"]
    covered, orphans = {}, []
    for question in questions:
        if is_excluded(question, payload):
            continue
        topic = match_topic(question, topics)
        if topic is None:
            orphans.append(question)
        else:
            covered.setdefault(topic["id"], []).append(question)
    return covered, orphans


def plan(payload):
    """Onboarding order: blocking first, then high frequency, then the tail."""
    ordered = []
    for tier in TIERS:
        group = [t for t in payload["topics"] if t["tier"] == tier]
        # Inside a tier, non-sensitive before sensitive: a person should meet
        # the ordinary questions before being asked to consent to anything.
        group.sort(key=lambda t: (bool(t.get("sensitive")), t["id"]))
        ordered.extend(group)
    return ordered


def main(argv=None):
    parser = argparse.ArgumentParser(description="The onboarding question taxonomy.")
    parser.add_argument("command", choices=["check", "plan", "coverage"],
                        nargs="?", default="check")
    parser.add_argument("--store", help="store directory (default ~/.job-application)")
    parser.add_argument("--taxonomy", help="taxonomy file (default references/questionnaire.json)")
    args = parser.parse_args(argv)

    try:
        payload = load(args.taxonomy)
        assert_no_answers(payload)
    except TaxonomyError as exc:
        print(f"questionnaire: {exc}", file=sys.stderr)
        return 2

    if args.command == "plan":
        for tier in TIERS:
            group = [t for t in plan(payload) if t["tier"] == tier]
            label = {1: "blocking, asked first",
                     2: "high frequency, asked at signup",
                     3: "long tail, captured in Needs you"}[tier]
            print(f"\nTIER {tier} — {label}  ({len(group)})")
            for topic in group:
                marks = []
                if topic.get("sensitive"):
                    marks.append("sensitive")
                if topic.get("perIdentity"):
                    marks.append("per-identity")
                if topic.get("storyBacked"):
                    marks.append("story")
                suffix = f"  [{', '.join(marks)}]" if marks else ""
                print(f"  {topic['id']:32} {topic['ask']}{suffix}")
        prompts = (payload.get("storyBank") or {}).get("prompts") or []
        print(f"\nSTORY BANK — an interview, not a form  ({len(prompts)} prompts, ask two or three)")
        for prompt in prompts:
            print(f"  - {prompt}")
        return 0

    questions = observed_questions(args.store)
    if not questions:
        if args.command == "check":
            print("questionnaire: taxonomy is well formed; no answer store to check it against")
            return 0
        print("questionnaire: no answer store found", file=sys.stderr)
        return 1

    covered, orphans = coverage(questions, payload)
    excluded = [q for q in questions if is_excluded(q, payload)]
    total = len(questions) - len(excluded)
    print(f"observed employer questions : {total}")
    print(f"excluded, not employer asks : {len(excluded)}")
    print(f"topics in taxonomy          : {len(payload['topics'])}")
    print(f"covered                     : {total - len(orphans)}")
    print(f"uncovered                   : {len(orphans)}")

    if args.command == "coverage":
        print("\ncollapse, most-aliased first:")
        for topic_id, matched in sorted(covered.items(), key=lambda kv: -len(kv[1])):
            if len(matched) > 1:
                print(f"  {topic_id:32} {len(matched)} phrasings")
        blocking = [t["id"] for t in payload["topics"] if t["tier"] == 1]
        asked = sum(len(covered.get(topic_id, [])) for topic_id in blocking)
        print(f"\ntier 1 topics: {len(blocking)}, matching {asked} of the {total} observed questions")

    if orphans:
        print("\nUNCOVERED — every one of these needs a topic or an explicit decision:",
              file=sys.stderr)
        for question in orphans:
            print(f"  {question[:100]}", file=sys.stderr)
        return 1
    print("\nevery observed question maps to a topic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
