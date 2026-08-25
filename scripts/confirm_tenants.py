#!/usr/bin/env python3
"""Confirm a probed token is the company we think it is, in the market we want.

`probe_tenants.py` answers one question: does this token have a live board?
That is not enough to put it in the registry. A token is a short common word
often enough that some other company holds it, and the probe cannot tell the
difference: `pinpoint/accenture` answered with three postings, and Accenture
does not run its hiring on a three-job Pinpoint board.

A wrong token is worse than a missing one. It is a confident wrong answer that
every later sweep repeats, filing somebody else's jobs under a name the user
recognises. So two things have to hold before a token is kept:

  identity  the board's own text names the company we harvested. Vendors do
            not agree on where the name lives, so this reads the whole body
            rather than one field, which is crude and, for a yes/no, enough.

  market    the board carries at least one posting in the market this run is
            for. A tenant harvested from an Indian advert whose board turns
            out to be entirely British is not wrong, but it is not what
            India-first discovery was for, and it is reported separately
            rather than quietly counted as a win.

  python3 scripts/confirm_tenants.py --found probed-in.json \\
      --names cand-in.names.json --market IN --out confirmed-in.json
"""

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_tenants import PLATFORMS, UA, READ_CAP  # noqa: E402

PER_HOST_DELAY = 1.0

# What "in this market" looks like in a job board's own words. Country name,
# the ISO code as a standalone token, and the cities that carry most of the
# hiring. Cities matter because plenty of boards write "Bengaluru, KA" and
# never say India at all.
MARKETS = {
    "IN": ("india", "bengaluru", "bangalore", "hyderabad", "mumbai", "pune",
           "chennai", "gurgaon", "gurugram", "noida", "delhi", "kolkata",
           "ahmedabad", "kochi", "coimbatore", "jaipur", "indore"),
    "SG": ("singapore",),
    "AE": ("united arab emirates", "dubai", "abu dhabi", "sharjah", "uae"),
}

# Words too generic to prove identity on their own. A board mentioning
# "services" or "global" says nothing about which company it belongs to.
WEAK = frozenset((
    "the", "and", "of", "for", "group", "global", "india", "services",
    "service", "solutions", "systems", "technologies", "technology", "labs",
    "software", "consulting", "consultants", "international", "company",
    "limited", "ltd", "inc", "corp", "corporation", "pvt", "private",
    "holdings", "ventures", "digital", "health", "healthcare", "care",
))


def words(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return [w for w in re.split(r"[^A-Za-z0-9]+", text.lower()) if w]


def fetch(platform, token):
    build, _ = PLATFORMS[platform]
    url = build(urllib.parse.quote(token, safe=""))
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                return None
            return response.read(READ_CAP)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            TimeoutError, ValueError):
        return None


def confirm(body, company, market):
    """(identity, market) as booleans, plus which words carried the identity."""
    text = " ".join(words(body.decode("utf-8", "replace")))
    strong = [w for w in words(company) if w not in WEAK and len(w) > 2]
    hits = [w for w in strong if re.search(rf"\b{re.escape(w)}\b", text)]
    # Every distinctive word, not any. "Sonic Automotive" must not be confirmed
    # by a board that merely says "sonic".
    identity = bool(strong) and len(hits) == len(strong)
    # Whole words only. Substring matching put Dungarvin and Thompson Thrift
    # in the India column because both operate in *Indiana*, which contains
    # "india" -- a false positive that reads exactly like a real find and would
    # have seeded two American boards as Indian coverage.
    in_market = any(re.search(rf"\b{re.escape(term)}\b", text)
                    for term in MARKETS.get(market, ()))
    return identity, in_market, hits, strong


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--found", required=True)
    ap.add_argument("--names", required=True)
    ap.add_argument("--market", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    found = json.loads(Path(args.found).read_text())
    names = json.loads(Path(args.names).read_text())

    kept, rejected, off_market = [], [], []
    last = {}
    for row in sorted(found, key=lambda r: -r["jobs"]):
        platform, token = row["platform"], row["token"]
        company = names.get(token) or token
        wait = PER_HOST_DELAY - (time.monotonic() - last.get(platform, 0))
        if wait > 0:
            time.sleep(wait)
        body = fetch(platform, token)
        last[platform] = time.monotonic()
        if body is None:
            rejected.append(dict(row, company=company, why="board no longer answers"))
            continue
        identity, in_market, hits, strong = confirm(body, company, args.market)
        record = dict(row, company=company, matched=hits, expected=strong,
                      inMarket=in_market)
        if not identity:
            record["why"] = (f"the board never names {company!r}; "
                             f"matched {hits} of {strong}")
            rejected.append(record)
        elif not in_market:
            record["why"] = f"real board, but nothing in {args.market}"
            off_market.append(record)
        else:
            kept.append(record)

    report = {"market": args.market, "confirmed": kept,
              "offMarket": off_market, "rejected": rejected}
    print(f"confirmed {len(kept)}  off-market {len(off_market)}  "
          f"rejected {len(rejected)}  of {len(found)}\n")
    for row in kept:
        print(f"  KEEP     {row['platform']:<16} {row['token']:<22} "
              f"{row['jobs']:>5}  {row['company'][:30]}")
    for row in off_market:
        print(f"  OFF-MKT  {row['platform']:<16} {row['token']:<22} "
              f"{row['jobs']:>5}  {row['company'][:30]}")
    for row in rejected:
        print(f"  REJECT   {row['platform']:<16} {row['token']:<22} "
              f"{row['jobs']:>5}  {row.get('why','')[:60]}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
