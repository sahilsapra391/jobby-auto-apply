#!/usr/bin/env python3
"""Has this posting been through here before?

A sourcing sweep finds the same job many times: the company's own board, an
aggregator that copied it, LinkedIn's index, and a rescan a week later. Without
a check every one of those becomes another watch entry, and the same job gets
applied to twice under the user's name.

The scan prompt already said not to re-add known roles. Saying it is not a
guard: the agent doing the sourcing is the same agent deciding what counts as
already known, and it is reading three separate lists to decide. This makes the
decision once, from every record the system keeps:

  roles.json           every role ever seen, kept after its session is deleted
  sessions/            watching, in flight, needs you, and dropped
  applications.jsonl   everything applied to, including retracted marks

A posting is known if ANY of these match something on record:

  the application id
  the URL, once tracking and host noise are stripped
  the ATS job key inside the URL, paired with the company
  the company and role, both normalised

The last one is what catches the same job arriving from two sources, since the
LinkedIn copy and the Greenhouse original share no URL at all. Matching is exact
after normalisation, never fuzzy: "Product Manager, Android Maps" and "Product
Manager, Android Auto" are different jobs, and a prefix rule would merge them.

    python3 scripts/seen.py --check --company Acme --role "Product Manager" --url ...
    python3 scripts/seen.py --filter --input candidates.json

Standard library only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import store  # noqa: E402

# The identifier a posting carries inside its own ATS URL. Two links to one job
# differ in host, tracking and casing but agree here.
ATS_KEYS = [
    re.compile(r"/jobs?/(\d{4,})"),                       # greenhouse, lever numeric
    re.compile(r"[_-](R-?\d{4,})", re.I),                 # workday requisition
    re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I),
    re.compile(r"[?&]gh_jid=(\d+)"),                      # greenhouse embedded
    re.compile(r"/job/[^/]*?(\d{6,})"),                   # icims and friends
]


def norm(value):
    """Lower case, punctuation to spaces, runs collapsed."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def norm_role(value):
    """A role title, with the noise that varies between listings removed.

    The same job is posted as "Senior Product Manager - AI", "Senior Product
    Manager, AI" and "Senior Product Manager (AI)". Those are one job. Seniority
    words are NOT removed: "Product Manager" and "Senior Product Manager" are
    different jobs and often both open at once.
    """
    text = norm(value)
    text = re.sub(r"\b(remote|hybrid|onsite|on site|full time|contract|f t|us|usa|india)\b",
                  " ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_url(value):
    """Strip everything that varies between two links to one posting."""
    u = (value or "").split("#")[0].strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    # Query strings on job links are tracking, with one exception that carries
    # the job id, and that is picked up by ats_key instead.
    u = u.split("?")[0]
    return u.rstrip("/")


def ats_key(value):
    for pattern in ATS_KEYS:
        found = pattern.search(value or "")
        if found:
            return found.group(1).lower()
    return ""


def url_identifies_posting(value):
    """Whether the URL, stripped of its query, still names one posting.

    norm_url drops the query, and for most links that only sheds tracking. But
    when the job id itself rides the query (?gh_jid=... on an employer's own
    careers page), the stripped URL is the page every posting there shares:
    stripe.com/jobs/search covered 258 different Stripe postings, and one
    recorded Stripe role declared all of them already known. Measured
    2026-08-28: 4,202 of 14,569 pool jobs carry their identity in the query.
    Such a URL must sit out URL matching entirely; the ATS key carries it.
    A path-borne id (boards.greenhouse.io/acme/jobs/123) survives the strip,
    so those keep matching by URL as before.
    """
    raw_key = ats_key(value or "")
    return not raw_key or ats_key(norm_url(value)) == raw_key


def known_records(root=None):
    """Everything on record, from all three places, as match keys."""
    paths = store.paths(store.root_dir(root))
    ids, urls, keys, pairs = set(), set(), set(), set()
    detail = {}

    def remember(aid, company, role, url, where):
        if aid:
            ids.add(aid)
            detail.setdefault(aid, where)
        u = norm_url(url)
        k = ats_key(url)
        if u and url_identifies_posting(url):
            urls.add(u)
            detail.setdefault("url:" + u, where)
        if k and company:
            pair = (norm(company), k)
            keys.add(pair)
            detail.setdefault("key:%s:%s" % pair, where)
        if company and role:
            pair = (norm(company), norm_role(role))
            pairs.add(pair)
            detail.setdefault("pair:%s:%s" % pair, where)

    roles = store.read_json(Path(paths["roles"]), {}) or {}
    for aid, entry in roles.items():
        remember(aid, entry.get("company"), entry.get("role"), entry.get("url"),
                 "the role index")

    sessions = Path(paths["sessions"])
    if sessions.is_dir():
        for f in sessions.glob("*.json"):
            entry = store.read_json(f, {}) or {}
            status = entry.get("status") or "watch"
            remember(entry.get("applicationId") or f.stem, entry.get("company"),
                     entry.get("role"), entry.get("url"),
                     f"the watch list (status {status})")

    history = Path(paths["history"])
    if history.exists():
        for line in history.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A truncated line must not quietly shrink what we know about.
                raise SystemExit("seen: applications.jsonl has an unreadable line, so "
                                 "what has been applied to cannot be established. "
                                 "Refusing to answer rather than answering wrongly.")
            remember(event.get("applicationId"), event.get("company"),
                     event.get("role"), event.get("url"), "already applied to")

    return {"ids": ids, "urls": urls, "keys": keys, "pairs": pairs, "detail": detail}


def check(candidate, records):
    """Why this posting is already known, or nothing."""
    aid = candidate.get("applicationId") or candidate.get("id")
    company = candidate.get("company")
    role = candidate.get("role") or candidate.get("title")
    url = candidate.get("url")

    if aid and aid in records["ids"]:
        return {"known": True, "reason": "same application id",
                "where": records["detail"].get(aid, "on record"), "match": aid}
    # A candidate whose identity rides its query strips to the careers page
    # every posting at that employer shares, so the URL test is skipped for it
    # in both directions: known_records no longer stores such URLs, and this
    # skip covers records written before 2026-08-28 that already hold them.
    u = norm_url(url)
    k = ats_key(url)
    if u and url_identifies_posting(url) and u in records["urls"]:
        return {"known": True, "reason": "same posting URL",
                "where": records["detail"].get("url:" + u, "on record"), "match": u}
    if k and company and (norm(company), k) in records["keys"]:
        return {"known": True, "reason": "same job on the same ATS, different link",
                "where": records["detail"].get("key:%s:%s" % (norm(company), k),
                                               "on record"), "match": k}
    if company and role:
        pair = (norm(company), norm_role(role))
        if pair in records["pairs"]:
            return {"known": True,
                    "reason": "same company and role, arriving from another source",
                    "where": records["detail"].get("pair:%s:%s" % pair, "on record"),
                    "match": " / ".join(pair)}
    return {"known": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="one posting")
    mode.add_argument("--filter", action="store_true",
                      help="a JSON array of candidates; prints only the new ones")
    ap.add_argument("--company")
    ap.add_argument("--role")
    ap.add_argument("--url")
    ap.add_argument("--id")
    ap.add_argument("--input", help="JSON array, or - for stdin")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    args = ap.parse_args()

    records = known_records(args.root)

    if args.check:
        if not (args.company or args.url or args.id):
            ap.error("--check needs at least --company with --role, or --url, or --id")
        verdict = check({"company": args.company, "role": args.role,
                         "url": args.url, "applicationId": args.id}, records)
        print(json.dumps(verdict, indent=2))
        return 1 if verdict["known"] else 0

    raw = sys.stdin.read() if args.input in (None, "-") else Path(args.input).read_text()
    try:
        candidates = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"seen: --filter needs a JSON array of candidates ({exc})")
    if not isinstance(candidates, list):
        raise SystemExit("seen: --filter needs a JSON array")

    fresh, skipped = [], []
    # Candidates are also matched against each other, so one sweep returning the
    # same job from two boards keeps it once.
    for candidate in candidates:
        verdict = check(candidate, records)
        if verdict["known"]:
            skipped.append({**{k: candidate.get(k) for k in ("company", "role", "url")},
                            **verdict})
            continue
        fresh.append(candidate)
        aid = candidate.get("applicationId") or candidate.get("id")
        if aid:
            records["ids"].add(aid)
        u = norm_url(candidate.get("url"))
        if u:
            records["urls"].add(u)
            records["detail"].setdefault("url:" + u, "another find in this same sweep")
        k = ats_key(candidate.get("url"))
        if k and candidate.get("company"):
            records["keys"].add((norm(candidate["company"]), k))
        if candidate.get("company") and (candidate.get("role") or candidate.get("title")):
            pair = (norm(candidate["company"]),
                    norm_role(candidate.get("role") or candidate.get("title")))
            records["pairs"].add(pair)
            records["detail"].setdefault("pair:%s:%s" % pair,
                                         "another find in this same sweep")

    print(json.dumps({"new": fresh, "skipped": skipped,
                      "counts": {"in": len(candidates), "new": len(fresh),
                                 "alreadyKnown": len(skipped)}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
