"""Which employer a posting points at, which nothing compared until now.

**`mirror_fields.EXEMPT` excused `postings.employer_id` with a reason that was
true of the column and false of the property.** It read "a uuid Postgres
assigns. The store has no such id, and the employer is compared through
`employers.canonical_name` instead." The first sentence is true. The second
described a comparison that did not exist: `backfill.posting_row` returns
thirty columns and none of them is the employer, and `mirror_fields.compared()
["employers"]` is that table's own four columns. So parity asked "does employer
row E have the right name" and never "does posting P point at the right
employer".

Measured 2026-09-06 by driving `verify_parity.compare`: a posting whose
employer edge was wrong and whose thirty compared columns were identical
produced no finding at all, while changing `title` produced one.

That matters now because Phase 3's employer resolver exists to repoint
postings -- 122 filed under a feed publisher that belong to Johnson Controls,
Tyco, Hologic and Pfizer. Its correct run and its catastrophic run would have
been the same green.

**Normalised, because the naive comparison is 307 false positives.** Comparing
the pool's `company` against `employers.canonical_name` disagrees on 307 of the
23,633 postings the two stores share, and every one is a single employer under
two spellings; 249 are Davies against Davies Group. `norm_company` is what the
mirror itself keys employers on, so comparing through it asks whether the
posting points at the same *employer* rather than at the same string. That is
16 of 23,633, in the two groups `EMPLOYER_EDGE_ALLOWED` names.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import pgtest          # noqa: E402
import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import backfill        # noqa: E402
import migrate         # noqa: E402
import mirror_fields   # noqa: E402
import verify_parity   # noqa: E402

RECORD = {
    "id": "probe", "title": "Technician", "state": "new",
    "canonicalSource": "greenhouse",
    "canonicalUrl": "https://boards.greenhouse.io/x/jobs/1",
}


def findings(pool_company, db_norm_name):
    """`compare` over one posting, agreeing on everything but the employer."""
    record = dict(RECORD, company=pool_company)
    row = dict(backfill.posting_row(record))
    row.update({"__state__": "new", "__merged_into__": None,
                "__employer__": db_norm_name})
    return verify_parity.compare({"probe": record}, {"probe": row})


def edges(pool_company, db_norm_name):
    return [f for f in findings(pool_company, db_norm_name)
            if f["kind"] == "employer_edge"]


class TheEdgeIsCompared(unittest.TestCase):
    def test_a_repointed_posting_is_reported(self):
        """The resolver's own output, and the thing that was invisible."""
        found = edges("Johnson Controls", "appcast enterprise")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["file"], "johnson controls")
        self.assertEqual(found[0]["postgres"], "appcast enterprise")

    def test_the_matching_edge_is_silent(self):
        self.assertEqual(edges("Johnson Controls", "johnson controls"), [])

    def test_a_display_spelling_is_not_a_repointing(self):
        """Davies and Davies Group are one employer, and the mirror knows it.

        This is the case that makes the naive comparison useless: 249 of the
        307 raw disagreements are this pair. `norm_company` collapses them, so
        the comparison never sees them.
        """
        self.assertEqual(backfill.norm_company("Davies"),
                         backfill.norm_company("Davies Group"))
        self.assertEqual(edges("Davies", backfill.norm_company("Davies Group")), [])

    def test_the_comparison_is_not_vacuous(self):
        """The control. Every assertion above is satisfied by a check that
        never fires, so one field parity does compare is changed here."""
        record = dict(RECORD, company="Johnson Controls")
        row = dict(backfill.posting_row(record))
        row.update({"__state__": "new", "__merged_into__": None,
                    "__employer__": "johnson controls", "title": "Other"})
        kinds = {f["field"] for f in verify_parity.compare({"probe": record},
                                                           {"probe": row})}
        self.assertIn("title", kinds)


class TheAllowancesAreNarrowAndExplained(unittest.TestCase):
    def test_the_sentinel_pair_is_allowed(self):
        self.assertEqual(edges("", "(unknown employer)"), [])

    def test_a_real_company_filed_under_the_sentinel_is_not(self):
        """The allowance is a *pair*, not a side.

        A posting whose feed named no company is filed under the sentinel and
        that is the design. A posting whose feed named Pfizer and which is
        filed under the sentinel is a lost employer, and an allowance keyed on
        the sentinel alone would have hidden it.
        """
        self.assertEqual(len(edges("Pfizer", "(unknown employer)")), 1)

    def test_the_honeycomb_pair_is_allowed_and_only_that_pair(self):
        self.assertEqual(edges("Honeycomb.io", "honeycomb"), [])
        self.assertEqual(len(edges("Honeycomb.io", "hive")), 1)

    def test_each_allowance_says_what_it_gives_up(self):
        for pair, reason in verify_parity.EMPLOYER_EDGE_ALLOWED.items():
            with self.subTest(pair=pair):
                self.assertIn("GIVES UP:", reason,
                              f"{pair} is allowed without saying what is now "
                              f"unchecked")
                self.assertGreater(len(reason.split()), 25,
                                   f"{pair}'s reason is too short to check")


class TheExemptionSaysWhatItGivesUp(unittest.TestCase):
    def test_employer_id_no_longer_claims_a_comparison_that_does_not_exist(self):
        """The exemption's own text is the thing that went wrong.

        It is kept rather than deleted -- the column really is a uuid the store
        has no counterpart for -- but its second sentence used to assert a
        comparison through `employers.canonical_name` that did not exist. If
        that phrasing comes back, the next reader inherits the same false
        assurance.
        """
        reason = mirror_fields.EXEMPT[("postings", "employer_id")]
        self.assertIn("GIVES UP:", reason)
        self.assertNotIn("compared through `employers.canonical_name` instead",
                         reason,
                         "the exemption again claims the employer is compared "
                         "through the employer's own name, which compares an "
                         "employer's name and not a posting's employer")

    def test_no_posting_column_carries_the_employer(self):
        """Why the edge needs its own comparison rather than a field.

        If this ever becomes false -- a denormalised employer name on
        `postings` -- the edge should be compared as an ordinary field and this
        machinery removed rather than left beside it.
        """
        row = backfill.posting_row(dict(RECORD, company="Anything"))
        self.assertEqual(
            [k for k in row if "employ" in k.lower() or "company" in k.lower()],
            [], "a posting row now carries its employer, so the edge comparison "
                "and the field comparison are two answers to one question")


class ProvenanceIsRecordedOnTheArrivalPath(pgtest.SchemaCase):
    prefix = "empedge"

    """`postings.employer_decided_by`, driven against a real database.

    **Added before the resolver, because the write it will make is the one
    nothing can take back.** Measured 2026-09-06 against production: exactly
    two foreign keys reference `employers` and neither is a witness to the
    posting-to-employer edge, so §11c's verification method -- find a table
    that references the row and was written earlier -- has nothing to find.

    Two properties, and the second is the one the resolver depends on.
    """

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)

    def record(self, legacy, company):
        return {"id": legacy, "company": company, "title": "T",
                "canonicalSource": "greenhouse",
                "canonicalUrl": f"https://boards.greenhouse.io/x/jobs/{legacy}",
                "state": "new", "firstSeenAt": "2026-09-01T00:00:00Z",
                "lastSeenAt": "2026-09-06T00:00:00Z"}

    def store(self, records):
        employers = backfill.upsert_employers(self.conn, records)
        if isinstance(employers, tuple):
            employers = employers[0]
        backfill.upsert_postings(self.conn, records, employers)

    def employer_of(self, legacy):
        with self.conn.cursor() as cur:
            cur.execute("""select e.norm_name, p.employer_decided_by,
                                  p.employer_decided_at is not null
                             from postings p
                             join employers e on e.id = p.employer_id
                            where p.legacy_id = %s""", (legacy,))
            return cur.fetchone()

    def test_an_arriving_posting_records_which_rule_chose_its_employer(self):
        self.store([self.record("prov-1", "Acme")])
        norm, by, stamped = self.employer_of("prov-1")
        self.assertEqual(norm, "acme")
        self.assertEqual(by, backfill.DECIDED_BY_INGEST)
        self.assertTrue(stamped)

    def test_a_stored_posting_keeps_its_employer_when_the_pool_is_corrected(self):
        """**The finding that reshapes the resolver, as a test.**

        `employer_id` and its provenance lead the INSERT tuple and are absent
        from the `on conflict do update` assignments, so a posting's employer
        is decided once. Correcting the pool's `company` and re-running the
        real upsert does not move the stored row.

        Verified against production the same way before it was written here,
        inside a transaction that was rolled back: a posting filed under
        `appcast enterprise` stayed there after its record's company was
        corrected to Johnson Controls and `upsert_postings` re-ran on it.

        So the resolver cannot work by fixing ingest, and it cannot work by
        fixing the pool. It needs its own write against `postings.employer_id`,
        exactly as it needs its own write for `employers.domain`, and for the
        same reason: the upsert path is deliberately not a correction path.
        """
        self.store([self.record("prov-2", "Appcast Enterprise")])
        self.assertEqual(self.employer_of("prov-2")[0], "appcast enterprise")

        self.store([self.record("prov-2", "Johnson Controls")])
        norm, _, _ = self.employer_of("prov-2")
        self.assertEqual(
            norm, "appcast enterprise",
            "a corrected pool moved the stored employer, which would mean the "
            "resolver could ship as an ingest fix. It cannot; if this ever "
            "passes the other way, the resolver's design changes with it")

    def test_the_provenance_column_is_not_backfilled(self):
        """A row written before this column existed reads null, and null means
        'decided before provenance was recorded'. Inventing 'ingest' for it
        would be a claim about how 25,000 rows were decided, reconstructed
        from the fact that nothing is complaining."""
        with self.conn.cursor() as cur:
            cur.execute("""insert into employers (canonical_name, norm_name)
                           values ('Old', 'old') returning id""")
            employer = cur.fetchone()[0]
            cur.execute(
                """insert into postings (employer_id, legacy_id, source,
                                         board_slug, external_id, title,
                                         apply_url, first_seen_at, last_seen_at)
                   values (%s, 'prov-3', 'greenhouse', 'x', 'prov-3', 'T',
                           'https://boards.greenhouse.io/x/jobs/3', now(), now())""",
                (employer,))
            cur.execute("select employer_decided_by from postings "
                        "where legacy_id = 'prov-3'")
            self.assertIsNone(cur.fetchone()[0])
