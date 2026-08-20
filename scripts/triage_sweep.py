#!/usr/bin/env python3
"""Dedupe a sweep result against the store's role index and print newest first."""
import json
import re
import subprocess
import sys


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def known_keys():
    out = subprocess.run(["python3", "scripts/store.py", "roles-list"],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    items = list(d.values()) if isinstance(d, dict) else d
    return ({(norm(r.get("company")), norm(r.get("role"))) for r in items},
            {norm(r.get("company")) for r in items})


def main():
    d = json.load(open(sys.argv[1]))
    rows = d["rows"]
    pairs, companies = known_keys()
    new = [r for r in rows if (norm(r["company"]), norm(r["title"])) not in pairs]
    new.sort(key=lambda r: r["updated"], reverse=True)
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    print("rows=%d afterDedupe=%d" % (len(rows), len(new)))
    for r in new[:limit]:
        seen = "*" if norm(r["company"]) in companies else " "
        print("%s %s%-20.20s | %-60.60s | %-38.38s | %s" % (
            r["updated"], seen, r["company"], r["title"], r["location"], r["url"]))


if __name__ == "__main__":
    main()
