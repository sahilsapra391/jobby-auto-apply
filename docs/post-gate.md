# After the parity gate closes

Work that is deliberately held until the three-day parity clock has run,
because each item touches a gate path (`scripts/verify_parity.py`,
`class_b.py`, `dual_write.py`, `jobs_store.py`, `packages/schema/backfill.py`,
`packages/schema/board_key.py`, `scripts/mirror_fields.py`) and would restart
it.

Add to this list rather than taking three days off the clock for a change that
can wait. Take the whole list in one change when the gate opens, for the same
reason: one reset, not five.

## Split the 16 unseen columns into exempt and defect

`scripts/parity_coverage.py` reports three states -- compared, exempt with a
reason, and unseen -- and on 2026-09-02 the numbers were 87, 22 and 16.

**"Unseen" is currently ambiguous and that is a known, temporary cost.** Some
of the 16 are plainly not content and want an `EXEMPT` entry; some are real
gaps in what parity compares. As it stands the number cannot tell you which,
which is exactly the conflation the exempt/unseen split exists to prevent, one
level down.

They have no entries because `mirror_fields.EXEMPT` is a gate path and adding
them would have cost three days at the moment the clock had just started.

    application_events   id
    applications         created_at, id, posting_id, session, session_deleted_at
    employers            created_at, id, merged_into
    job_decisions        decided_at
    job_scores           computed_at
    postings             body_r2_key, id, merged_legacy_ids, search_tsv
    profiles             base_resume_text

Most look like Postgres-assigned identity and timestamps (`id`, `created_at`,
`decided_at`, `computed_at`), a generated column (`search_tsv`), and mirror
bookkeeping (`merged_legacy_ids`, `merged_into`) -- all of which want an
exemption with its reason rather than a comparison.

Three need a decision rather than a note:

- `applications.session` -- **do not declare it** until somebody has measured
  whether it duplicates `blockers`. That was settled earlier and stands.
- `profiles.base_resume_text` -- is this live, or a column the Profile page
  stopped writing? Check before exempting.
- `postings.body_r2_key` -- the posting body lives in R2; whether parity should
  compare the key is a real question, not a formality.

One change, one clock reset, every entry carrying its reason.
