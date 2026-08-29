#!/usr/bin/env python3
"""Warn when the live answer store has outgrown the question categoriser.

This was a test, and it read the live store, so it failed on somebody else's
application run rather than on a change to this repo. It was red for every
session that ran the suite, and a permanently red test teaches everyone to
ignore red, which this project has already paid for once.

Growing live data is a health signal, not an invariant. So it warns and always
exits 0. The invariant, that a frozen sample still categorises, lives in
tests/test_answer_categories.py against tests/fixtures/answer_questions.json.

  python3 scripts/lint_answer_categories.py
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import answer_categories as ac  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "answer_questions.json"


def main():
    root = Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()
    store = root / "answers.json"
    if not store.exists():
        print("answer categories: no answer store on this machine, nothing to check")
        return 0

    recorded = json.loads(FIXTURE.read_text())["knownGaps"]
    known = {e["question"] for e in recorded["englishQuestionsWithNoCategory"]}
    known |= {e["question"] for e in recorded["nonLatinScript"]}

    answers = json.loads(store.read_text())
    questions = [v for v in answers.values() if ac.kind_of(v) == ac.KIND_QUESTION]
    uncategorised = [v.get("question") for v in questions
                     if ac.category_of(v) == ac.UNCATEGORISED and v.get("question")]
    fresh = sorted(set(uncategorised) - known)

    print(f"answer categories: {len(questions) - len(uncategorised)} of "
          f"{len(questions)} employer questions land in a category")
    if known:
        print(f"  known gaps, already recorded: {len(known)}")
    if fresh:
        print(f"  NEW questions with no category: {len(fresh)}")
        for question in fresh[:10]:
            print(f"    {question[:88]}")
        print(f"  add a category, or add these to knownGaps in "
              f"{FIXTURE.relative_to(REPO)} so they stay visible")
    # Always 0: this reports on the world, and the world changing is not a fault.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
