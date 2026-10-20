"""What an application may be linked to, and what it must refuse to guess.

The refusals are the point. A resolver that links 100% of applications is
wrong, because 39% of them point at postings that have been pruned or at URLs
carrying no board identity at all, and a link to the wrong posting puts one
company's name on another company's application.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import posting_link  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402


class ResolverTests(pgtest.SchemaCase):
    prefix = "postlink"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into employers (canonical_name, norm_name) "
                        "values ('Acme', 'acme') returning id")
            employer = cur.fetchone()[0]
            cur.execute(
                """insert into postings (employer_id, legacy_id, source,
                                         board_slug, external_id, title,
                                         apply_url, first_seen_at, last_seen_at)
                   values (%s, 'abc123', 'greenhouse', 'acme', '77', 'Engineer',
                           'https://boards.greenhouse.io/acme/jobs/77?gh_jid=77',
                           now(), now())
                   returning id""", (employer,))
            self.posting = cur.fetchone()[0]
        self.resolver = posting_link.Resolver(self.conn)

    def test_an_exact_apply_url_links(self):
        found, how = self.resolver.resolve(
            url="https://boards.greenhouse.io/acme/jobs/77?gh_jid=77",
            source="greenhouse")
        self.assertEqual(found, self.posting)
        self.assertEqual(how, "apply_url")

    def test_a_board_key_links_when_the_url_differs(self):
        """The same posting reached through the other Greenhouse hostname."""
        found, how = self.resolver.resolve(
            url="https://job-boards.greenhouse.io/acme/jobs/77",
            source="greenhouse")
        self.assertEqual(found, self.posting)
        self.assertEqual(how, "board_key")

    def test_a_legacy_id_links_when_there_is_no_usable_url(self):
        found, how = self.resolver.resolve(legacy_id="abc123", url="", source="")
        self.assertEqual(found, self.posting)
        self.assertEqual(how, "legacy_id")

    # -- the refusals -----------------------------------------------------

    def test_a_url_that_differs_only_in_its_query_is_not_a_match(self):
        """**The shared-careers-page shape, and the reason nothing is normalised here.**

        `norm_url` strips queries, and 4,202 of 14,569 pool jobs carry their
        identity in the query. One recorded role at one employer already
        made `seen.check` call every other job there known. A different query is a
        different posting until a board key says otherwise.
        """
        found, how = self.resolver.resolve(
            url="https://boards.greenhouse.io/acme/jobs/77?gh_jid=99999",
            source="")
        self.assertIsNone(found)
        self.assertEqual(how, posting_link.NO_SOURCE)

    def test_an_unknown_source_is_refused_rather_than_guessed(self):
        """Trying all sixteen board patterns measured zero ambiguity on this
        corpus, which is a property of the corpus and not of the method."""
        found, how = self.resolver.resolve(
            url="https://job-boards.greenhouse.io/acme/jobs/77", source="")
        self.assertIsNone(found)
        self.assertEqual(how, posting_link.NO_SOURCE)

    def test_an_aggregator_redirect_carries_no_board_key(self):
        found, how = self.resolver.resolve(
            url="https://jobviewtrack.com/v2/uZnEmMoQFRfzXRNA53uqy",
            source="jooble")
        self.assertIsNone(found)
        self.assertEqual(how, posting_link.NO_KEY)

    def test_a_pruned_posting_resolves_to_nothing_and_says_so(self):
        """The largest category, and a ceiling rather than a gap: there is no
        row to point at."""
        found, how = self.resolver.resolve(
            url="https://boards.greenhouse.io/other/jobs/5",
            source="greenhouse")
        self.assertIsNone(found)
        self.assertEqual(how, posting_link.NO_POSTING)

    def test_no_url_at_all_says_which_of_the_reasons_it_is(self):
        found, how = self.resolver.resolve(legacy_id="nope", url="", source="")
        self.assertIsNone(found)
        self.assertEqual(how, posting_link.NO_URL)

    def test_it_never_reads_the_frozen_roles_table(self):
        """`roles` would name 196 of 204 and is a snapshot nothing maintains.
        Reading it here would put its content on the board as though it were
        current, which is the `answers.json` join in different clothing."""
        source = (REPO / "scripts" / "posting_link.py").read_text(encoding="utf-8")
        code = source[source.index("class Resolver"):]
        self.assertNotIn("roles", code,
                         "the resolver must not read the frozen snapshot")
