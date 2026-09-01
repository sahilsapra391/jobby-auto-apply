# What Phase 3 should know before the connectors are written

Design inputs, measured during Phase 1. Nothing here is built. It is written
down now because all of it is far cheaper to know before §5's connectors exist
than after, and because two of the three findings contradict what a reasonable
person would assume.

## 0. Where these numbers may be re-measured, and where they may not

**Every timing figure in this document was measured against Neon. None of them
may be re-measured on the local `postgres:18` container.**

That container arrived on 2026-08-30 and it is the right place for correctness
work: the database suites run 233 tests in 5.3 seconds against it versus 261
against Neon, so the keyset walks and lane comparisons that used to be
sampled can now be run exhaustively. Do that.

But it is not the machine the product runs on, and the differences are exactly
the ones every number below depends on:

| | Neon | the container |
|---|---|---|
| network | a round trip per statement, measured at ~60 ms | a unix-domain hop |
| pooler | the configured endpoint is pooled | none |
| storage | separated compute and storage | local disk |
| cold start | compute suspends and resumes | always warm |

So the split is by what is being asked, not by convenience:

    correctness  -- does the query return the right rows, in the right order,
                    without repeating or skipping        -> the container
    timing       -- how long does it take, how does it scale with payload,
                    what is the p95                      -> Neon, always

Everything in sections 1 and 2 below is the second kind. The 0.83 s per MB
payload curve, the 6.0 ms per changed row, the 15% narrow-update figure, the
GIN and tsvector shares: re-measuring any of those on the container produces a
faster number that is true about the container and false about the product, and
a faster number is exactly the kind nobody questions.

The Phase 2 gate is the same category. **"p95 click-to-visible under 250 ms, on
a real device over a real network, recorded"** is not satisfiable by a local
benchmark, and it says so in its own wording. A local number that clears it
proves nothing at all.

## 1. Most of the mirror's work is a timestamp, and that is the big one

The tier-1 sweep of 2026-08-30T03:49:15Z, the first with dual-write live, put
4,960 changed rows through the mirror in 29.75 s. The shape of those changes
matters more than the total:

```
added       223      genuinely new postings
changed   4,673      overwhelmingly a lastSeenAt refresh and nothing else
removed      64      pruned
```

**94% of the mirror's cost is a timestamp that nothing reads for correctness.**
`last_seen_at` drives one thing, the closure sweep: a posting present in the
last sweep and absent from this one gets `closed_at` set. No lane sorts on it,
no filter uses it, no document references it. The board's newest-first ordering
is `posted_at`.

Real churn was 287 rows, 223 added and 64 removed. The other 4,673 were rows
whose content did not change at all.

So the §11 projection of ~152,000 rows and ~15 minutes of mirroring per sweep
at 500k postings is measuring the wrong thing. It projects the *refresh* rate,
not the *change* rate, and those differ by a factor of sixteen on the one sweep
anybody has measured.

### The shape of the fix, not the fix

`last_seen_at` written separately from the row it belongs to. Either a narrow
companion table keyed on `posting_id`, or a bulk

```sql
update postings set last_seen_at = v.seen
from (values (%s, %s::timestamptz), ...) as v(legacy, seen)
where postings.legacy_id = v.legacy
```

touching that column and no index. Measured below at **15% of the cost** of the
equivalent full-row upsert, and the reason is section 2 rather than anything
about indexes.

**Not built, deliberately.** It changes `dual_write`, which is a Phase 1
gate path: any change there restarts the seven-day parity clock at zero. It is
also the kind of optimisation that should be designed against §5's real
connector upsert rather than retrofitted onto the file-store mirror, since the
connectors are the caller that will actually pay for it.

Two things whoever builds it has to keep. A posting whose `last_seen_at` moved
and whose content also changed must take the full path, so the narrow write is
an optimisation of one case rather than a second way to write a row. And the
closure sweep must read whichever column ends up holding the value, which is a
one-line change today and a two-place invariant forever if the narrow write
lands in a companion table.

## 2. The cost is the payload, not the index, and the obvious guess was wrong

The first write-up of the sweep named the generated `search_tsv` column and its
GIN index as the prime suspect: linear in the delta, and exactly what an
ordinary upsert does not pay. It reads well and it is wrong.

Timed in a scratch schema over 5,000 rows, row count and statement count held
fixed:

| variant | seconds | ms/row | isolates |
|---|---|---|---|
| `full` | 9.38 | 1.88 | as shipped: generated tsv + GIN |
| `tsv_only` | 8.67 | 1.73 | generated tsv, no GIN |
| `plain` | 8.57 | 1.71 | neither: the floor |
| `narrow` | 1.40 | 0.28 | `last_seen_at` only, full table |

GIN maintenance is **8%** of the cost. The tsvector computation is **1%**.
Together under a tenth, and an optimisation aimed there would have bought
almost nothing.

Varying only how wide each row is, same 5,000 rows in the same ten statements:

| summary | payload | seconds | ms/row |
|---|---|---|---|
| 180 words | 8.0 MB | 8.12 | 1.62 |
| 40 words | 2.2 MB | 3.29 | 0.66 |
| empty | 0.6 MB | 2.02 | 0.40 |

Linear at roughly **0.83 s per MB** over the wire. Not the indexing, and not
the round trips either: all three ran in ten statements, which at Neon's
measured 60 ms is under a second of latency.

**So the lever is how much of each row is sent.** That is why the narrow
`last_seen_at` update is 15% rather than 50%: it ships about thirty bytes a row
instead of a kilobyte and a half. It is the same finding as section 1 from the
other direction.

A caution on transferring these numbers. The benchmark table has 10 columns and
one index; the real `postings` has 33 columns and six, plus a foreign key and
four check constraints, and the real mirror also upserts employers and
decisions. The real sweep measured 6.0 ms per changed row against the
benchmark's 1.62. **The ratios transfer; the absolute figures do not.**

## 3. Employer resolution has to exist before the first sweep upserts

Covered in full in `docs/employer-identity.md` and repeated here because it is a
Phase 3 blocker rather than a nicety.

§5's connector upsert keys on `(source, board_slug, external_id)` and takes the
employer from whatever name the board's API returns. That name arrives fresh
every sweep and is not the name the backfill normalised. Without a resolution
step in front of the upsert, every spelling variation an ATS emits creates a new
employer row while the postings scatter across them.

The signal the backfill was confident about is already recorded:
`employers.merged_into`, set on an exact normalised-domain collision. Nothing
reads it yet. One collision exists in 1,934 employers, `sharegate.com` claimed
by `sharegate` and `sharegate migrate chezworkleap`.

A wrong merge files one company's postings under another's name, which is the
stranger's-logo failure at the scale of a board lane. A missed merge shows the
same company twice, which is untidy and correct. Prefer the second.

## 4. What the board key already tells you about connector coverage

`packages/schema/board_key.py` derives `(board_slug, external_id)` from a URL,
which is what backfill and dual-write need and what a connector does not: a
connector fetching `boards.greenhouse.io/<slug>` knows all three by
construction and should pass them rather than re-deriving them.

Measured over 20,103 records, a real tenant slug is found for 79.2% of postings
from a source that has a pattern. The shortfall is not parser weakness, and
Phase 3 should not try to close it by parsing harder:

- aggregators and feeds have no ATS tenant at all
- 3,528 Greenhouse postings are served from an employer's own domain, where the
  URL carries the job id and not the board slug
- 144 Workable short links carry no slug
- 29 Recruitee and Teamtailor postings sit on custom careers domains

For all of these the fallback `(source, record_id)` is the honest key, and a
connector that knows its own board replaces it with the real one for free.

## 8. A session deletion is not mirrored, and it leaks

A session file is deleted when its application completes; the `applications`
row keeps whatever status it last had, forever. Nothing clears it and nothing
notices.

Measured 2026-09-01: 108 applications have no session file. 107 carry
`unknown` or `known`, which is `roles.json` doing its job. **One carries
`active`** -- `actively-ai-agent-product-manager` -- so a lane reading
`applications.status` puts it in Needs you while the old board, which buckets
sessions, puts it nowhere.

One row today, and it is a slow leak rather than a one-off: every future
deletion adds another, and each one is a role the board says is waiting for
you when it is not. Parity does not see it, because a session absent from the
file store is `only_in_postgres`, which is the exemption section 6 is about.

The fix belongs with whatever mirrors deletions, which nothing currently does.

## 9. A retitle is a false collision under a URL-derived key

Bitdeer's "Operations Technician" was captured twice: the employer retitled it,
the slug moved with the title, and the scraper took it as a new posting. The
two records share `(breezy, bitdeer, eeab1edbc9db)`, so they collapse into one
row and every field that moved with the title -- title, normalised title, apply
URL -- shows up as a merged-field drop.

**That is one posting merged with its own past, not two records for one job.**
The distinction matters because the remedy is different: there is nothing to
reconcile between two employers' listings, only a stale capture of the same one.

**And it is normal traffic rather than a curiosity.** The collision count went
2, then 5, then 7 within two days of anyone looking. §5's connector upsert will
meet this constantly, and a connector that re-derives its key from a URL will
keep manufacturing pairs every time a title changes.

A connector fetching `boards.greenhouse.io/<slug>` knows its `source`,
`board_slug` and `external_id` by construction and never has to look at the
URL, so it sidesteps the whole class. That is the second argument for passing
the key rather than re-deriving it -- section 4 gives the first, which is that
79.2% is as far as derivation gets on the corpus that exists.
## 5. An application is not linked to its posting, and Phase 3 needs it to be

**`applications.posting_id` is NULL for all 224 rows.** Measured 2026-08-31.
The column exists, the foreign key exists, and nothing has ever populated it.

That does not block the v2 lanes, which read `applications.status` and the
history events directly. It blocks three things that all arrive in Phase 3:

- a Submitted card cannot link to the posting it came from, because it does
  not know which one that is;
- dedup against the pool has nothing to dedup on;
- §5's connector upsert cannot reconcile a re-seen job with an application
  already made against it, which is the case that produces a duplicate
  application under a real name.

It also explains a delta that looks like a bug and is not. The pool holds
`passed 9` and the applications hold `dropped 57`; those are different
populations -- a posting you pressed Pass on, versus an application you
abandoned after it existed -- and with no link between them they can never
agree. Anyone comparing the two counts will keep rediscovering that.

The backfill has the material to do it: an application's legacy id and the
posting's `apply_url` and `(source, board_slug, external_id)` are both in the
store. It was not done because the board key work landed after the application
backfill, and nothing since has needed it.

## 6. The parity gate cannot see extra applications

`verify_parity.compare_class_b` reports `only_in_file` for sessions and
deliberately not `only_in_postgres`, because `roles.json` legitimately creates
`applications` rows that the sessions directory never had. That exemption is
correct and should stay.

The cost is that **anything which appears in `applications` from nowhere is
invisible to the gate**. Measured 2026-08-31: 27 test fixtures -- `a`, `b`,
`c`, `nw-pm`, `criteo`, `acme-pm`, `hostile` -- sat in production for hours
while parity read green on every run.

The fix is not to remove the exemption. It is to distinguish "created by
roles.json" from "appeared from nowhere", which the store can answer: a row
whose legacy id is in neither `sessions/` nor `roles.json` is in the second
category. That is a change to a gate path, so it waits for the clock.

## 7. Three spellings of session status, and one is dead

The status of an application exists in three places: the session files under
`sessions/`, the `applications` table, and a `sessions` table.

`sessions` is left over from `jobs_db.SCHEMA`, the pre-§3 layout. Nothing in
the §3 path reads it -- the only reference anywhere is a table name in a counts
helper at `jobs_db.py:307` -- and it is already wrong: 88 rows against 89
session files, missing the Toast watch driven on 2026-08-30.

**It is a deletion candidate rather than a repair.** Fixing it would mean
maintaining a third copy that nothing consumes, which is the same argument the
`jobby:doc:*` KV copies are retired under. Parity compares the files against
`applications`, which is the pair that matters.
