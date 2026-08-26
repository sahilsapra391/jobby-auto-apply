#!/usr/bin/env python3
"""Candidate ATS tokens for one market, harvested rather than invented.

The registry is the unit of coverage and it is nearly saturated: 836 of its 854
companies already have a token. So the next tenants are not in it, and they are
not in the job pool either. Measured 2026-08-25: of the employers appearing in
the pool with no registry entry, 339 are American and **three** are Indian. A
pool that has few Indian employers cannot be mined for more of them, which is
why India-first discovery has to start outside our own data.

The aggregators can see that market. Adzuna, Careerjet and Jooble all index
Indian postings, and what we want from them is not the postings -- those are
thin copies -- but the **employer names**. A name harvested from a real Indian
job advert is a real Indian company that is hiring right now, which is a far
better probe candidate than a name somebody thought of.

From a name to a token is a guess, and it is meant to be. `probe_tenants.py`
settles it: a candidate either answers with live postings on some platform's
own endpoint or it does not, and a wrong guess costs one 404. Nothing enters
the registry on the strength of the guess.

  python3 scripts/harvest_candidates.py --country in --out candidates-in.txt
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity  # noqa: E402
import jobs_store  # noqa: E402
from sources import base  # noqa: E402

# Legal suffixes carry no information about the token and are almost never in
# one. "Zerodha Broking Ltd" is `zerodha` on every platform that has it.
SUFFIXES = (
    "private limited", "pvt ltd", "pvt. ltd.", "pvt limited", "p ltd",
    "limited", "ltd", "llp", "llc", "inc", "incorporated", "corp",
    "corporation", "co", "company", "gmbh", "bv", "nv", "sa", "ag", "plc",
    "pte ltd", "pte", "fzco", "fz llc", "dmcc", "technologies", "technology",
    "solutions", "services", "labs", "software", "systems", "india",
)

# Names that are not employers. An aggregator's `company` field carries these
# whenever the advert was placed by an agency or the employer was withheld.
NOT_AN_EMPLOYER = re.compile(
    r"confidential|undisclosed|client of|recruit|staffing|consultanc|"
    r"placement|manpower|hiring partner|talent solutions|hr services",
    re.I)


def slug(name):
    """`ascii-lowercase-with-hyphens`, which is what most vendors use."""
    text = unicodedata.normalize("NFKD", name or "")
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().lower()
    words = text.split()
    while words and " ".join(words[-2:]) in SUFFIXES:
        words = words[:-2]
    while words and words[-1] in SUFFIXES:
        words = words[:-1]
    return "-".join(words)


# Half these platforms address a tenant as `https://{token}.vendor.com`, so a
# token is a DNS label before it is anything else: 63 characters, and no
# leading or trailing hyphen. Two harvested names exceeded it -- one was an
# agency's 74-character strapline -- and the resulting UnicodeEncodeError
# killed six of the twelve probe threads outright.
MAX_LABEL = 63


def usable(token):
    return (3 <= len(token) <= MAX_LABEL
            and not token.startswith("-") and not token.endswith("-"))


def candidates(name):
    """The token spellings worth one request each, most likely first.

    Deliberately few. Every extra spelling multiplies the probe budget by the
    number of platforms, and the third-best guess for a name almost never wins
    when the first two have already lost.
    """
    base_slug = slug(name)
    if not base_slug or len(base_slug) < 3:
        return []
    out = [base_slug]
    joined = base_slug.replace("-", "")
    if joined != base_slug and len(joined) >= 3:
        out.append(joined)
    # The first word alone is deliberately NOT a candidate. It is the highest
    # yield spelling and the only unsafe one: "Federal Deposit Insurance
    # Corporation" becomes `federal`, and if some unrelated company holds that
    # token the probe answers 200 and we file their jobs under the wrong
    # employer. A wrong token is worse than a missing one, because it is a
    # confident wrong answer that every later sweep repeats. The same guess was
    # tried for logos on 2026-08-25 and produced federal.ai and bureau.ai; a
    # wrong logo is a blemish, a wrong employer is bad data.
    #
    # It could be made safe by confirming the board's own company name against
    # the candidate, which is worth doing when there is a name to compare. The
    # posting endpoints these probes use do not return one.
    return [t for t in out if usable(t)]


def harvest(fetcher, country, terms, config, limit_per_term=None):
    """Employer names an aggregator can see in this market."""
    from sources import adzuna, careerjet, jooble  # noqa: PLC0415

    names, notes = {}, []
    for label, module, supported in (("adzuna", adzuna, country in adzuna.COUNTRIES),
                                     ("careerjet", careerjet, True),
                                     ("jooble", jooble, True)):
        if not supported:
            notes.append(f"{label}: does not index {country.upper()}")
            continue
        try:
            # `countries` is its own argument, not a config key: passing the
            # market inside config silently searched the default country and
            # returned 200 postings, none of them in the market asked for.
            result = module.fetch(fetcher, config=dict(config, keywords=terms),
                                  countries=[country.upper()], limit=limit_per_term)
        except Exception as exc:                     # noqa: BLE001 - reported
            notes.append(f"{label}: {type(exc).__name__}: {exc}"[:140])
            continue
        if result.get("status") != "ok":
            notes.append(f"{label}: {result.get('reason')}")
            continue
        kept = 0
        for posting in result["postings"]:
            company = (posting.get("company") or "").strip()
            in_market = ((posting.get("location") or {}).get("country") or "").upper()
            if not company or in_market != country.upper():
                continue
            if NOT_AN_EMPLOYER.search(company):
                continue
            names.setdefault(company, label)
            kept += 1
        notes.append(f"{label}: {len(result['postings'])} postings, {kept} in {country.upper()}")
    return names, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--country", required=True, help="ISO-2, e.g. in")
    ap.add_argument("--store", help="store directory")
    ap.add_argument("--out", help="write candidate tokens here, one per line")
    ap.add_argument("--terms", default="product,program,engineering,manager,analyst,sales",
                    help="comma-separated search terms")
    ap.add_argument("--limit", type=int, default=200, help="postings per aggregator term")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.store)
    jobs = jobs_store.load_jobs(root)
    companies = jobs_store.load_companies(root)
    index = identity.Index(jobs, companies)
    known = {identity.norm_company(c.get("name"), index.alias_map) for c in companies.values()}
    known_tokens = {c.get("token") for c in companies.values() if c.get("token")}

    fetcher = base.Fetcher(root=root)
    names, notes = harvest(fetcher, args.country.lower(),
                           [t.strip() for t in args.terms.split(",") if t.strip()],
                           {}, args.limit)

    fresh = {n: src for n, src in names.items()
             if identity.norm_company(n, index.alias_map) not in known}
    tokens = {}
    for name in sorted(fresh):
        for token in candidates(name):
            if token not in known_tokens:
                tokens.setdefault(token, name)

    report = {"country": args.country.upper(), "notes": notes,
              "employersSeen": len(names), "alreadyKnown": len(names) - len(fresh),
              "newEmployers": len(fresh), "candidateTokens": len(tokens),
              "requests": fetcher.requests}
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.out:
        Path(args.out).write_text("".join(f"{t}\n" for t in sorted(tokens)))
        Path(args.out).with_suffix(".names.json").write_text(
            json.dumps(tokens, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
