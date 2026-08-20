#!/usr/bin/env python3
"""Work out what to put in a compensation field.

When a posting states a band and then asks what the candidate expects, the
answer is the middle of that band. Not the stored expectation: naming a figure
above a band the employer has already published invites a filter, and naming one
below it leaves money on the table before a conversation has started.

The arithmetic looks trivial and is not. Bands arrive as "$70,000 - $90,000",
"$140K–$180K" with an en dash, "USD 90k to 110k", "Rs. 25,00,000 - 35,00,000" in
lakh grouping, "25-35 LPA", and "up to $150,000". Eyeballing those mid-form is
how a decimal point moves.

  python3 scripts/salary.py --band "$70,000 - $90,000"        -> $80,000
  python3 scripts/salary.py --band "$140K - $180K"            -> $160,000
  python3 scripts/salary.py --band "25-35 LPA"                -> 30 LPA
  python3 scripts/salary.py --region us                        -> the stored expectation

Standard library only.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"

# en dash, em dash and "to" all mean the same thing in a band.
SEPARATOR = re.compile(r"\s*(?:-|–|—|to|through|‒)\s*", re.I)
NUMBER = re.compile(r"(\d[\d, .]*)\s*([kKmM]|lpa|lakhs?|cr|crores?)?", re.I)
CURRENCY = re.compile(r"(₹|Rs\.?|INR|\$|USD|£|GBP|€|EUR|CAD|AUD)", re.I)
MULTIPLIER = {"k": 1_000, "m": 1_000_000, "lpa": 100_000, "lakh": 100_000,
              "lakhs": 100_000, "cr": 10_000_000, "crore": 10_000_000,
              "crores": 10_000_000}


def parse_amount(text):
    """One figure from a fragment, with its magnitude suffix applied."""
    m = NUMBER.search(text or "")
    if not m:
        return None
    digits = m.group(1).replace(",", "").replace(" ", "")
    # A trailing ".00" is a decimal; "1.5" with a k suffix is one and a half
    # thousand. Both are handled by float, and the int() below drops the cents.
    try:
        value = float(digits)
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    return value * MULTIPLIER.get(suffix, 1)


def currency_of(text):
    m = CURRENCY.search(text or "")
    if not m:
        return ""
    token = m.group(1).lower().rstrip(".")
    return {"rs": "₹", "inr": "₹", "₹": "₹", "usd": "$", "$": "$",
            "gbp": "£", "£": "£", "eur": "€", "€": "€",
            "cad": "$", "aud": "$"}.get(token, "")


def indian_grouping(n):
    """1,20,000 rather than 120,000. Getting this wrong on an Indian form
    reads as carelessness on the very field they scrutinise."""
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def midpoint(band):
    """(value, formatted) for the middle of a stated band, or (None, reason)."""
    text = (band or "").strip()
    if not text:
        return None, "no band given"

    lpa = bool(re.search(r"\blpa\b|\blakhs?\b", text, re.I))
    symbol = currency_of(text)
    # "up to X" and "starting at X" are one-sided; the stated figure is the answer.
    one_sided = re.match(r"\s*(up to|upto|max(?:imum)?|starting at|from|min(?:imum)?)\b",
                         text, re.I)

    parts = [p for p in SEPARATOR.split(text) if NUMBER.search(p)]
    amounts = [a for a in (parse_amount(p) for p in parts) if a]
    # A suffix often appears only on the second half: "$140K - $180K" is fine,
    # but "140 - 180K" means both are thousands.
    if len(amounts) == 2 and amounts[1] >= 1000 > amounts[0]:
        scale = 1
        m = NUMBER.search(parts[-1])
        if m and (m.group(2) or "").lower() in MULTIPLIER:
            scale = MULTIPLIER[(m.group(2) or "").lower()]
        amounts[0] *= scale

    if not amounts:
        return None, "no figure found in the band"
    if len(amounts) == 1 or one_sided:
        value = amounts[0]
        note = "one-sided band; using the stated figure"
    else:
        lo, hi = min(amounts[0], amounts[1]), max(amounts[0], amounts[1])
        value = (lo + hi) / 2
        note = f"midpoint of {lo:,.0f} and {hi:,.0f}"

    if lpa:
        pretty = f"{value / 100_000:g} LPA"
    elif symbol == "₹":
        pretty = f"₹{indian_grouping(value)}"
    else:
        pretty = f"{symbol}{int(round(value)):,}"
    return value, {"formatted": pretty, "note": note}


def stored_expectation(region):
    """The fallback when the posting states no band at all."""
    question = ("What is your expected CTC for India roles?" if region == "in"
                else "What is your expected salary for US roles?")
    r = subprocess.run([sys.executable, str(STORE), "answer-find", "--question", question],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return (json.loads(r.stdout or "{}") or {}).get("value")


def main():
    ap = argparse.ArgumentParser(description="What to put in a compensation field.")
    ap.add_argument("--band", help="the range the posting states, verbatim")
    ap.add_argument("--region", choices=["us", "in"], default="us")
    args = ap.parse_args()

    if args.band:
        value, detail = midpoint(args.band)
        if value is None:
            print(json.dumps({"answer": None, "why": detail}, indent=2))
            return 1
        print(json.dumps({"answer": detail["formatted"], "value": value,
                          "band": args.band, "why": detail["note"]},
                         indent=2, ensure_ascii=False))
        return 0

    stored = stored_expectation(args.region)
    print(json.dumps({"answer": stored, "why": "no band stated; the stored expectation"},
                     indent=2, ensure_ascii=False))
    return 0 if stored else 1


if __name__ == "__main__":
    sys.exit(main())
