#!/usr/bin/env python3
"""Fetch each candidate posting's real body and extract its stated minimum years.

The years cap is the filter that rejects most volume, and a title is not a proxy
for it, so this reads the employer's own body text rather than guessing.
"""
import concurrent.futures as cf
import html
import json
import re
import sys
import urllib.parse

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from ats_sweep import get  # noqa: E402

YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?"
    r"(?:[^.]{0,60}?)(?:experience|exp\b)", re.I)
YEARS2 = re.compile(
    r"(?:experience|exp)[^.]{0,40}?(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?",
    re.I)


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def body_for(row):
    ats, url = row["ats"], row["url"]
    jid = row["id"]
    if ats == "greenhouse":
        tok = row["company"]
        d = json.loads(get(
            "https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s" % (tok, jid)))
        return strip_html(d.get("content", ""))
    if ats == "lever":
        d = json.loads(get("https://api.lever.co/v0/postings/%s/%s"
                           % (row["company"], jid)))
        parts = [d.get("descriptionPlain", "")]
        for lst in d.get("lists", []):
            parts.append(strip_html(lst.get("text", "")))
            parts.append(strip_html(lst.get("content", "")))
        parts.append(d.get("additionalPlain", ""))
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p))
    if ats == "ashby":
        org = row["company"]
        d = json.loads(get(
            "https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true"
            % org))
        for j in d.get("jobs", []):
            if j.get("id") == jid:
                return strip_html(j.get("descriptionHtml")
                                  or j.get("descriptionPlain", ""))
    return ""


def min_years(text):
    hits = []
    for rx in (YEARS, YEARS2):
        for m in rx.finditer(text):
            lo = int(m.group(1))
            if 0 < lo <= 25:
                hits.append((lo, text[max(0, m.start() - 90):m.end() + 40]))
    return hits


def main():
    rows = []
    for path in sys.argv[1:]:
        d = json.load(open(path))
        rows.extend(d["rows"] if isinstance(d, dict) else d)

    def one(r):
        try:
            t = body_for(r)
        except Exception as e:  # noqa: BLE001
            return {**r, "err": "%s" % type(e).__name__, "body": ""}
        hits = min_years(t)
        return {**r, "body": t, "yearHits": [h[0] for h in hits],
                "yearCtx": [h[1] for h in hits][:4],
                "minYears": min(h[0] for h in hits) if hits else None,
                "sponsor": bool(re.search(
                    r"(not|unable to)[^.]{0,40}(sponsor|visa)|no visa sponsorship",
                    t, re.I))}

    out = []
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(one, rows):
            out.append(r)
    json.dump(out, open(sys.argv[-1] if False else ".bodies.json", "w"))
    print("fetched=%d errors=%d" % (
        len(out), sum(1 for r in out if r.get("err"))))


if __name__ == "__main__":
    main()
