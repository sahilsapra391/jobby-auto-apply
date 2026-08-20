#!/usr/bin/env python3
"""The source catalog, and the accounting that keeps "every source" honest.

"Every single source covered" is not a claim anyone can verify by reading code.
It is only meaningful as accounting: every channel in this catalog is either
connected, swept on rotation, blocked for a stated reason, or excluded for a
stated reason. A channel that is none of those is a gap, and this file is what
makes that gap countable rather than invisible.

sources.json is the machine copy; references/sources-registry.md is the one a
human reads. They drift the moment they are maintained separately, so the
markdown's table is generated from the JSON and a test fails when the two
disagree.

  python3 scripts/sources_registry.py summary
  python3 scripts/sources_registry.py render     # rewrite the table in the md
  python3 scripts/sources_registry.py check      # exit 1 if the md is stale
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "sources.json"
DOC = REPO / "references" / "sources-registry.md"
BEGIN = "<!-- generated:sources begin -->"
END = "<!-- generated:sources end -->"

# Every status a channel may hold. A channel outside this set is a bug, not a
# new idea: the whole point is that "absent" is not one of the options.
STATUSES = ("connected", "needs-key", "planned", "rotation", "blocked", "excluded")


class RegistryError(Exception):
    pass


def load(path=None):
    path = Path(path or CATALOG)
    if not path.exists():
        raise RegistryError(f"{path.name} is missing; the source catalog is not optional")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{path.name} is not valid JSON: {exc}")
    sources = doc.get("sources")
    if not isinstance(sources, list) or not sources:
        raise RegistryError(f"{path.name} holds no sources")
    seen = set()
    for entry in sources:
        sid = entry.get("id")
        if not sid:
            raise RegistryError("every source needs an id")
        if sid in seen:
            raise RegistryError(f"duplicate source id {sid}")
        seen.add(sid)
        if entry.get("status") not in STATUSES:
            raise RegistryError(f"{sid} has status {entry.get('status')!r}; "
                                f"expected one of {list(STATUSES)}")
        if entry["status"] in ("blocked", "excluded") and not entry.get("statusReason"):
            raise RegistryError(f"{sid} is {entry['status']} with no reason; "
                                "an unexplained exclusion is how coverage gets quietly lost")
    return doc


def by_id(doc=None):
    return {s["id"]: s for s in (doc or load())["sources"]}


def phase_one_ids(doc=None):
    """Channels Phase 1 is responsible for connecting."""
    return [s["id"] for s in (doc or load())["sources"] if s.get("phase") == 1]


def summary(doc=None):
    doc = doc or load()
    counts, phases = {}, {}
    for entry in doc["sources"]:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        key = f"phase {entry.get('phase')}"
        phases[key] = phases.get(key, 0) + 1
    return {"sources": len(doc["sources"]), "byStatus": counts, "byPhase": phases,
            "unaccountedFor": 0}


def _cell(value):
    """Markdown-safe: a pipe inside a cell silently breaks the table."""
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def render_table(doc=None):
    doc = doc or load()
    rows = ["| Channel | id | Class | Tier | Phase | Status | Regions | Notes |",
            "|---|---|---|---|---|---|---|---|"]
    for entry in sorted(doc["sources"], key=lambda s: (s.get("phase") or 99, s["id"])):
        regions = ", ".join(entry.get("regions") or []) or "-"
        note = entry.get("statusReason") or entry.get("notes") or ""
        if entry.get("verifyAtBuild"):
            note = (note + " **Verify at build.**").strip()
        rows.append("| {} | `{}` | {} | {} | {} | `{}` | {} | {} |".format(
            _cell(entry.get("name")), _cell(entry["id"]), _cell(entry.get("class")),
            _cell(entry.get("tier")), _cell(entry.get("phase")), _cell(entry["status"]),
            _cell(regions), _cell(note)))
    return "\n".join(rows)


def _split(text):
    if BEGIN not in text or END not in text:
        raise RegistryError(f"{DOC.name} has no generated block; expected {BEGIN} … {END}")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return head, tail


def rendered_doc(doc=None, path=None):
    text = Path(path or DOC).read_text(encoding="utf-8")
    head, tail = _split(text)
    return f"{head}{BEGIN}\n\n{render_table(doc)}\n\n{END}{tail}"


def check(doc=None, path=None):
    path = Path(path or DOC)
    return path.read_text(encoding="utf-8") == rendered_doc(doc, path)


def main():
    ap = argparse.ArgumentParser(description="The source catalog and its accounting.")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("summary", "render", "check", "list"):
        sub.add_parser(name)
    args = ap.parse_args()
    try:
        doc = load()
        if args.command == "summary":
            print(json.dumps(summary(doc), indent=2, sort_keys=True))
        elif args.command == "list":
            print(json.dumps([{k: s.get(k) for k in ("id", "status", "phase", "tier")}
                              for s in doc["sources"]], indent=2))
        elif args.command == "render":
            DOC.write_text(rendered_doc(doc), encoding="utf-8")
            print(json.dumps({"rendered": str(DOC), "sources": len(doc["sources"])}, indent=2))
        else:
            if not check(doc):
                print("sources registry: references/sources-registry.md is out of date; "
                      "run python3 scripts/sources_registry.py render", file=sys.stderr)
                return 1
            print(json.dumps({"upToDate": True}, indent=2))
    except RegistryError as exc:
        print(f"sources registry: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
