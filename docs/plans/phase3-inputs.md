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

## 10. §3 cannot name an application, and `roles` is not the way out

`applications` holds no company and no role. An application lane rendered from
it shows an opaque legacy id and nothing a person recognises, which is what
Watching and Needs you looked like the first time they drew real rows.

**The obvious fix is to join `roles`, and it is wrong.** That table has both,
on the same key, for 196 rows -- and no migration creates it. It is a pre-§3
`jobs_db` table, like `sessions` and `history`, and nothing maintains it:
`class_b._mirror_roles` says so in its own docstring and mirrors `roles.json`
as *applications* instead, on the grounds that inventing a `roles` table would
be a fifth spelling of the same fact.

Demonstrated rather than argued, on 2026-09-01: writing a role through
`store.py role-put` returned `remembered: true`, the file took it, and the
table stayed at 196.

**This was written up once as "`roles` is mirrored and the parity gate cannot
see it". That was wrong and the correction is the point.** The gate is not
blind to a mirrored table; the table is not mirrored, and not part of the
schema. It is the same mistake as reading `history` for the Submitted lane,
made one function later, in the same afternoon, by someone who had just caught
it. A join that works on this machine and returns nothing on a clean
deployment is the launchd PATH bug's shape, and knowing the shape is evidently
not enough to stop making it.

So the lanes show the legacy id until §3 carries a name. Two ways to give it
one, and section 5 argues for the second:

- company and role on `applications`, which duplicates what the posting
  already holds;
- `applications.posting_id`, which is NULL for all 224 rows and which every
  other Phase 3 item needs anyway.


## 5. What it would take to give an application a company and a role

**Measured 2026-09-01, because this now blocks the most visible part of the
board rather than five items in a document.** Every application lane on v2
draws a legacy slug where the board draws a company and a role, and
`applications.posting_id` is NULL for all 204 rows.

### The frozen table names 96% and is the wrong answer

`roles` has `application_id, company, role, url, record` and covers **196 of
204**, all 196 with both fields. Per lane it would name Submitted 104 of 104,
Dropped 57 of 57, Watching 15 of 18, Needs you 16 of 21.

It is also a snapshot that no migration creates and nothing maintains, exactly
like `history` and `jobs`. The eight it misses are the newest applications,
which is the tell: coverage does not stay at 96%, it decays from it, and it
decays silently because a missing name looks the same as a name that was never
there. Reading it to fill a column is the `answers.json` join in different
clothing -- today's snapshot presented as the record of what happened.

### The honest backfill resolves 61%

Resolving `posting_id` properly, against live `postings`:

```
exact roles.url -> postings.apply_url     103
board key (slug, external id)              13
legacy_id -> postings.legacy_id             9
                                          ---
                                          125 of 204   (61%)
```

The 79 that do not resolve are not a parser weakness and Phase 3 should not try
to close them by parsing harder. They are §4's categories again plus one new
one:

- the posting is no longer in the pool, so there is nothing to point at
- aggregator redirects (`jobviewtrack.com/v2/...`), which carry no board identity
- employer-own domains carrying a `gh_jid` (Duolingo, Dropbox, Coupang)
- Workable short links (`apply.workable.com/j/AA703B6828`)

**A posting that has been pruned cannot be resolved to, at any coverage.** That
is a ceiling on this approach rather than a gap in it, and it is the reason the
answer is 61% and not 90%.

### So it is two changes, and only one of them is a backfill

**New applications need `posting_id` written when they are created.** The id is
known at that moment: the click that creates an application is a click on a
posting. Nothing has to be inferred. This is a mirror change on the promote
path, which is a Phase 1 gate path, so it restarts the parity clock and wants
to be taken deliberately rather than folded into UI work.

**Old applications get the 61% backfill above**, and the rest keep showing what
they have. Do not close the gap by falling back to `roles` for the remainder:
that is the frozen snapshot again, and mixing a resolved id with a snapshot
lookup in one column produces a display where nobody can tell which is which.
If the remainder needs a name, it needs its own field saying where the name
came from, the way `answer_record`'s `capture` level does.

### Two ways to get this wrong, both measured

**Matching on a normalised URL would collide.** `norm_url` strips queries, and
4,202 of 14,569 pool jobs carry their identity in the query; one recorded Stripe
role already made `seen.check` call every other Stripe job known. The 103 above
are *exact* `apply_url` matches, which is safe. A normalised match is not.

**Trying every source to parse a board key is ambiguous in principle.** It was
measured at zero ambiguity here (0 of 196 URLs parsed under more than one
source), but that is a property of this corpus, not of the method.
`roles.record` carries a `source` for 172 of 196 and an `ats` for 155, so a real
backfill should use the recorded source and treat a URL with none as
unresolvable rather than guessing across all sixteen.

### What the backfill actually did, and which part Phase 3 closes for free

Run against production on 2026-09-01 with `scripts/link_applications.py
--apply`. **121 of 204 linked, 59%.** Every other table count unchanged.

```
via apply_url    112      exact equality, never a normalised url
via board_key      8      (source, board_slug, external_id)
via legacy_id      1
                 ---
                 121 of 204
```

The URL to resolve came from two places and the report keeps them apart:

```
session          61      the device's own session files, live
roles-snapshot   60      the frozen table, used for the URL only
```

**Using the snapshot for a URL is a different act from using it for content,**
and the distinction is the whole reason `roles` is otherwise refused. The value
stored is the uuid of a row that is in `postings` at resolution time, checked
then. Reading `roles.company` onto the board would instead put a snapshot's
content on screen as though it were current, which is the `answers.json` join
in different clothing. The report prints the split so a reader can tell which
applications were named through which.

#### The 83 that did not resolve, by reason

```
the url carries no board key      52
no posting in the pool matches    31
```

**A connector closes the first 52 and cannot touch the second 31.** §4 already
says why: a connector fetching `boards.greenhouse.io/<slug>` knows its
`(source, board_slug, external_id)` by construction and does not have to derive
them from a URL. That is exactly the population in the first bucket, which is
§4's shortfall categories again:

- Greenhouse postings served from an employer's own domain, where the URL
  carries a `gh_jid` and no board slug (Duolingo, Dropbox, Coupang)
- Workable short links (`apply.workable.com/j/AA703B6828`)
- aggregator redirects (`jobviewtrack.com/v2/...`), which carry no board
  identity at all and which a connector reaches directly instead

The other 31 are postings that have been pruned from the pool. **There is no
row to point at, so no amount of connector coverage recovers them.** That is a
ceiling on this approach rather than a gap in it, and it is the reason the
answer is 59% and not 90%. An application whose posting has aged out keeps its
legacy id and says "posting not linked" rather than showing a blank.

#### One resolver, both directions

`scripts/posting_link.py` is the only place this decision lives, so the
backfill over history and anything writing a new application cannot disagree
about what the column means. Two refusals in it are load-bearing and both are
verified by disabling them: it never matches a query-stripped URL (4,202 of
14,569 pool jobs carry their identity in the query, and one Stripe role already
made `seen.check` call every other Stripe job known), and it never guesses a
source to derive a board key with (trying all sixteen patterns measured zero
ambiguity on this corpus, which is a property of the corpus and not the method).


## 11. A careers host recorded as an employer domain, 24 of 351

Added 2026-09-03. This came out of the employer-resolver sketch as a case the
resolver should refuse, and it is not a resolver problem at all. It is an ingest
defect, and the resolver is only where it becomes visible.

```
TelusDigital    telusdigital.com        55 postings
TELUS Digital   jobs.telusdigital.com   12 postings
```

Two employer rows, both with a domain, and the domains disagree, so neither
domain equality nor the no-domain squash rule can touch them. But the second
domain should never have been written. `jobs.telusdigital.com` is where TELUS
advertises jobs; the employer is `telusdigital.com`.

**Measured: 24 of the 351 employers holding a domain (6.8%) have one whose first
label is not the company.** Nineteen careers hosts and five blogs:

```
careers.airbnb.com     careers.robinhood.com   careers.roblox.com
careers.snowflake.com  careers.adyen.com       careers.pagerduty.com
careers.deliveroo.co.uk careers.mckesson.com   careers.calendly.com
careers.doctolib.com   careers.feedzai.com     careers.hootsuite.com
careers.oatly.com      careers.swissport.com   careers.toasttab.com
career.proxify.io      jobs.funnel.io          jobs.spectrum.com
jobs.telusdigital.com
blog.duolingo.com      blog.cloudflare.com     blog.coursera.org
blog.1password.com     blog.faire.com
```

`blog.duolingo.com` is already on record as one of the eleven
`employer_field_pinned` parity disagreements, where the pinned value is the
worse one. **Its cause is now identified rather than noted.**

### Two causes, both reproduced

`company_logos.website_from_description` returns the **first** link in the
posting body whose host contains a token of the company name. Run against real
input:

```python
website_from_description("TELUS Digital",
    '<a href="https://jobs.telusdigital.com/careers/1234">Apply</a>'
    '<a href="https://www.telusdigital.com">About us</a>')
-> "https://jobs.telusdigital.com"
```

**First match wins, with no ranking.** On a job posting the apply link precedes
the corporate one essentially always, so the ordering is not bad luck, it is the
common case. Duolingo is the same shape with a blog link in front.

**And there is no rejection of an employer's own careers host.** A third-party
ATS host is caught by `_is_infrastructure_host` (`boards.greenhouse.io` and
`apply.workable.com` both return True), and `jobs.telusdigital.com` and
`careers.duolingo.com` both return False. The filter knows about other people's
ATSs and not about the employer's own.

### A third hazard in the same function, latent rather than live

The host test is `token in compact_host`, a substring with no label boundary,
which is the failure this repository's CLAUDE.md already records three instances
of. Reproduced:

```
Ford      -> https://www.stanfordalumni.com
Vast      -> https://devastating-labs.io
Apex      -> https://grapexchange.net
```

`Vast` is a real employer here with 61 postings; it currently holds no domain,
so this one has not fired yet. `Ford` and `Apex` are not in the store. Calling
it latent rather than live is the honest reading, and it is one posting body
away from either.

### What Phase 3 should do about it

Rank the candidates rather than taking the first, refuse a host whose first
label is a careers or content label, and match on label boundaries rather than
substrings. All three are in one function. The 24 existing rows are then a
backfill, and until they are corrected the employer-resolver's "both have a
domain and they differ" refusal is doing this defect's containment for it, which
is the wrong place and should not be mistaken for a fix.


## The three attributes nothing extracts, measured 2026-09-02

`extracted.degree`, `extracted.employmentType` and `extracted.roleCategory` are
populated in **0 of 22,762** pool records. Not a regression and not a v2 fault:
`extract.extract()` returns `{yearsMin, yearsMax, yearsText}` and never
attempts the other three, so the old board's Degree, Role and Job type filters
have been decorative for as long as they have existed. It says so itself, in
`found_lane.py`: "degree, role and job type are not extracted yet".

What `extracted` does carry is the years triple, in 9,483 records, 41.7%.

So the filters are honest rather than broken -- they filter the unknown bucket
and the board's note explains that -- and the work is an ingest one: an
extractor that reads a description for a degree requirement, an employment
type and a role category. It belongs here rather than with the board, and
building the pickers before it exists would ship three controls that can only
ever match nothing.
