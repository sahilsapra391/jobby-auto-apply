#!/usr/bin/env python3
"""Merge sweep files, drop out-of-band seniority and geography, dedupe vs store."""
import json
import re
import subprocess
import sys

# Titles whose stated minimum is reliably above the five-year ceiling.
SENIORITY_OUT = re.compile(
    r"\b(staff|principal|lead|director|head|vp|chief|group\s+product|"
    r"distinguished|architect)\b", re.I)
GEO_IN = re.compile(
    r"(united states|u\.s\.|\bUSA\b|\bUS\b|remote - us|new york|san francisco|"
    r"seattle|austin|boston|chicago|denver|atlanta|los angeles|san mateo|"
    r"palo alto|mountain view|sunnyvale|bellevue|menlo park|washington|texas|"
    r"california|massachusetts|illinois|colorado|florida|georgia|virginia|"
    r"north carolina|arizona|utah|oregon|new jersey|pennsylvania|"
    r"india|bengaluru|bangalore|mumbai|delhi|gurugram|gurgaon|noida|hyderabad|"
    r"pune|chennai|kolkata|ahmedabad|"
    r"united kingdom|london|manchester|edinburgh|\bUK\b|"
    r"canada|toronto|vancouver|montreal|ottawa|ontario|british columbia|"
    r"australia|sydney|melbourne|brisbane)", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def known():
    """Role index plus the live watch list: a watched role is not a new find."""
    pairs = set()
    for cmd in ("roles-list", "session-list"):
        out = subprocess.run(["python3", "scripts/store.py", cmd],
                             capture_output=True, text=True).stdout
        d = json.loads(out)
        items = list(d.values()) if isinstance(d, dict) else d
        for r in items:
            if isinstance(r, dict):
                pairs.add((norm(r.get("company")), norm(r.get("role"))))
    return pairs


def main():
    rows = []
    for path in sys.argv[1:]:
        rows.extend(json.load(open(path))["rows"])
    pairs = known()
    seen = set()
    keep = []
    for r in rows:
        k = (norm(r["company"]), norm(r["title"]))
        if k in pairs or k in seen:
            continue
        if SENIORITY_OUT.search(r["title"]):
            continue
        if not GEO_IN.search(r["location"] or ""):
            continue
        seen.add(k)
        keep.append(r)
    keep.sort(key=lambda r: r["updated"], reverse=True)
    json.dump(keep, open(".candidates.json", "w"))
    print("candidates=%d" % len(keep))
    for r in keep:
        print("%s | %-16.16s | %-58.58s | %-34.34s | %s" % (
            r["updated"], r["company"], r["title"], r["location"], r["url"]))


if __name__ == "__main__":
    main()
