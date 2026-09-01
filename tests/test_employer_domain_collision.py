"""A domain claimed twice must be resolved, wherever the other claimant is.

**The sibling of the board-key bug, one table over, and it was still holding
the board frozen after that one was fixed.** `employers` has two unique
constraints, `norm_name` and `domain`. `upsert_employers` resolves domain
contention *within a batch* in Python and then writes `on conflict
(norm_name)`, which covers the first and not the second. An employer whose
domain already belongs to a different stored row raised `UniqueViolation`,
aborted the batch, and failed the sweep the same way:

    duplicate key value violates unique constraint "employers_domain_key"
    DETAIL:  Key (domain)=(rydercareers.video) already exists.

Observed on the live pool on 2026-09-01, on the first sweep after the postings
fix deployed. Reported here rather than smoothed over: the postings fix was
correct and it was not sufficient, and only running a real sweep showed that.

The rule is the one already documented for the in-batch case: the domain goes
to the lexicographically first claimant, deterministically so a re-run does not
move it; the losers keep their row with no domain and a `merged_into` pointer;
and they are reported rather than swallowed. That rule now runs over the
arriving employers *and* whatever already holds the domain.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import backfill  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402


#: `norm_company` strips the legal suffix, so "Alpha Corp" is stored as
#: `alpha`. The tests name the normalised form, because that is what the
#: unique constraint and the sort order actually see.
def record(company, website, legacy="x1"):
    return {"id": legacy, "canonicalSource": "greenhouse",
            "canonicalUrl": f"https://boards.greenhouse.io/acme/jobs/{legacy}",
            "company": company, "title": "Engineer",
            "companyWebsite": website,
            "firstSeenAt": "2026-08-01T00:00:00Z",
            "lastSeenAt": "2026-09-01T00:00:00Z"}


class EmployerDomainCollisionTests(pgtest.SchemaCase):
    prefix = "empdomain"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)

    def upsert(self, records):
        return backfill.upsert_employers(self.conn, records)

    def stored(self):
        with self.conn.cursor() as cur:
            cur.execute("select norm_name, domain, merged_into is not null "
                        "from employers order by norm_name")
            return cur.fetchall()

    def domain_holder(self, domain):
        with self.conn.cursor() as cur:
            cur.execute("select norm_name from employers where domain = %s",
                        (domain,))
            row = cur.fetchone()
            return row[0] if row else None

    # -- the cases --------------------------------------------------------

    def test_two_employers_contesting_a_domain_in_one_batch(self):
        """Already handled, asserted so the fix cannot regress it."""
        ids, contested = self.upsert([
            record("Zeta Corp", "https://shared.example"),
            record("Alpha Corp", "https://shared.example", legacy="x2"),
        ])
        self.assertEqual(self.domain_holder("shared.example"), "alpha",
                         "the lexicographically first claimant takes it")
        self.assertTrue(contested, "the loser is reported, never swallowed")

    def test_an_employer_whose_domain_a_stored_row_already_holds(self):
        """**The case that raised**, and the Ryder shape.

        The two employers arrive in different passes, so the in-batch loop
        never sees them together.
        """
        self.upsert([record("Alpha Corp", "https://shared.example")])
        self.assertEqual(self.domain_holder("shared.example"), "alpha")

        ids, contested = self.upsert(
            [record("Zeta Corp", "https://shared.example", legacy="x2")])

        self.assertEqual(self.domain_holder("shared.example"), "alpha",
                         "the stored claimant sorts first and keeps it")
        rows = dict((n, d) for n, d, _ in self.stored())
        self.assertIn("zeta", rows, "the loser still gets its own row")
        self.assertIsNone(rows["zeta"],
                          "and that row carries no domain")
        self.assertTrue(contested, "the loser is reported")

    def test_an_arriving_employer_that_sorts_first_takes_the_domain(self):
        """The direction the live case did not take.

        A fix that said "the stored row always wins" would pass every test
        drawn from the pool and be a second rule. One rule, over every
        claimant, means the incumbent can lose.
        """
        self.upsert([record("Zeta Corp", "https://shared.example")])
        self.assertEqual(self.domain_holder("shared.example"), "zeta")

        ids, contested = self.upsert(
            [record("Alpha Corp", "https://shared.example", legacy="x2")])

        self.assertEqual(self.domain_holder("shared.example"), "alpha",
                         "the arriving employer sorts first, so it takes it")
        rows = dict((n, d) for n, d, _ in self.stored())
        self.assertIsNone(rows["zeta"],
                          "and the displaced incumbent gives the domain up")
        self.assertTrue(contested)

    def test_the_same_employers_twice_changes_nothing(self):
        """Idempotency, because this is exactly what the postings fix broke on
        its first attempt: once the stored row is a claimant for its own
        domain, the naive comparison makes every employer contest itself."""
        records = [record("Alpha Corp", "https://a.example"),
                   record("Beta Corp", "https://b.example", legacy="x2")]
        self.upsert(records)
        first = self.stored()
        ids, contested = self.upsert(records)
        self.assertEqual(first, self.stored(), "a second pass moved a row")
        self.assertEqual(contested, [],
                         "an unchanged batch contests nothing")
        self.assertEqual(self.domain_holder("a.example"), "alpha")
        self.assertEqual(self.domain_holder("b.example"), "beta")

    def test_distinct_domains_are_all_written(self):
        """The control. Without it every assertion above is satisfied by an
        upsert that drops every domain on the floor."""
        ids, contested = self.upsert([
            record("Alpha Corp", "https://a.example"),
            record("Beta Corp", "https://b.example", legacy="x2"),
            record("Gamma Corp", "https://c.example", legacy="x3"),
        ])
        self.assertEqual(contested, [])
        held = {n: d for n, d, _ in self.stored()}
        self.assertEqual(held.get("alpha"), "a.example")
        self.assertEqual(held.get("beta"), "b.example")
        self.assertEqual(held.get("gamma"), "c.example")


if __name__ == "__main__":
    unittest.main()
