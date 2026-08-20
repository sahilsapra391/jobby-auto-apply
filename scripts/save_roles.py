#!/usr/bin/env python3
"""Save selected verified roles into the store as watch-list sessions.

Reads .bodies.json (bodies already pulled from each employer's own ATS API this
run) plus a picks file of URLs, re-confirms each posting is still live, resolves
a true first-published date where the platform exposes one, and writes one
session per role with an extraction complete enough that an apply run never has
to open the posting again.
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ats_sweep import get  # noqa: E402

COMP = re.compile(
    r"(\$\s?\d{2,3},\d{3}\s*(?:-|–|to)\s*\$?\s?\d{2,3},\d{3}"
    r"|₹\s?[\d,]{5,}\s*(?:-|–|to)\s*₹?\s?[\d,]{5,}"
    r"|\d{1,3}\s*(?:-|–|to)\s*\d{1,3}\s*LPA)", re.I)
NAMES = {
    "scaleai": "Scale AI", "gleanwork": "Glean", "grafanalabs": "Grafana Labs",
    "togetherai": "Together AI", "matchgroup": "Match Group",
    "1password": "1Password", "mongodb": "MongoDB", "openai": "OpenAI",
    "zscaler": "Zscaler", "phonepe": "PhonePe", "scribe": "Scribe",
    "langchain": "LangChain", "perplexity": "Perplexity", "hex": "Hex",
    "datadog": "Datadog", "databricks": "Databricks", "anthropic": "Anthropic",
    "cresta": "Cresta", "harvey": "Harvey", "decagon": "Decagon",
    "sierra": "Sierra", "gamma": "Gamma", "benchling": "Benchling",
    "vanta": "Vanta", "ramp": "Ramp", "plaid": "Plaid", "affirm": "Affirm",
    "chime": "Chime", "robinhood": "Robinhood", "checkr": "Checkr",
    "druva": "Druva", "coinbase": "Coinbase", "elastic": "Elastic",
    "verkada": "Verkada", "cohere": "Cohere", "brex": "Brex",
    "instacart": "Instacart", "faire": "Faire", "gusto": "Gusto",
    "asana": "Asana", "discord": "Discord", "samsara": "Samsara",
    "everlaw": "Everlaw", "rubrik": "Rubrik", "turing": "Turing",
    "stripe": "Stripe", "figma": "Figma",
}
REGION = [
    (re.compile(r"india|bengaluru|bangalore|mumbai|pune|gurugram|gurgaon|"
                r"hyderabad|delhi|noida|chennai", re.I), "IN"),
    (re.compile(r"united kingdom|london|manchester|\bUK\b", re.I), "UK"),
    (re.compile(r"canada|toronto|vancouver|montreal|ontario|british columbia",
                re.I), "Canada"),
    (re.compile(r"australia|sydney|melbourne", re.I), "AU"),
]


def slug(company, role):
    s = re.sub(r"[^a-z0-9]+", "-", ("%s %s" % (company, role)).lower())
    return s.strip("-")[:60]


def region_of(loc):
    for rx, tag in REGION:
        if rx.search(loc or ""):
            return tag
    return "US"


def first_published(row):
    """Greenhouse exposes the real publish date; Lever/Ashby dates already are."""
    if row["ats"] != "greenhouse":
        return row["updated"], None
    try:
        d = json.loads(get("https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s"
                           % (row["company"], row["id"])))
    except Exception:  # noqa: BLE001
        return None, None
    fp = (d.get("first_published") or "")[:10]
    up = (d.get("updated_at") or "")[:10]
    return (fp or up or None), up


def main():
    bodies = {r["url"]: r for r in json.load(open(".bodies.json"))}
    picks = [ln.strip() for ln in open(sys.argv[1]) if ln.strip()
             and not ln.startswith("#")]
    rows = []
    for u in picks:
        if u not in bodies:
            print("MISSING from bodies: %s" % u)
            continue
        rows.append(bodies[u])

    def confirm(r):
        posted, updated = first_published(r)
        return r, posted, updated

    saved, failed = [], []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(confirm, rows))

    for r, posted, updated in results:
        company = NAMES.get(r["company"], r["company"].title())
        role = r["title"].strip()
        body = r["body"]
        if not body or len(body) < 300:
            failed.append("%s / %s: body vanished on re-check" % (company, role))
            continue
        comp = COMP.search(body)
        sess = {
            "company": company,
            "role": role,
            "url": r["url"],
            "ats": r["ats"],
            "region": region_of(r["location"]),
            "source": "%s-api" % r["ats"],
            "status": "watch",
            "postedDate": posted,
            "postedAgo": ("board updated %s" % updated) if updated else None,
            "extraction": {
                "title": role,
                "company": company,
                "location": r["location"],
                "minYearsStated": r.get("minYears"),
                "yearsContext": (r.get("yearCtx") or [])[:2],
                "compensation": comp.group(0) if comp else None,
                "sponsorshipCaveat": bool(r.get("sponsor")),
                "verifiedLiveOn": "2026-08-20",
                "verifiedVia": "%s public job-board API returned this posting"
                               % r["ats"],
                "body": body[:9000],
            },
        }
        sess = {k: v for k, v in sess.items() if v is not None}
        sid = slug(company, role)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         dir=".") as fh:
            json.dump(sess, fh)
            tmp = fh.name
        p = subprocess.run(["python3", "scripts/store.py", "session-save",
                            "--id", sid, "--input", tmp],
                           capture_output=True, text=True)
        os.unlink(tmp)
        if p.returncode:
            failed.append("%s: %s" % (sid, p.stderr.strip()[:160]))
        else:
            saved.append((sid, posted, company, role))

    for sid, posted, company, role in saved:
        print("saved %s | %s | %s - %s" % (posted, sid, company, role))
    print("\nSAVED=%d FAILED=%d" % (len(saved), len(failed)))
    for f in failed:
        print("  FAIL", f)


if __name__ == "__main__":
    main()
