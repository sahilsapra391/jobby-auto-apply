#!/usr/bin/env python3
"""Give every watched role a pool record, so the staleness sweep can see it.

The Watching lane is built from sessions; the staleness sweep works from the
job pool. 43 of 52 watch sessions had no pool record at all, so 83% of the lane
the owner curates by hand was invisible to the feature that removes dead postings
from it. This is what closes that gap.

**Why these have no record.** A watched role that does not match today's search
keywords never enters the pool: the gate runs before storage. That is right for
discovery and wrong here. A role the user chose by hand outranks the keyword
filter -- the filter exists to decide what to *show* him, not to overrule what
he already picked. So these records are written past the gate, in `watched`,
and they carry `keptByDecision` to say why they are exempt.

**Why records rather than URL checks.** The alternative was teaching the sweep
to verify a session's posting URL directly. That cannot be made correct on the
platform that matters most: a dead Greenhouse posting answers HTTP 200 with the
board index, so a URL check reports it alive. Absence from a complete board
listing is the only reliable signal, and that lives in the pool's world. Two
liveness paths would also be two paths to keep honest.

**Dead versus unknown.** Fetched complete and the posting is absent: the role
is closed, with the Dropped-lane trace, and that is the feature working rather
than a failure. Board unfetchable, or the listing not complete: the session is
left exactly as it is and named as unverifiable. A failed fetch never feeds
closure, here as everywhere.

Defaults to a dry run.

  python3 scripts/backfill_watch_records.py --out plan.json
  python3 scripts/backfill_watch_records.py --apply --out plan.json
"""

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "sources"))
import identity  # noqa: E402
import jobs_store  # noqa: E402
import sources  # noqa: E402
from sources import base  # noqa: E402


def store(*args):
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "store.py"), *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"backfill: store {' '.join(args)} failed: {out.stderr[:200]}")
    return json.loads(out.stdout or "[]")


def stranded_sessions(jobs, roles):
    """Watch sessions with no pool record, and what we know about each."""
    out = []
    for session in store("session-list"):
        aid = session.get("applicationId")
        if session.get("status") != "watch" or aid in jobs:
            continue
        role = roles.get(aid) or {}
        out.append({"applicationId": aid,
                    "company": session.get("company") or role.get("company"),
                    "role": session.get("role") or role.get("role"),
                    "url": role.get("url"),
                    "ats": role.get("ats"),
                    "source": role.get("source")})
    return out


def on_same_board(url, postings):
    """Does this posting URL belong to the board these postings came from?

    Host and the path segment after it, which is the tenant on every platform
    that puts one there. Crude, and the alternative is a per-platform tenant
    parser for a yes/no.
    """
    def shape(value):
        parts = urllib.parse.urlsplit(value or "")
        head = [p for p in parts.path.split("/") if p][:1]
        return (parts.netloc.lower(), head[0].lower() if head else "")

    if not url:
        return False
    mine = shape(url)
    return any(shape(p.get("canonicalUrl")) == mine for p in postings)


def boards_for(companies, wanted, index):
    """Which registry companies to fetch, keyed by normalised name."""
    by_name = {}
    for company in companies.values():
        if company.get("ats") and company.get("token"):
            by_name[identity.norm_company(company.get("name"), index.alias_map)] = company
    return {name: by_name[name] for name in wanted if name in by_name}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.store)
    jobs = jobs_store.load_jobs(root)
    companies = jobs_store.load_companies(root)
    roles_path = root / "roles.json"
    roles = json.loads(roles_path.read_text()) if roles_path.exists() else {}
    index = identity.Index(jobs, companies)

    sessions = stranded_sessions(jobs, roles)
    wanted = {identity.norm_company(s["company"], index.alias_map) for s in sessions}
    targets = boards_for(companies, wanted, index)

    # A fetcher with no stored validators, so every board is read in full.
    #
    # This is not an optimisation to skip; it is the difference between "the
    # board has no such posting" and "the board told us nothing". Sharing the
    # store's cache means a board that answered 304 returns zero postings, and
    # zero postings read as *every watched role at that employer is gone*. The
    # first dry run proposed closing 34 of 43 roles on exactly that reasoning,
    # and a spot check found the postings alive.
    cache = tempfile.TemporaryDirectory()
    fetcher = base.Fetcher(root=Path(cache.name))
    listings, incomplete = {}, {}
    for name, company in sorted(targets.items()):
        channel = sources.ALL.get(company["ats"])
        if channel is None:
            incomplete[name] = f"no connector for {company['ats']}"
            continue
        result = channel(fetcher, companies={name: company})
        if result.get("status") != "ok":
            incomplete[name] = result.get("reason") or result.get("status")
            continue
        if not result.get("listingComplete"):
            incomplete[name] = "the board's listing is not complete, so absence proves nothing"
            continue
        if not result["postings"]:
            # An employer really can have an empty board, and we cannot tell
            # that apart from a board we failed to read. The safe reading of
            # "nothing came back" is that we learned nothing.
            incomplete[name] = "the board returned no postings at all, which is not evidence"
            continue
        listings[name] = result["postings"]

    restore, close, unverifiable = [], [], []
    for session in sessions:
        name = identity.norm_company(session["company"], index.alias_map)
        if name in incomplete or name not in listings:
            unverifiable.append(dict(session, why=incomplete.get(
                name, "no board known for this employer")))
            continue
        wanted_url = identity.url_key(session.get("url") or "")
        match = None
        for posting in listings[name]:
            urls = {identity.url_key(u) for u in identity.sighting_urls(posting)}
            if wanted_url and wanted_url in urls:
                match = posting
                break
            if (jobs_store.norm_title(posting.get("title"))
                    == jobs_store.norm_title(session.get("role"))):
                match = posting
                break
        if match is None:
            # Absence is only evidence on the board the posting actually lives
            # on. Glean's registry entry is SmartRecruiters and the watched
            # posting is on Greenhouse: fetching the wrong board and reading
            # its silence as closure would have retired a live role. So the
            # session's own URL has to sit on the board we listed.
            if not on_same_board(session.get("url"), listings[name]):
                unverifiable.append(dict(session, why=(
                    "the watched posting is not on the board we hold for this "
                    "employer, so that board's listing says nothing about it")))
                continue
            close.append(dict(session, why="absent from a complete board listing"))
        else:
            restore.append(dict(session, matchedTitle=match.get("title"),
                                url=match.get("canonicalUrl")))

    plan = {"applied": False, "restore": restore, "close": close,
            "unverifiable": unverifiable, "boardsFetched": len(listings),
            "requests": fetcher.requests}

    if args.apply:
        with jobs_store.edit(root, note="backfill_watch_records") as (live, state):
            for row in restore:
                posting = next(p for p in listings[identity.norm_company(
                    row["company"], index.alias_map)]
                    if p.get("canonicalUrl") == row["url"])
                # Through normal identity resolution, so a backfill cannot
                # mint a duplicate of a posting the pool already holds under
                # another sighting.
                match_id, how = identity.resolve(index, posting)
                if match_id in live:
                    row["note"] = f"the pool already knew this posting ({how})"
                    continue
                record = dict(posting, id=match_id, state="watched",
                              keptByDecision="watched by the user; exempt from the "
                                             "search gate, which decides what to show "
                                             "rather than what he already chose")
                live[match_id] = record
                row["id"] = match_id
            if not restore:
                state["write"] = False
        plan["applied"] = True

    print(f"{'APPLIED' if args.apply else 'DRY RUN'}: {len(restore)} records to write, "
          f"{len(close)} postings gone, {len(unverifiable)} unverifiable "
          f"({len(listings)} boards read, {fetcher.requests} requests)\n")
    for label, rows in (("RESTORE", restore), ("GONE", close),
                        ("UNVERIFIABLE", unverifiable)):
        for row in rows[:6]:
            print(f"  {label:<13} {row['company'][:22]:<22} {(row['role'] or '')[:38]:<38}"
                  f"{'  ' + row['why'][:44] if row.get('why') else ''}")
        if len(rows) > 6:
            print(f"  {'':<13} ... and {len(rows) - 6} more")
    if args.out:
        Path(args.out).write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        print(f"\nfull plan: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
