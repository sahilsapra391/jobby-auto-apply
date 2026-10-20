"""Which posting an application is for, resolved one way for every caller.

**`applications.posting_id` was NULL for all 204 rows**, so every application
lane on v2 drew a legacy slug where the board draws a company and a role. This
is the resolver, and it is one function on purpose: the backfill over history
and `class_b` writing a new application must not disagree about what an
application points at, or the column means two different things depending on
when the row was written.

Three strategies, strongest first, measured over the real store on 2026-09-01:

    exact apply_url                    103
    board key (source, slug, id)        13
    legacy_id -> postings.legacy_id      9
                                       ---
                                       125 of 204   (61%)

**The 79 that do not resolve are not a parser weakness and must not be closed
by guessing.** They are, in order of size: the posting has been pruned from the
pool, so there is nothing to point at; aggregator redirects that carry no board
identity (`jobviewtrack.com/v2/...`); employer-own domains carrying a `gh_jid`
(Duolingo, Dropbox, Coupang); and Workable short links. A pruned posting cannot
be resolved to at any coverage, which is a ceiling on this approach rather than
a gap in it.

Two ways to get this wrong, both measured and both refused here:

**Never match on a normalised URL.** `norm_url` strips queries and 4,202 of
14,569 pool jobs carry their identity in the query; one recorded role at one
employer already made `seen.check` call every other job there known. Only exact
`apply_url` equality is used.

**Never guess the source.** Deriving a board key requires knowing which board's
pattern to apply, and trying all sixteen was measured at zero ambiguity on this
corpus, which is a property of the corpus and not of the method. A record whose
source is unknown is unresolvable, and says so.

And never `roles`. It carries a company and a role on the same key and would
name 196 of 204, and it is a snapshot that no migration creates and nothing
maintains. Reading it would present today's snapshot as the record of what
happened, which is the `answers.json` join in different clothing.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))

import board_key  # noqa: E402

#: Why one application could not be pointed at a posting. Reported rather than
#: counted, because "79 unresolved" invites closing the gap and these say which
#: part of it a Phase 3 connector closes for free.
NO_URL = "no posting url recorded"
NO_SOURCE = "no source recorded, so no board pattern applies"
NO_KEY = "the url carries no board key"
NO_POSTING = "no posting in the pool matches"


class Resolver:
    """Three lookup tables, read once, then a pure decision per application.

    Read once because the alternative is three statements per application and
    Neon answers a round trip in about 60 ms; 204 applications would be a
    minute of latency for a query that fits in memory three times over.
    """

    def __init__(self, conn):
        with conn.cursor() as cur:
            cur.execute("select apply_url, id from postings "
                        "where apply_url is not null and apply_url <> ''")
            self.by_url = dict(cur.fetchall())
            cur.execute("select board_slug, external_id, id from postings "
                        "where board_slug is not null and external_id is not null")
            self.by_key = {(slug, ext): pid for slug, ext, pid in cur.fetchall()}
            cur.execute("select legacy_id, id from postings "
                        "where legacy_id is not null")
            self.by_legacy = dict(cur.fetchall())

    def resolve(self, legacy_id=None, url=None, source=None):
        """`(posting_id, how)` or `(None, why)`. Never raises on bad input."""
        url = (url or "").strip()
        source = (source or "").strip().lower()

        if url and url in self.by_url:
            return self.by_url[url], "apply_url"

        if url:
            if not source:
                key_reason = NO_SOURCE
            else:
                parsed = board_key.parse(source, url)
                if not parsed:
                    key_reason = NO_KEY
                elif parsed in self.by_key:
                    return self.by_key[parsed], "board_key"
                else:
                    key_reason = NO_POSTING
        else:
            key_reason = NO_URL

        legacy_id = (legacy_id or "").strip()
        if legacy_id and legacy_id in self.by_legacy:
            return self.by_legacy[legacy_id], "legacy_id"

        return None, key_reason
