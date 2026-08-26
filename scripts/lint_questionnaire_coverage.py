#!/usr/bin/env python3
"""Warn when the live answer corpus has outgrown the question taxonomy.

This was a test, and it read the live store. It therefore failed on somebody
else's application run rather than on a change to this repo, which made it red
for every session that ran the suite. A permanently red test teaches everyone
to ignore red, and this project has already paid for that once.

Growing live data is a health signal, not an invariant. So it warns and always
exits 0. The invariant -- that the taxonomy covers a frozen sample -- lives in
tests/test_questionnaire.py against tests/fixtures/observed_questions.json.

  python3 scripts/lint_questionnaire_coverage.py
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import questionnaire  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "observed_questions.json"


def main():
    root = Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()
    if not (root / "answers.json").exists():
        print("questionnaire coverage: no answer store on this machine, nothing to check")
        return 0

    known = set(json.loads(FIXTURE.read_text()).get("knownOrphans", []))
    questions = questionnaire.observed_questions(root)
    covered, orphans = questionnaire.coverage(questions, questionnaire.load())
    fresh = sorted(set(orphans) - known)

    print(f"questionnaire coverage: {len(covered)} of {len(questions)} observed "
          f"questions map to a topic")
    if known:
        print(f"  known gaps, already recorded: {', '.join(sorted(known))}")
    if fresh:
        print(f"  NEW gaps employers are asking about: {', '.join(fresh)}")
        print("  add a topic, or add these to knownOrphans in "
              f"{FIXTURE.relative_to(REPO)} so they stay visible")
    # Always 0: this reports on the world, and the world changing is not a fault.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
