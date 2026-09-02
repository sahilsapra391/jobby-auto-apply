"""Parity compares every column the mirror writes, or names why it does not.

**Seven consecutive green runs compared 11 of 28 posting columns and one of
four session columns.** `applications.status` was the whole of what parity knew
about a session; `phase` was never looked at, and on the first run that did
look, it found a real one: Harvey `17121dc2523fec5e` held `Queued` in Postgres
against a session file with no `phase` key at all.

The field set was a subset nobody had written down, which is why nobody noticed
it was a subset. So it is derived now, and this is the control: it fails when
the mirror gains a column parity does not compare, and it fails when an
exemption is added without a reason.

It is deliberately not a list of the columns as they stand today. A test that
restates the configuration defends the configuration from being corrected --
`parity_clock.GATE_PATHS` had exactly that, an `assertEqual` against four
names that would have failed the fix for the outage it was written after.
"""

import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import store_isolation  # noqa: E402

import backfill  # noqa: E402
import class_b  # noqa: E402
import mirror_fields  # noqa: E402
import verify_parity  # noqa: E402


def setUpModule():
    store_isolation.begin(mirror_fields, verify_parity, class_b)


def tearDownModule():
    store_isolation.end()


class EveryMirroredColumnIsCompared(unittest.TestCase):
    def test_nothing_the_mirror_writes_is_uncompared(self):
        gaps = mirror_fields.uncovered(verify_parity.coverage())
        self.assertEqual(
            gaps, [],
            "these columns reach Postgres and nothing checks them. Either "
            "compare them in verify_parity, or add them to mirror_fields."
            "EXEMPT with a reason saying why they cannot disagree.")

    def test_the_check_notices_a_new_column(self):
        """The control. A comparison of two empty sets passes on any tree."""
        coverage = verify_parity.coverage()
        written = mirror_fields.written()
        self.assertTrue(written, "discovery found no tables at all")
        # Pretend the mirror grew a column nobody wired up.
        real = mirror_fields.written

        def with_extra():
            grown = {t: frozenset(c) for t, c in real().items()}
            grown["applications"] = grown["applications"] | {"outcome_note"}
            return grown

        mirror_fields.written = with_extra
        try:
            gaps = mirror_fields.uncovered(coverage)
        finally:
            mirror_fields.written = real
        self.assertIn(("applications", "outcome_note"), gaps,
                      "a column the mirror writes and parity ignores has to "
                      "surface, or this whole file is decoration")

    def test_it_covers_more_than_the_subset_it_replaced(self):
        """The eleven lambdas, named, so a regression to them is visible."""
        before = {"title", "source", "apply_url", "norm_title", "location_raw",
                  "city", "region", "workplace", "desc_fingerprint", "lang",
                  "salary_raw"}
        now = verify_parity.coverage()["postings"]
        self.assertTrue(before < now,
                        "postings coverage has shrunk back toward the eleven "
                        "fields that were compared through seven green runs")
        self.assertGreaterEqual(len(now), 27)
        for column in ("posted_at", "summary", "years_min", "sections",
                       "remote", "tags", "closed_at"):
            self.assertIn(column, now,
                          f"{column} is drawn on the board and was uncompared")

    def test_a_session_is_more_than_its_status(self):
        self.assertIn("phase", verify_parity.coverage()["applications"],
                      "comparing status alone is what let a dropped session "
                      "keep the phase 'Queued' in Postgres indefinitely")

    def test_the_event_payload_is_compared(self):
        self.assertIn("payload", verify_parity.coverage()["application_events"],
                      "the confirmation text, the resume filename and the "
                      "cover-letter note all live in that column, and it is "
                      "the only record of what an employer received")


class EveryExemptionCarriesAReason(unittest.TestCase):
    def test_each_entry_says_why(self):
        for (table, column), reason in mirror_fields.EXEMPT.items():
            with self.subTest(table=table, column=column):
                self.assertIsInstance(reason, str)
                self.assertGreaterEqual(
                    len(reason.split()), 6,
                    f"{table}.{column} is exempt with no reason worth the "
                    f"name. An exemption without one is a hole with a comment.")

    def test_an_exemption_names_a_column_the_mirror_writes(self):
        written = mirror_fields.written()
        for table, column in mirror_fields.EXEMPT:
            with self.subTest(table=table, column=column):
                self.assertIn(table, written,
                              "exempting a table the mirror does not write")
                self.assertIn(column, written[table],
                              "exempting a column the mirror does not write, "
                              "which is an exemption that will outlive its "
                              "subject and quietly cover the next one")

    def test_no_exemption_hides_a_field_the_board_draws(self):
        """A column a lane renders can never be exempt: that is the class of
        field the DATA block was made of."""
        drawn = {("applications", "status"), ("applications", "phase"),
                 ("application_events", "payload"), ("application_events", "at"),
                 ("postings", "posted_at"), ("postings", "summary"),
                 ("job_scores", "total")}
        for pair in drawn:
            self.assertNotIn(pair, mirror_fields.EXEMPT,
                             f"{pair[0]}.{pair[1]} is on a lane")


class DiscoveryFailsLoudRatherThanEmpty(unittest.TestCase):
    def test_every_mirrored_table_discovers_columns(self):
        for table, columns in mirror_fields.written().items():
            self.assertTrue(columns, f"{table} discovered as empty")

    def test_the_tables_the_mirror_actually_writes_are_all_found(self):
        found = set(mirror_fields.written())
        for table in ("postings", "employers", "job_scores", "job_decisions",
                      "applications", "application_events", "answer_bank",
                      "profiles"):
            self.assertIn(table, found,
                          f"{table} is written by the mirror and discovery "
                          f"missed it, so parity would cover none of it")

    def test_postings_comes_from_the_function_not_the_regex(self):
        """`upsert_postings` interpolates its column list, so a regex reads
        nothing. Discovery calls `posting_row` instead, and this is what says
        so if that route is ever swapped for a parsed one that finds less."""
        columns = mirror_fields.written()["postings"]
        self.assertGreaterEqual(len(columns), 28)
        self.assertEqual(
            columns - {"employer_id"},
            frozenset(backfill.posting_row({
                "id": "x", "canonicalSource": "s",
                "canonicalUrl": "https://x/y"})))


class OneTransformNotTwo(unittest.TestCase):
    """Parity's expected side is the mirror's own row builder.

    The eleven lambdas were a second implementation of `posting_row`, and being
    a subset of it is exactly how a second implementation drifts. These assert
    the builders are shared rather than re-read.
    """

    def test_the_class_b_builders_are_what_the_mirror_uses(self):
        source = (REPO / "scripts" / "class_b.py").read_text()
        for name in ("session_row", "history_row", "answer_row", "profile_row"):
            self.assertIn(f"def {name}(", source)
        self.assertIn("built = session_row(session)", source,
                      "_application_id has to build its row through the same "
                      "function parity calls")
        self.assertIn("built = answer_row(entry)", source)

    def test_the_class_a_builders_are_shared_too(self):
        source = (REPO / "packages" / "schema" / "backfill.py").read_text()
        self.assertIn("built = score_row(record)", source)
        self.assertIn("employer_rows(records).items()", source)

    def test_the_session_builder_reads_every_field_it_owns(self):
        built = class_b.session_row({
            "status": " watch ", "phase": " Queued ", "fitScore": 89,
            "coreSkills": ["product-management"], "alignment": "Match 89% ...",
            "droppedFrom": "Watching", "droppedStage": "not started",
            "seenOnBoards": 2, "postedDate": "2026-08-19",
            "pendingFields": [{"question": "code?"}]})
        self.assertEqual(built["status"], "watch")
        self.assertEqual(built["phase"], "Queued")
        self.assertEqual(built["fit_score"], 89)
        self.assertEqual(built["core_skills"], ["product-management"])
        self.assertEqual(built["dropped_from"], "Watching")
        self.assertEqual(built["seen_on_boards"], 2)
        self.assertEqual(built["posted_at"], "2026-08-19")
        self.assertEqual(len(built["blockers"]), 1)

    def test_an_absent_field_is_none_not_a_missing_key(self):
        """The session file is the authority on its own state, including when
        it says nothing: a missing key has to reach the column as NULL, or a
        cleared value is kept forever. That is the Harvey phase bug."""
        built = class_b.session_row({"status": "x"})
        self.assertEqual(set(built), set(class_b.session_row({})),
                         "the key set must not depend on what the file has")
        for name, value in built.items():
            if name == "status":
                continue
            self.assertIsNone(value, f"{name} should be None, got {value!r}")

    def test_the_role_builder_owns_the_name_and_nothing_else(self):
        built = class_b.role_row({"company": " Hex ", "role": "PM",
                                  "url": "https://x/1", "region": "US",
                                  "source": "greenhouse", "ats": "greenhouse",
                                  "status": "watch"})
        self.assertEqual(built["company"], "Hex")
        self.assertNotIn("status", built,
                         "roles.json is an index of roles seen, not a record "
                         "of state: passing a status from here once overwrote "
                         "all 88 real ones with the word 'known'")
        self.assertEqual(set(built) & set(class_b.session_row({})), set(),
                         "two writers, disjoint columns, one row")


class ValuesAreComparedNotReprs(unittest.TestCase):
    """The comparator, which decides whether the widening is usable at all."""

    def test_a_timestamp_matches_its_iso_string(self):
        import datetime as dt
        stored = dt.datetime(2026, 8, 11, 17, 58, 7, tzinfo=dt.timezone.utc)
        self.assertTrue(verify_parity.same_value("2026-08-11T17:58:07Z", stored))

    def test_jsonb_matches_the_json_string_the_mirror_sent(self):
        self.assertTrue(verify_parity.same_value(
            '{"a": 1, "b": [2]}', {"b": [2], "a": 1}))

    def test_a_number_matches_the_text_column_it_was_written_into(self):
        self.assertTrue(verify_parity.same_value(23, "23"),
                        "job_scores.config_version is text and the pool "
                        "carries an int; 22,001 findings came from this")

    def test_emptiness_is_one_value(self):
        for pair in ((None, ""), ("", None), (None, []), ("  ", None)):
            self.assertTrue(verify_parity.same_value(*pair))

    def test_it_still_says_no(self):
        """The control. A comparator that returns True is not a comparator."""
        for pair in (("a", "b"), (1, 2), ("23", "24"), ({"a": 1}, {"a": 2}),
                     ("2026-08-11T17:58:07Z", "2026-08-12T17:58:07Z"),
                     ("1", "1.0")):
            self.assertFalse(verify_parity.same_value(*pair),
                             f"{pair!r} compared equal")


if __name__ == "__main__":
    unittest.main()
