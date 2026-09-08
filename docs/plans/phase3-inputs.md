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

### 9a. The fold keeps the expired record, measured 2026-09-03

**§9 says the two records are one posting and that is right. Which of them
survives the fold is a separate question, and today the answer is the wrong
one.**

All three collisions in the store share one signature. The employer edits the
posting, Breezy keeps the id and rewrites the slug, the URL-keyed sighting
misses it, the sweep expires the old record, and five or six minutes later
ingests the new slug as a new record. The lifetimes never overlap:

```
breezy/bitdeer/eeab1edbc9db        closed 08-31 19:41   next seen 19:47
breezy/surge/f8b44d25ba27          closed 09-02 15:47   next seen 15:52
breezy/nationsbenefits/8afada1bbe99  closed 09-03 19:43   next seen 19:48
```

`postings` then holds **one row per pair and it is the closed one**. The live
record has no row of its own; it is filed under the expired one in
`merged_legacy_ids`.

```
1afe6cbad39c16b2   closed 2026-09-02   $65,000 - $85,000
                   "Operations Manager - 2nd & 3rd Shift"

live posting at the same Breezy id, no row:
f4ca7580de7eacac   open                $50,000 - $55,000
                   "3rd Shift Electronics Operations Supervisor"
```

**This is a cutover item, not only a Phase 3 one.** v2 reads Postgres, so it
shows three postings as closed that the Python board shows as open, at the
previous title and the previous pay. Three of roughly 17,000 today, and the
mechanism runs every time an employer retitles, so it accumulates rather than
sitting still. The three collisions went 2, then 5, then 7 in two days when
somebody last counted, and today's number is a snapshot of the same drift.

Winner selection is the fix and it is a one-line rule: **between records
sharing a board key, the one that is still live wins.** It belongs in
`backfill.upsert_postings` alongside the `on conflict` work, and it is not made
here because a mirror change is the user's call.

**Parity is right to be silent about it**, which is worth stating because the
silence looks like coverage. Both stores agree; the fold is deterministic and
the mirror faithfully reflects what it decided. This is the `Appcast
Enterprise` lesson one table over: a green parity run means the mirror lost
nothing on the way across, and says nothing whatever about whether what
crossed was right.

**And nothing would have found it except running the tests.**
`test_every_dropped_field_is_one_a_merge_may_discard` had been failing on
`main` and `test_every_colliding_key_is_a_genuine_duplicate_posting` was about
to. Both skip in CI, and CI is the only place the suite runs. The salary pair
is the reason the hourly corpus check exists.

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


## 11. 9.7% of employer domains are not the employer

Added 2026-09-03, out of the employer-resolver sketch. TELUS is how it was
found; it is not the size of it.

**34 of the 351 employers holding a domain hold one that does not belong to the
employer. 1,187 postings sit behind them.** This is not an edge case and the
names are not obscure ones:

```
careers.snowflake.com    157      careers.robinhood.com     52
careers.adyen.com        114      blog.duolingo.com         33
careers.deliveroo.co.uk   98      blog.1password.com        31
harveyai.notion.site      83      careers.doctolib.com      26
careers.toasttab.com      84      careers.pagerduty.com     26
careers.airbnb.com        82      launchdarkly.slack.com    24
blog.cloudflare.com       73      dropbox.github.io         21
careers.roblox.com        71      notion.notion.site        57
```

Two shapes, and they do different amounts of damage.

**28 have a first label that is not the company** -- `careers.`, `jobs.`,
`blog.`, plus `engineering.`, `images.`, `identity.` and `insideout.`. The
favicon of `careers.snowflake.com` is usually Snowflake's, so the render mostly
survives. The damage is in the data: the stored domain is what the employer
resolver keys on, which is how TELUS ended up as two employers, and it is what
parity reports as an `employer_field_pinned` disagreement, which is how
`blog.duolingo.com` got onto that list.

**6 are hosted on somebody else's platform**, and those do draw a stranger's
mark, because the favicon belongs to the host:

```
Harvey        harveyai.notion.site      83 postings
Notion        notion.notion.site        57
LaunchDarkly  launchdarkly.slack.com    24
Dropbox       dropbox.github.io         21
Vonage        ericssonab70331551vonagepro-my.sharepoint.com   11
Hex           hexhq.notion.site          4
```

Dropbox draws GitHub's octocat. Harvey and Hex draw Notion's. That is the
stranger's-logo failure `company_logos.py` was built to prevent, arriving
through the one path that does not go through its trusted-host check.

### Two causes, both reproduced

`company_logos.website_from_description` returns the **first** link in the
posting body whose host contains a token of the company name:

```python
website_from_description("TELUS Digital",
    '<a href="https://jobs.telusdigital.com/careers/1234">Apply</a>'
    '<a href="https://www.telusdigital.com">About us</a>')
-> "https://jobs.telusdigital.com"
```

**First match wins, with no ranking.** On a job posting the apply link precedes
the corporate one essentially always, so this is the common case rather than bad
luck. Duolingo is the same shape with a blog link in front.

**And there is no rejection of a host by its role.** `_is_infrastructure_host`
returns True for `boards.greenhouse.io` and `apply.workable.com` and False for
`jobs.telusdigital.com`, `careers.duolingo.com` and `dropbox.github.io`. The
filter knows about other people's ATSs and not about the employer's own careers
host, and not about general-purpose hosting at all.

### The substring host test, and why the obvious fix is worse

The host test is `token in compact_host`, a substring with no label boundary,
which is the class this repository's CLAUDE.md already records three instances
of. It reproduces:

```
Ford  -> https://www.stanfordalumni.com
Vast  -> https://devastating-labs.io
Apex  -> https://grapexchange.net
```

`Vast` is a real employer here with 61 postings and no domain yet, so it is one
posting body away rather than hypothetical.

**A label-boundary test is the obvious fix and the corpus refuses it. Measured
before writing it down.** 23 of the 351 stored domains match only as an embedded
substring, and the clear majority of those are correct:

```
Oscar Health  hioscar.com          Peloton     onepeloton.com
Brightwheel   mybrightwheel.com    Persona     withpersona.com
Moss          getmoss.com          Recharge    getrecharge.com
Stream        getstream.io         Via         ridewithvia.com
```

`get-`, `my-`, `with-`, `one-`, `hi-` and `ride-` are a standard domain
convention, not a collision. A boundary test loses all eight of those and 188
postings with them.

The next idea is a boundary test that tolerates a leading affix and refuses a
trailing word. That was written and run against all 351: **it refuses 29 and
about sixteen of the refusals are correct domains.** `davies-group.com`,
`redditinc.com`, `cusointernational.org`, `flockcover.com`, `toasttab.com`,
`surferseo.com` and `ouraring.com` all have a word after the name and all belong
to the company. Five more are a public-suffix bug -- `tiro.co.uk`,
`mable.com.au`, `paypay.ne.jp` -- because taking the last two labels as the
registrable domain is wrong for multi-part suffixes, and doing it properly needs
a public-suffix list this codebase does not carry.

**So the trailing word is not a signal, and the third rule of this design pass
to be killed by the corpus is killed here.** Across all 351 stored domains the
substring test has produced exactly **one** likely bad row that the role rules
below do not already catch: `ATOM PARTNERS LIMITED -> atomsmiths.com`, one
posting, where the token `atom` is followed by `smiths`. Not verified against
the real company, and stated as a candidate rather than a fault.

### What actually works: reject on the host's role, not on how the name matches

The two rules that separate signal from noise in this corpus do not look at the
name at all.

```
1  refuse a host whose first label is a careers or content label
   catches  28 of 351, including every careers. and blog. row above

2  refuse a host on a general-purpose hosting domain
   catches  6 of 351, and these are the ones that draw a stranger's mark
```

Both are exclusion lists, which is what the substring finding argues against,
and the distinction is worth stating rather than glossing: an exclusion list on
a **host's role** is a small closed set that changes slowly, while an exclusion
list standing in for a **name-matching rule** is the thing that grows forever
and is always one company short. The existing `_is_infrastructure_host` is the
first kind and its only defect is being incomplete.

Then rank rather than take the first: prefer a candidate whose registrable
domain is not the posting's own ATS host, prefer the shortest host, and prefer a
bare `www.` or apex over any subdomain. TELUS resolves correctly under ranking
alone, without any change to the matching.

The 34 existing rows are a backfill. Until they are corrected, the employer
resolver's "both have a domain and they differ" refusal is containing this
defect rather than fixing it, and the two must not be confused.


## 11b. A deployed decision rule reaches only what the next sweep touches

**The mirror writes a delta, so changing a rule does not change the rows the
rule already decided.** `dual_write.persist` computes `added | changed` and
passes only those to `backfill.upsert_postings`; a record that did not move is
never re-decided.

Measured 2026-09-06, and it is written here because the prediction was wrong in
public. §9a's fix to `BOARD_KEY_WINNER` -- prefer a live posting over a closed
one -- was verified by driving `upsert_postings` with the three real pairs, and
reported as "self-corrects on the next sweep". The next sweep ran for 1443
seconds, completed normally, and flipped **0 of 3**. All six records were last
seen 2026-09-04; none was in that sweep's delta, so none reached the new rule.

The verification was sound and the conclusion drawn from it was not. Driving
the function proves the decision; it says nothing about what the pipeline
feeds the function. Those are different claims and only the first was checked.

**So any change to a decision rule needs a backfill step, or it applies to new
collisions only.** `scripts/backfill_fold_winners.py` is that step for one
named set: it re-runs `upsert_postings` over the six records alone, reads
employer ids rather than upserting them, and prints before and after because
an exit code is not evidence. It is deliberately not a general re-mirror -- the
delta path is correct and a script that re-mirrors everything to fix three rows
is a standing invitation to bypass it.

**Phase 3's employer resolver meets this on day one.** Its whole purpose is to
decide differently about employers already stored: `Appcast Enterprise` under
two spellings, the 11 pinned domains, the 34 hosts that are not the employer.
Every one of those is a record the next sweep has no reason to touch, so a
resolver shipped without a backfill would correct new arrivals and leave the
corpus that motivated it exactly as it is -- and would look like it worked,
because new collisions would resolve correctly.

Budget the backfill with the rule, not after it.

### 11c. A row that references a uuid is a witness the comparison cannot be

**Parity cannot prove a uuid survived a fold, and the scores table can.**

The fold rewrites which record a `postings` row holds. The question afterwards
is whether the row kept its uuid or got a new one, because `applications.
posting_id` and `job_scores.posting_id` both point at it and §5 is about
building more of those links. Parity compares the device's pool against the
mirror, and the device's pool has no uuids in it at all -- they are minted on
the Postgres side -- so a green parity run is silent about exactly this.

The proof is a third table that already holds the answer. A `job_scores` row
can only carry a uuid that existed when the row was written, so a score
computed *before* the fold and still joining afterwards is a uuid that did not
move. Measured 2026-09-06, after `backfill_fold_winners.py --apply`:

```
3c12eb512130a4bb  4d55ecc6-3fb8-431f-9f58-176fec9cba26  closed=false
                  score computed 2026-09-02 22:28:56Z
f4ca7580de7eacac  5b3fdbdc-e4df-46d5-8d23-2a00a1d0660e  closed=false
                  score computed 2026-09-02 22:28:56Z
d0710a7fb465bcff  aac3d5e0-aa3f-4997-9bba-1e231b568554  closed=false
                  score computed 2026-09-03 20:16:57Z

job_scores rows pointing at a uuid postings no longer has:  0 of 25,199
applications with a posting_id pointing at a missing posting: 0 of 97
```

All three now read `closed=false` under the live title, which is the fold, and
all three kept a score row from three days before it, which is the uuid.

**That witness has since been spent, deliberately, and the note says so rather
than reading as reproducible.** The same fold left `job_scores` describing the
record that had just *lost* the row -- a freshness point each, because the two
were posted on different days -- and parity reported six divergences on two of
the three pairs. `backfill_fold_winners.py` now rewrites the score with the
posting, and `on conflict do update` sets `computed_at = now()`, so the
timestamps above are today's on any re-reading. The uuids are unchanged and
were compared against the values recorded here before the rewrite, which is the
claim; the pre-fold timestamp is gone.

Which is its own small lesson about this kind of evidence: **a witness drawn
from a mutable row is consumed by the next write to it.** Record the value, not
the query, and say when the query stopped answering.

The other witness survives untouched and is the stronger one for §5's purposes:
97 applications carry a `posting_id` and 0 of them point at a missing posting.

**The method generalises past this fix.** When a change rewrites a row, the
thing to look for is a table that references it and was written earlier: the
reference is evidence about identity that neither the row nor a comparison of
two copies of the row can give. Same shape as reading the pool's mtime rather
than the publish time to answer "is the board current", and the opposite of
asking parity, which by construction answers only "did the two copies agree".

### 11d. v2 cannot edit an answer that has been edited before

**`answer_bank` does not carry the entry's version, so v2 has nothing to send,
and `store.py` is right to refuse an edit that guesses.**

Every Memory Bank action carries the version it was made against --
`answer-edit`, `answer-delete`, `answer-dismiss` and `answer-restore` all take
`--version`, and `store.bank_version` compares it against the stored entry.
Matching applies the edit; not matching is either a replay of the same
`editId` or something else moved first, which is refused as `conflict`. That
is the guarantee, and it is the right one.

`class_b.answer_row` carries question, value, state, tags, sensitive, kind and
category. Not version. So v2 sends 0 for every row. Measured 2026-09-06 over
all 195 entries:

```
version 0   156    editable from v2 today
version 1    38    refused: "the entry moved to version 1"
version 3     1    refused: "the entry moved to version 3"
```

**And 156 is a ceiling that falls to zero with use**, because the first
successful edit of any entry takes it to version 1 and v2 still says 0. So
this is not a 20% gap, it is a gap that closes the feature after one use of
each row.

The Python board does not have it: `profile_panel.py` reads `answers.json`
directly and has the version in hand. This is v2-only, introduced by which
fields the mirror carries rather than by anything about the write path.

**The fix is one column and it is a mirror change**, which is why it is written
here rather than made: `version integer` on `answer_bank`, one key on
`answer_row`, and v2 sends what it read. `mirror_fields` discovers the column
from the INSERT and parity compares it without anyone listing it twice, the
same way `edit_outcomes` landed. Nothing else changes: no store contract, no
new queue field, no backfill -- the device's own file already holds every
value, so the first sweep after the migration fills the column from what is
there.

Until then the refusal is at least visible: `edit_outcomes` records it, and the
row draws what was sent, what the store holds, and the store's own words. That
is the honest failure rather than a silent overwrite of a newer answer, but it
is a failure.

### 11e. Parity could not see the employer edge at all, 2026-09-06

**Closed before the resolver, because it is the check the resolver needs.**
`mirror_fields.EXEMPT[("postings","employer_id")]` excused the column as "a
uuid Postgres assigns... the employer is compared through
`employers.canonical_name` instead". The first clause is true and the second
described a comparison that did not exist: `posting_row` returns thirty columns
and none is the employer, and `compared()["employers"]` is that table's own
four. Parity asked "does employer row E have the right name" and never "does
posting P point at the right employer".

Measured by driving `verify_parity.compare`: a posting under the wrong employer
with every compared column identical produced no finding; changing `title`
produced one. So the resolver's correct run and its catastrophic run were the
same green.

Comparing on `canonical_name` is 307 false positives of 23,633, all one
employer under two spellings (249 are Davies against Davies Group). Through
`norm_company` against `norm_name` it is 16, in two named allowances. Landed in
#201, mutation-verified against production by repointing one posting inside a
transaction.

**Every exemption now carries a `GIVES UP:` clause** naming the property it
surrenders rather than only why the column differs. Three exemptions have had
that shape; this is the one that cost.

### 11f. What the multi-agent pass established, and what it did not

A five-lens pass produced 38 claims, each put to three independent refuters.
It is recorded here with its coverage rather than as a set of conclusions,
because 35 of 120 agents died on a session limit including the synthesizer,
and **a claim whose verifiers did not run is not a refuted claim.**

Coverage: the `ordering`, `carry-set` and `winner-rule` lenses completed. Every
one of their 24 claims was refuted, overwhelmingly in the useful shape --
the refuter reproduces the measurement exactly and then shows the derivation
from it is wrong. `observability` and most of `blast-radius`, 14 claims, lost
their verifiers. **Those are unverified. Do not cite them.**

Two survived, and both were re-measured by hand before being written here:

**The rule and the backfill have different lifetimes, so "budget the backfill
with the rule" hides two budgets.** A repointed `employer_id` is never
re-derived, so the backfill is permanent and irreversible. A new posting's
employer is derived fresh on every arrival from one line --
`backfill.py:512`, `norm = norm_company(record.get("company")) or
UNKNOWN_EMPLOYER` -- so the corpus refills with wrong rows whether or not the
backfill ran. Verified: that line is the sole site deciding a posting's
employer; every other mention of `employer_id` in `packages/` and `scripts/` is
a read or the INSERT that carries it. The consequence is that the rule must be
installed at that line and not only inside a module the backfill calls, and the
two halves need different evidence: the backfill is verified once by reading
rows, the arrival path by watching wrongly-attributed arrivals not grow.

**§11c's witness method has no witness here.** Its rule is "when a change
rewrites a row, find a table that references it and was written earlier". That
worked for the fold because `job_scores` and `applications` reference
`postings.id`. Nothing anywhere references the posting-to-employer edge:
verified against the device database, exactly two foreign keys reference
`employers` (`postings_employer_id_fkey` and `employers_merged_into_fkey`),
`job_scores` carries no employer material, and there is no audit table. So the
method §11c calls general fails on its very next case, and §9a's scale is why
nobody noticed -- at six rows the stdout print *is* the before-image. At
thousands it is not.

The consequence is the next section.

### 11g. The largest source drew a job board's mark on every card, 2026-09-06

**`reed.co.uk` was not in `company_logos.INFRA_HOST_PARTS`** while linkedin,
indeed, adzuna, jooble and careerjet all were. So `logo_plan` returned
`PLAN_POSTING_HOST` for every reed posting and the client derived the employer's
mark from the posting's host, which is the job board.

    reed postings in the pool          2,577 of 23,633, the largest single
                                       source that names a host at all
    on PLAN_POSTING_HOST before        2,577
    on PLAN_NAME_CORE after            2,540
    on a monogram after                   37   the name is too short to be a
                                             candidate

**It was invisible because it was consistent.** Every reed card carried the
same wrong mark, so nothing looked out of place. It surfaced only when 47 of
those postings were repointed onto their real employers and *still* showed a
job board's logo, which is a different question than "does this card look
right" and is the question nobody had asked.

That is the general shape and it is worth more than the instance: **a defect
that is uniform across a class does not look like a defect, it looks like a
design.** The board had a rule for exactly this failure -- "never use an ATS or
job aggregator favicon as an employer mark" -- and the rule was right, complete
and enforced. What was missing was one entry in the list it enforces against.

**So the list is now derived from the connectors rather than remembered.**
Every module under `scripts/sources/` declaring `SOURCE_CLASS = "aggregator"`
declares `POSTING_HOSTS`, and `tests/test_aggregator_hosts.py` fails when one
of them is not covered. Adding a connector without adding its host is a red
test rather than a discovery months later.

**Writing that test found two more nobody had noticed**: `usajobs.gov` (343
postings) and `themuse.com` (32). Both are aggregators by their own
declaration and neither host was listed. Three of six aggregators were
mis-drawing their cards, and the count of how long is not knowable.

The hosts are not the API hosts, which is why the declaration is measured
rather than derived from `SEARCH_URL`: careerjet's API is
`public.api.careerjet.net` and its postings live on `jobviewtrack.com`.

**And the rule change needed a backfill, which it shipped without.**
`logo_domain` and `logo_alt` are stored columns, `dual_write.persist` mirrors
`added | changed`, and 2,530 records were not in any delta. Parity reported
5,060 `field_mismatch` findings within ten minutes of the deploy. That is §11b
holding on the change made while writing §11b's own section, and
`scripts/backfill_logo_plans.py` is the pass that closes it.

### 11h. A v2 control that wrote a state and ordered no work, 2026-09-07

**Three controls posted `status: "inflight"` and none of them reached the
device.** `queue.STATUS_QUEUE` carried `dropped` alone, so `order` was never
called; the route wrote `applications.status = 'inflight'` regardless, and the
board reported that work had started on a real application while no machine had
heard of it.

Found by the employer-edge parity run on one row: Scale Media / Data Analyst,
session file `watch`, Postgres `inflight`, apply queue empty, POST recorded at
2026-09-07T00:18:36Z with a 200. The Vercel log was gone by the time anybody
looked, which is why the durable evidence mattered: `idempotency_keys` stores
the response, and `{"kind": "application.changed"}` is returned only by the
status path, never by `decide`. That is what identified the route without a log.

**Its own docstring is what caused it.** The map read: "`review`, `active` and
`inflight` are stages the daemon itself moves an application through; a board
does not order them, it observes them." True of the daemon's transitions, false
of a control labelled Apply, and stated confidently enough that nobody checked
which controls actually send it.

`inflight` maps to `apply`, which is what the Python board does -- `/api/retry`
is deliberately the apply queue -- and the guard is
`tests/status_writes_reach_the_daemon.test.mjs`: every status a control sends
must be carried by the queue or declared observational with a reason. **It
found a second one immediately.** Undrop posts `status: "watch"` and was
stranded the same way; the Python board has always sent it to `restore`. One
bug reported, two found, and the second had nobody looking for it.

## 12. Four filter controls were removed, and here is what brings them back

Re-measured 2026-09-05 over all **25,204** pool records, which is up from the
22,762 of section "The three attributes nothing extracts":

```
extracted.degree            0    0.0%
extracted.employmentType    0    0.0%
extracted.roleCategory      0    0.0%
extracted.yearsMin       9,826   39.0%
```

And in Postgres it is stronger than a low percentage: `postings` has **no
`extracted` column at all**, so the three are not sparse, they are absent.
"Job type" -- permanent, temporary, volunteer -- has neither a column nor an
extracted key anywhere.

So v2's Degree, Role, Employment and Job type selects are removed rather than
left. They could only ever match nothing, and **a control that cannot match is
worse than a note saying the data is missing**: it looks like a filter and it
reads as broken to the first person who tries it. That is the
433-disabled-controls finding in a better costume, and the reason it took a
user report to surface is that a filter returning nothing is indistinguishable
from a filter working on an empty result.

The old board is honest about this in `found_lane.py` -- "degree, role and job
type are not extracted yet" -- and shows the emptiness behind that note.

**This is an ingest item and always was.** `extract.extract()` returns
`{yearsMin, yearsMax, yearsText}` and attempts nothing else. Bringing the
controls back means an extractor that reads a description for a degree
requirement, an employment type and a role category, and it belongs with the
connector work rather than with the board. The controls go back the day the
column is populated, not before.

What survives and is worth wiring: **Max experience** (`years_min`/`years_max`,
39.0%), **Locations** (`location_raw` 99.7%, plus `city`/`region`/`country`),
**workplace** (`workplace` 30.2%, `remote` 100%), and the two free-text
filters, hide-mentioning and company include/exclude, which read columns that
are fully populated.

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

## 13. The container's first sweeps, and three wrong diagnoses, 2026-09-08

### 13a. A warm canary answers 304, and the count could not say which board

The first warm cloud sweep reported `canary_silent` for spacex and snowflake --
greenhouse's and ashby's canary boards, the two sources held to the strict
standard. The wording is "was fetched and returned no postings", which is what a
moved tenant or a broken parse looks like. Both had in fact answered 304, which
is the healthiest thing a board can do.

`canary_problem`'s first guard is exactly for this: a board named in
`unchangedSlugs` is excused. It could never fire, because the list never
crossed the seam. `sweep_runs` carries `unchanged` as an int, and 0020's own
comment says why: "the device's coverage ratchet needs to tell those apart",
where a count is the whole fact. The canary asks a different question of the
same event -- was *this* board among them -- and `cloud_sweep.staged` rebuilt
the envelope from the count alone, so `base.ok` defaulted the list to empty.

**Empty is indistinguishable from "nothing was unchanged".** That is the
synthetic-record shape already in CLAUDE.md, one table over: a stand-in built
to satisfy one predicate answers every other predicate with a default. Here the
default was `()` and the real value was "these 96 boards".

The cost is not one wrong line. A warm sweep is the normal case, so the alarm
fired on its two most important sources **every time it ran**, which is how an
alarm stops being read. Migration 0022 carries the slugs; `base.board_slugs` is
the one normaliser all three sites use.

**The corpus said this thirteen days before it shipped.**
`docs/handoffs/claude-discovery-2026-08-23.md` records "Ashby and Lever
contacted 135 and 70 boards respectively but returned no changed payloads on
that conditional run", and the next paragraph is a warning in general form: "Do
not interpret `kept: 0` as connector failure... A healthy unchanged board can
produce zero new records." A check that reads zero postings as a broken board
is that mistake with a badge on. Nobody connected the two, and nothing would
have made them: the handoff is about reading a report and the canary is about
writing one.

### 13b. Three wrong diagnoses of a slow container, and what the log said

The first cold sweep took ashby alone about twenty-four minutes. Three
explanations were advanced and each was refuted by measurement rather than
argument: parse-bound, shared-vCPU, datacentre throttling. What actually
settled it was `free -m` over `flyctl ssh console` and a missing validator
cache -- the container had no `source-cache.json`, so every board was a cold
200 where the laptop's every board was a 304.

**A cold sweep and a warm sweep are different workloads, and comparing one
machine's cold run against another machine's warm run measures the cache.**
Warm, the container runs at 1.0s a board, which is `MIN_INTERVAL_PER_HOST`
exactly: the sweep is sleeping, not working.

`load_validators` now seeds the container from `http_validators` before the
Fetcher is built, and `seed_validators.py` put the laptop's 814 entries there
so the first cloud run was warm.

### 13c. Greenhouse's per-board cost is not its payload

17.5 seconds a board for greenhouse against 3.7 for ashby reads like payload,
and greenhouse's own connector docstring invites that reading: `content=true`
is "fourteen times the traffic". Measured against the live APIs on 2026-09-08,
five boards each:

    greenhouse   mean 3.9 MB/board, network 1.27s, json.loads 0.014s
    ashby        mean 4.5 MB/board, network 0.63s, json.loads 0.013s

Ashby's payloads are **larger** per job (8,711 mean body chars on its big board
against greenhouse's 8,033) and its per-job fingerprint cost is the same, 3.5ms
against 3.6ms. Payload is refuted, and so is parse: the whole 870-job
databricks board fingerprints in 3.1 seconds on the laptop.

What the container log shows instead is that **every 200 is followed by a stall,
and the stalls grow**: 40s, 52s, then 151s, between requests that are otherwise
1.0s apart. The same work costs 3.1s here and about 151s there, on a
shared-1x-vCPU machine with 2048 MB and no swap. The interrupt that ended that
run landed inside `identity.simhash`'s bit loop, which is where a memory-starved
CPython would be.

So the honest statement is: not payload, not the algorithm, and not the source
-- the container's own environment on a full-body 200. It matters little in
steady state, because a warm sweep has almost no 200s, and it is the thing to
measure if a cold sweep is ever needed again. **Do not size the machine on it
without measuring first**; three diagnoses have already been wrong here.

### 13d. The sweep is single-threaded, so vCPU count is irrelevant

`sweep_worker.sweep` runs channels in a loop and each connector walks its boards
in a loop. There is no thread pool, no `asyncio`, no `multiprocessing`. Adding
vCPUs cannot move a wall clock that one Python thread owns, and the earlier
instinct to "size it from 15.9 postings/sec on the Mac" was sizing the wrong
axis.

The axis that is real is memory, per 13c. The axis that dominates a warm sweep
is neither: 795 boards at a 1.0s per-host floor is 13 minutes of deliberate
sleeping, and the only way to move that is more hosts in parallel, which is a
design change and a courtesy question, not a machine size.


### 13e. The measured warm sweep, 2026-09-08

The first device-triggered warm sweep to run end to end, and the numbers it
settles.

    fetch          06:04:09 -> 06:33:37   29.5 minutes, in the container
    27 channels    24 complete, 2 failed, 1 skipped
    staged         12,632 postings
    POOL LOCK      30.8 seconds
    merge          found 12,632, kept 357, closed 1

**30.8 seconds against a local sweep's median 26.8 minutes.** Measured twice
by instruments that share no code: `cloud_sweep` times the span inside
`runlock.held` and reports `lockSeconds`, and a separate process polled
`~/.job-application/locks/scan.lock` at 10 Hz and recorded a `cloud-merge`
span from 02:36:59 to 02:37:30. Both say 30.8.

**The cloud fetch is slower than the laptop, and that was never the argument.**
29.5 minutes against 26.8. It could not have been faster: 795 boards at a 1.0s
per-host floor is 13 minutes of deliberate sleeping before any work happens, and
the container is a shared 1x vCPU. `WAIT_SECONDS` moved from 12 minutes to 45
on this measurement, and its docstring now says it bounds a *hung* container
rather than a slow one.

**The device's own connection died the same way the container's had.**
`cloud_sweep.main` opened one Postgres connection, called `start_machine`,
waited 29.5 minutes on it, and then used it: Neon had closed it, and the run
reported `consuming input failed: SSL connection has been closed unexpectedly`
**after** the container had finished writing all 27 channels. A completed fetch
reported as a failed run.

This is `sweep_worker.stage`'s fault, in the file next door, three days later.
CLAUDE.md already carries the general form from `read_pool` and `load_jobs`: a
rule stated in one function's docstring is not a rule the codebase holds, and
the closer the neighbour the less likely anybody checks. Both readers of the
same seam had the same connection lifetime available to them, and only one had
been fixed. `wait_for_sweep` takes a `connect` callable now and opens one per
poll, which is exactly what `stage` was changed to do.

**The problems line, which is the point of the health work.** Three entries,
each naming what it is and what it would take:

    jooble      failed: jooble any '': HTTP 400
    oracle_orc  failed: every board failed (2)
    lever       returned 8% of its usual 32 for 2 sweeps running

No canary entries, which is the 13a fix landing. Nothing that a reader cannot
act on.


### 13f. A completed sweep thrown away twice, and the timeout that did it

**The trigger worked. The deadline threw the result away.** `config.env` was
armed at 06:50 while the running checkout still carried `WAIT_SECONDS = 12 * 60`.
Two scheduled sweeps then fired unattended:

    07:58:44  started the machine, gave up at 08:10, machine finished 09:05
    11:55:10  started the machine, gave up at 12:07, machine finished 13:11

Both fell back with the right words -- "the machine was still running after
720s; `flyctl logs ...` says why" -- and both were complete: 27 of 27 channels,
21,414 and 23,207 postings, 44,621 rows sitting in `sweep_postings` that nothing
ever read. Eight hours of a stale board while the work was done and staged.

**The two directions of this error are not symmetric and the constant has to be
chosen on that rather than on a percentile.** Too long means a hung container
goes unnoticed and one cycle is missed. Too short means a *finished* sweep is
discarded, every time, forever, because the container will always be slower than
the deadline that is too short for it. `WAIT_SECONDS` is two hours, 1.6x the
worst of three real sweeps and well inside the four-hour tier-1 interval.

**And the first measurement was the smallest, which made it the wrong one to
generalise from.** 29.5 minutes at 12,632 postings looked like the warm number.
It was the warm number *for that sweep*: 1.8x the postings costs 2.6x the time.
A single measurement of a non-linear thing is a measurement of one point, and
reporting it as "the warm number" -- which is what happened -- is the summary
statistic problem this file already records about a p95 over a non-stationary
series, in a new place.

### 13g. Greenhouse costs 45 s a board in the container, and it is not payload

Section 13c refuted payload and CPU and reached for memory on the strength of a
cold run's `free -m`. The per-channel numbers refute the general form of that
too. From the 76.3-minute sweep, all on the same machine, in one process:

    channel          elapsed   postings  boards  304s   per posting
    greenhouse        3399 s     13149     309    234      0.26 s
    ashby              748 s      3172     193    171      0.24 s
    smartrecruiters      77 s      3324      49      0      0.023 s
    lever              120 s       889      91      0      0.13 s
    rippling            15 s       818      16      0      0.018 s

**smartrecruiters parses 3,324 postings from 49 all-200 boards in 77 seconds,
after ashby had already loaded 3,172 postings into the same process.** If this
were memory pressure or GC growth, smartrecruiters could not be 11x cheaper per
posting than greenhouse in the same run, later in the run. On the laptop the two
sources cost the *same* per job (3.6 ms against 3.5 ms, fact 13c).

So what is left is something greenhouse does that the cheap channels do not, and
"memory on a 2 GB box" is at best half of it. This is under investigation and is
deliberately not being guessed at again: three diagnoses have already been wrong
here, and the fourth would be the one that got written down as settled.

**What is safe to say today**, and the only thing that should be relied on:
greenhouse is 56.6 of the sweep's 76 minutes and dominates the wall clock, the
wall clock does not block the device (the pool lock is 30.8 s), and
`WAIT_SECONDS` is sized to survive it rather than to be right about it.

