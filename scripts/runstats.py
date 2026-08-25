#!/usr/bin/env python3
"""What each application run actually cost, from the agent transcripts.

timing.py answers how long a run took by reconstructing it from the store. This
answers where the time went inside it, which is the part that decides whether
another change is worth making.

Three numbers per run, and nothing else, because the questions it exists to
settle are specific:

  calls        total tool calls, and wall time, to settle whether a run is the
               13 minutes projected from trimming or the 14.8 measured before it
  browser/work how much is the employer's form and how much is us. Browser calls
               are bounded by the site; tailoring calls are ours to cut. If the
               remaining time is nearly all browser, there is nothing left to win
  corrections  runs where a template-built document had to be fixed. Extracting
               and re-rendering the 29 existing resumes proves the template
               reproduces what it was built from; it cannot prove anything about
               a posting nobody has tailored for yet, and that is the risk

    python3 scripts/runstats.py                  # summary of every run found
    python3 scripts/runstats.py --since 2026-08-20T15:00:00Z
    python3 scripts/runstats.py --append         # also write docs/runstats.jsonl

Standard library only.
"""

import argparse
import datetime as dt
import json
import re
import statistics as st
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
def transcripts_dir():
    """Where Claude Code keeps this project's session transcripts.

    The directory name is the absolute path with every character that is not
    alphanumeric flattened to a dash, so ".nosync" in the path becomes "-nosync"
    rather than surviving as a dot.
    """
    import re as _re
    slug = _re.sub(r"[^A-Za-z0-9]", "-", str(REPO))
    return Path.home() / ".claude" / "projects" / slug


TRANSCRIPTS = transcripts_dir()
LEDGER = REPO / "docs" / "runstats.jsonl"

RUN_MARKER = "apply to exactly ONE job"
AID = re.compile(r"APPLICATION ID:\s*\\?\"?([a-z0-9][a-z0-9-]{6,})")
OURS = re.compile(r"scripts/(\w+)\.py")


def parse_runs(since=None):
    if not TRANSCRIPTS.is_dir():
        raise SystemExit(f"runstats: no transcripts at {TRANSCRIPTS}")
    runs = []
    for f in sorted(TRANSCRIPTS.glob("*.jsonl")):
        if f.stat().st_size < 20000:
            continue
        text = f.read_text(errors="replace")
        if RUN_MARKER not in text:
            continue
        # The marker has to be the transcript's OPENING message, and this is
        # stricter than it looks on purpose. Do not relax it to "contains the
        # marker anywhere".
        #
        # The prompt run_apply.py sends is a string inside run_apply.py. Any
        # session that reads that file, greps it, or reviews a diff touching it
        # has the marker in its transcript. Those sessions run for hours and
        # make hundreds of tool calls, so counting one as an application does
        # not add noise, it moves the median: including a single one of them
        # took this sample from 94 runs to 95 and the browser share from 45.5%
        # to 39%, which would have made a before-and-after comparison mean
        # nothing.
        if not opens_with_the_runner_prompt(text):
            continue
        found = AID.search(text)
        if not found:
            continue
        run = collect(found.group(1), text, f.name[:8])
        if run and (not since or (run["startedAt"] or "") >= since):
            runs.append(run)
    return sorted(runs, key=lambda r: r["startedAt"] or "")


def opens_with_the_runner_prompt(text):
    """True when the first user message is the runner's prompt."""
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "user" and (record.get("message") or {}).get("role") != "user":
            continue
        content = (record.get("message") or {}).get("content")
        if isinstance(content, str):
            return RUN_MARKER in content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return RUN_MARKER in (block.get("text") or "")
            return False
    return False


def collect(aid, text, short):
    first = last = None
    browser = tailoring = other = 0
    scripts = {}
    renders = template_renders = 0
    verify_failures = 0

    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        stamp = record.get("timestamp")
        if stamp:
            try:
                t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                first = first or t
                last = t
            except ValueError:
                pass
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                body = json.dumps(block.get("content"))[:400]
                if "differences" in body and '"match": false' in body.replace(" ", ""):
                    verify_failures += 1
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name") or ""
            if name.startswith("mcp__browser__"):
                browser += 1
                continue
            if name == "Bash":
                command = (block.get("input") or {}).get("command", "")
                hit = OURS.search(command)
                if hit:
                    scripts[hit.group(1)] = scripts.get(hit.group(1), 0) + 1
                    if hit.group(1) == "resume_pdf" and " render" in command:
                        renders += 1
                    if hit.group(1) == "resume_build" and " render" in command:
                        template_renders += 1
                    tailoring += 1
                else:
                    other += 1
                continue
            if name in ("Write", "Edit", "Read"):
                tailoring += 1
            else:
                other += 1

    if not first:
        return None
    minutes = round((last - first).total_seconds() / 60, 1)
    total = browser + tailoring + other
    return {
        "applicationId": aid,
        "transcript": short,
        "startedAt": first.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minutes": minutes,
        "calls": total,
        "browserCalls": browser,
        "tailoringCalls": tailoring,
        "otherCalls": other,
        "pdfRenders": renders,
        "templateRenders": template_renders,
        # NOT template renders. Rendering the template again is the cheap
        # iteration the design is built around: edit the overrides, re-render
        # the HTML in milliseconds, re-check the content gate, and only put it
        # through Chrome once at the end. Counting those as corrections said 7
        # of 10 runs had a document go wrong when the truth was 0: the same
        # runs had one PDF render each and not a single layout failure.
        #
        # A correction is the expensive kind: the layout gate rejected the page,
        # or the PDF had to be produced more than the two a resume and a cover
        # letter need.
        "documentNeededCorrection": verify_failures > 0 or renders > 2,
        "verifyFailures": verify_failures,
        "scripts": scripts,
    }


def summarise(runs):
    if not runs:
        return {"runs": 0}
    def med(key):
        values = [r[key] for r in runs if r.get(key) is not None]
        return round(st.median(values), 1) if values else None
    browser = sum(r["browserCalls"] for r in runs)
    tailoring = sum(r["tailoringCalls"] for r in runs)
    other = sum(r["otherCalls"] for r in runs)
    total = browser + tailoring + other
    return {
        "runs": len(runs),
        "medianMinutes": med("minutes"),
        "medianCalls": med("calls"),
        "medianBrowserCalls": med("browserCalls"),
        "medianTailoringCalls": med("tailoringCalls"),
        "shareBrowser": round(browser / total * 100, 1) if total else None,
        "shareTailoring": round(tailoring / total * 100, 1) if total else None,
        "shareOther": round(other / total * 100, 1) if total else None,
        "usedTemplate": sum(1 for r in runs if r["templateRenders"]),
        "documentsNeedingCorrection": sum(1 for r in runs if r["documentNeededCorrection"]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", help="ISO stamp, e.g. 2026-08-20T15:00:00Z")
    ap.add_argument("--runs", action="store_true", help="one line per run")
    ap.add_argument("--append", action="store_true",
                    help="append every run to docs/runstats.jsonl, skipping ones "
                         "already recorded, so the numbers outlive the transcripts")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    runs = parse_runs(args.since)
    summary = summarise(runs)

    if args.append:
        seen = set()
        if LEDGER.exists():
            for line in LEDGER.read_text().splitlines():
                try:
                    seen.add(json.loads(line)["transcript"])
                except (json.JSONDecodeError, KeyError):
                    pass
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        added = 0
        with open(LEDGER, "a") as fh:
            for run in runs:
                if run["transcript"] in seen:
                    continue
                fh.write(json.dumps(run, sort_keys=True) + "\n")
                added += 1
        summary["appendedToLedger"] = added

    if args.json:
        print(json.dumps({"summary": summary, "runs": runs}, indent=2))
        return

    for key, value in summary.items():
        print(f"  {key:28} {value}")
    if args.runs:
        print(f"\n  {'min':>5} {'calls':>6} {'brow':>5} {'tail':>5} {'tpl':>4}  application")
        for r in runs:
            print(f"  {r['minutes']:>5.1f} {r['calls']:>6} {r['browserCalls']:>5} "
                  f"{r['tailoringCalls']:>5} {r['templateRenders']:>4}  "
                  f"{r['applicationId'][:44]}")


if __name__ == "__main__":
    main()
