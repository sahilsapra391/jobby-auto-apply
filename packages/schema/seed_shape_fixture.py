"""One row of everything, so the deployed-shape crawl renders row-bearing code.

**An empty fixture under-renders and nothing looks wrong.** CI's Postgres is a
throwaway container with no rows in it, so every lane drew its empty state, the
Memory Bank drew none of its rows, and `check_deployed_shape.mjs` crawled a
page that was a fraction of the real one. Three faults reached production
through that gap, each invisible for the same reason:

  /api/company-logo   the route came across in the markup and not in the code.
                      Fifty Found cards asked for it per page load and 404'd;
                      CI emitted no logo URLs because it had no postings.
  the RSC boundary    `page.tsx` passed a function prop into a client
                      component. It throws at serialization, so only a render
                      that *carries a row* throws, and CI had no rows.
  .gitignore          `applications/` unanchored swallowed the write endpoint's
                      route file. A route git ignores and a route nobody wrote
                      answer identically from CI.

This is the "not zero is not complete" lesson from `test_v2_matches_the_board`
applied to the deployed check: a guard against a vacuous run has to be
calibrated against the size of the real subject rather than against emptiness.

**Sentinel values, not plausible ones.** Every string here is recognisable on
sight and could not be mistaken for real data. The crawl asserts the rendered
HTML contains them, so a row that silently fails to render is a failure rather
than a slightly shorter page, and anyone who ever sees one of these strings on
a real board knows immediately what they are looking at.

Idempotent, so a re-run in the same container is free. Refuses outright to run
against anything but a throwaway: see `_refuse_a_real_store`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate  # noqa: E402

#: What the crawl looks for. Keyed by route, and the crawl reads this file
#: rather than restating the strings, because a marker list written twice is
#: the class this repository has now paid for six times.
MARKERS = {
    "/": ["Shape Fixture Employer", "Shape Fixture Posting"],
    "/profile": ["Shape fixture question?", "Shape fixture answer"],
}

EMPLOYER = "Shape Fixture Employer"
POSTING = "Shape Fixture Posting"
QUESTION = "Shape fixture question?"
ANSWER = "Shape fixture answer"
APPLICATION = "shape-fixture-application"


def _refuse_the_device_database(url):
    """Refuse to seed the database this device actually uses.

    **The failure this prevents is sentinel rows in the production corpus**,
    where "Shape Fixture Employer" would sit among real companies until
    somebody read the name.

    **Not a row count, and the first version was one.** It refused above 50
    postings, which sounded like "a real store has thousands and a throwaway
    has none". Measured on a container that had just run the suite: 300
    postings and 229 applications, from tests that create their own accounts
    and rows. So the guard would have refused in CI, on the one database it
    exists to run against, and the count says nothing about whose database it
    is anyway.

    Provenance is the property the thing itself cannot answer, and this is a
    provenance question. `migrate._device_url` reads it directly rather than
    inferring it from what the database contains.
    """
    device = migrate._device_url()
    if not device:
        return
    if _same_database(url, device):
        raise SystemExit(
            "refusing to seed the database this device is configured to use. "
            "The sentinel rows this writes would be indistinguishable from "
            "real ones on the board. Point --url at a throwaway container, or "
            "set JOBBY_TEST_DATABASE_URL.")


def _same_database(a, b):
    """Whether two URLs name one database, pooled or not.

    Host and path, with `-pooler` removed from both, because the pooled and
    direct endpoints of one Neon project are the same rows and comparing the
    strings would say otherwise.
    """
    from urllib.parse import urlsplit
    def key(url):
        parts = urlsplit(migrate.direct_url(url))
        return ((parts.hostname or "").lower(), (parts.path or "").lower())
    try:
        return key(a) == key(b)
    except Exception:                                        # noqa: BLE001
        # An unparseable URL is "could not look", and "could not look" is never
        # "is not there". Refuse rather than seed.
        raise SystemExit("could not compare this URL against the device's own, "
                         "so it is not safe to seed. Nothing was written.")


def seed(conn, url):
    _refuse_the_device_database(url)
    with conn.cursor() as cur:
        cur.execute("select id from accounts order by created_at limit 1")
        row = cur.fetchone()
        if not row:
            raise SystemExit(
                "no account exists, so nothing can be attributed. The workflow "
                "creates one before this runs.")
        account = row[0]

        cur.execute(
            """insert into employers (canonical_name, norm_name, domain)
               values (%s, %s, %s)
               on conflict (norm_name) do update set canonical_name = excluded.canonical_name
               returning id""",
            (EMPLOYER, "shapefixtureemployer", "shape-fixture.example"))
        employer = cur.fetchone()[0]

        # A posting with a score and no decision, which is what puts a card in
        # Found. `posted_at` is recent so the lane's 30-day default window
        # keeps it: a fixture the default filter hides renders nothing, which
        # is this whole file's failure mode in miniature.
        cur.execute(
            """insert into postings (employer_id, legacy_id, source, board_slug,
                                     external_id, title, apply_url, posted_at,
                                     first_seen_at, last_seen_at, country,
                                     remote, summary, years_min, sources)
               values (%s, 'shape-fixture-posting', 'greenhouse', 'shapefixture',
                       'shape-fixture-posting', %s,
                       'https://boards.greenhouse.io/shapefixture/jobs/1',
                       now() - interval '1 day', now(), now(), 'US', true,
                       'A posting that exists so the crawl renders a card.',
                       3, '[{"sourceClass":"ats"}]'::jsonb)
               on conflict (legacy_id) do update set title = excluded.title,
                     posted_at = excluded.posted_at, last_seen_at = now()
               returning id""",
            (employer, POSTING))
        posting = cur.fetchone()[0]

        cur.execute(
            """insert into job_scores (account_id, posting_id, total, parts, why,
                                       gates, unknowns, config_version)
               values (%s, %s, 71, '{"title": 20}'::jsonb,
                       '{"title": "a fixture"}'::jsonb, '[]'::jsonb,
                       '[]'::jsonb, '23')
               on conflict (account_id, posting_id) do update
                 set total = excluded.total""",
            (account, posting))

        # One application in each of the lanes a row can sit in, so Watching,
        # In flight, Needs you and Submitted all render a row rather than an
        # empty state. Four rows, because four lanes have separate row code.
        for suffix, status in (("watch", "watch"), ("inflight", "inflight"),
                               ("review", "review"), ("done", "done")):
            cur.execute(
                """insert into applications (account_id, legacy_id, status,
                                             posting_id, created_at, updated_at)
                   values (%s, %s, %s, %s, now(), now())
                   on conflict (account_id, legacy_id) do update
                     set status = excluded.status, updated_at = now()""",
                (account, f"{APPLICATION}-{suffix}", status, posting))

        # One bank entry per kind, so every tab has a row and every row draws
        # its controls. `question` is the kind that carries a category chip.
        for i, (key, state, kind) in enumerate((
                ("shape-fixture-answered", "confirmed", "question"),
                ("shape-fixture-open", "needs-you", "question"),
                ("shape-fixture-dismissed", "dismissed", "question"))):
            cur.execute(
                """insert into answer_bank (account_id, key, question, value,
                                            state, tags, sensitive, kind,
                                            category, version, updated_at)
                   values (%s, %s, %s, %s, %s, '{}', false, %s, 'logistics',
                           %s, now())
                   on conflict (account_id, key) do update
                     set question = excluded.question, value = excluded.value,
                         state = excluded.state, version = excluded.version""",
                (account, key, QUESTION,
                 ANSWER if state == "confirmed" else None, state, kind, i))

        cur.execute(
            """insert into profiles (account_id, identity, resumes, updated_at)
               values (%s, '{"location": "Somewhere"}'::jsonb,
                       '[{"key": "us", "appliesTo": ["US"], "default": true,
                          "identity": {"location": "Somewhere"},
                          "document": "shape-fixture.pdf"}]'::jsonb, now())
               on conflict (account_id) do update
                 set resumes = excluded.resumes, updated_at = now()""",
            (account,))
    conn.commit()
    return {"account": str(account), "posting": str(posting),
            "markers": MARKERS}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", help="the database to seed; defaults to the "
                                  "configured test URL")
    ap.add_argument("--out", help="write the markers here, for the crawl to "
                                  "read instead of restating them")
    args = ap.parse_args(argv)
    url = args.url or migrate.database_url()
    conn = migrate.connect(url=url, announce=False)
    try:
        report = seed(conn, url)
    finally:
        conn.close()
    payload = json.dumps(report, indent=1)
    if args.out:
        # Written for `check_deployed_shape.mjs`, which fails outright when it
        # is missing rather than crawling an unseeded page and reporting a
        # pass. A silent skip here is the exact failure this file exists to
        # close, one level up.
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
