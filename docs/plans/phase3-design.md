# Phase 3 design notes, written in the parity window

Design only. Nothing here is built, and nothing in this document touches a
parity gate path: the clock started 2026-09-02T22:58:53Z and opens
2026-09-05T22:58Z.

Three things: what has gone stale in `phase3-inputs.md`, the employer resolver
sketched against the real cases rather than a hypothetical, and what the
`sweep_runs` gate actually needs.

Every number here was read off the live database or the sweep log on
2026-09-03. Two of the previous draft's own rules were refuted by that reading
and are marked where they failed rather than quietly replaced.

## 1. What is stale in `phase3-inputs.md`

Measured against the live database on 2026-09-03. The document is two weeks old
and some of it was written before things it assumes changed.

| claim | as written | measured now | verdict |
|---|---|---|---|
| §3 employers | 1,934, one domain collision | **2,855**, 2,504 with no domain; 1 `merged_into` set and never applied | numbers stale, conclusion understated |
| §5 `posting_id` | NULL for all 224 rows | 205 applications, **103 linked** | superseded, and see below |
| §5b backfill | 121 of 204 linked | 103 of 205 | **the link decays** |
| §6 fixtures | 27 in production | **none** | done |
| §7 `sessions` table | 88 rows against 89 files | 88 against **97** | drift grew from 1 to 9 |
| §8 the leak | one `active` orphan, shows in Needs you | orphan still there, **does not show** | consequence stale |
| §10 / §5 numbering | two sections numbered 5 | -- | fix while editing |
| §11 | did not exist | added 2026-09-03, the careers-host ingest defect | new, written out of this sketch |
| extracted attrs | 0 of 22,762; years 9,483 (41.7%) | 23,141; years 9,596 (41.5%) | holds |

Four of those are worth more than a row.

### §5 is not stale, it describes a decay -- and the decay is pointing at the wrong field

The doc treats "a pruned posting cannot be resolved to" as a **ceiling on the
backfill**. It is worse: `applications.posting_id` carries `ON DELETE SET NULL`
(`confdeltype = 'n'`), so pruning a posting **destroys a link that was already
resolved**. The backfill wrote 121 on 2026-09-01; **103 remain**, 18 lost in two
days with nothing recording they existed. `applications.posting_id` has gone
from 59% to 50%, and it is the first thing the population ratchet would have
caught had it existed a day earlier.

**But an application does not need `posting_id` to name itself, and measuring
that is what changes the design.** Over all 205 applications:

```
company      205   100%      role        205   100%
apply_url    205   100%      legacy_id   205   100%
ats          155    76%      source      181    88%
posted_at     80    39%      posting_id  103    50%
```

102 applications have no `posting_id` and a complete company, role and URL.
Zero `posting_id` values point at a row that is missing -- the cascade is doing
its job, not leaving dangling pointers. **So the name is already self-sufficient
and no field needs adding for it.**

What the cascade actually destroys is the **join to what the posting knew**, and
that splits into four things with four different right answers:

```
the employer identity   -> the real gap. Add `applications.employer_id`.
the posting body        -> already safe. Class 1 artifact in R2, survives.
posted_at               -> freeze it at link time, do not re-derive it.
"is this still listed"  -> genuinely expires. Let it.
```

**`posting_id` was carrying two meanings and only one of them should decay.**
"Which posting this was" and "which employer this is" are different facts with
different lifetimes, and the employer outlives the listing. `employers` is not
pruned, so an `employer_id` on the application survives everything that removes
a posting, and it follows a merge through `merged_into` -- which is exactly what
the board needs for a logo and a company view, and what it silently loses today
on half the applications.

That also reframes the cascade itself: **`ON DELETE SET NULL` is correct.** The
posting is gone and nulling the pointer is honest.

**This replaces the exempt-linked-postings-from-pruning draft, and the reason
is worth keeping rather than just the conclusion.** That draft was reached by
treating the decay as the defect and reaching for the cheapest thing that stops
it: 103 rows out of 23,141 postings, a small carve-out. Three things are wrong
with it. It buys a correct answer by fighting the retention policy, and a
carve-out against retention only ever grows, because every future application
adds a row that may never be pruned. It preserves the whole posting to keep one
field, when the field that matters is the employer and the body is already a
Class 1 artifact in R2. And it leaves `posting_id` carrying two facts with
different lifetimes, so the next person to read it still cannot tell whether a
NULL means "we never linked this" or "the listing closed".

Accepted instead, and the shape is the one `answer_record` already uses -- two
claims, two fields, never blended:

```
applications.employer_id   new column. Survives pruning, follows merged_into,
                           is what the board needs for a logo and a company view.
applications.posted_at     copied down at link time so it freezes. 39% today
                           against 100% for the identity fields.
applications.posting_id    allowed to decay honestly. It means "the posting is
                           still in the pool" and that genuinely expires.
applications.company       stays frozen. It is what the employer's form saw.
```

The 5 links whose `company` disagrees with their resolved employer go to a
review queue rather than an overwrite, for the same reason a salvaged answer
record is never joined against `answers.json`: resolving a frozen claim against
today's store produces what the store would say now, presented as what was true
then.

Two measurements make the shape concrete. `posted_at` is on 39% of applications
against 100% of the identity fields, which is the one field the link was
genuinely supplying and the one to copy down at link time so it freezes rather
than evaporates. And **5 of the 103 linked applications hold a `company` string
that disagrees with their resolved employer's canonical name** -- the frozen
string is what the employer's form actually saw, so those are a review queue and
not an overwrite. Same rule as a salvaged answer record: two claims, two fields,
never blended.

### §8's leak is still there, but its stated consequence is not

`actively-ai-agent-product-manager` is still the one application with no session
file and status `active`. The doc says a lane reading `applications.status` puts
it in Needs you. It no longer does: `v2_lanes.LANES["act"]` excludes anything
with a completed event and no later abandonment, and this row has one from
2026-08-19. The leak is real and the symptom it was described by has been closed
from the other end. **Do not delete the section; correct it.** The row is still
wrong and the next deletion still adds another.

### §6 is done and the reason matters

The 27 fixtures are gone. The exemption that hid them is unchanged, so the
finding stands and only the instance is closed. Worth keeping as written.

### §7's drift is growing

88 table rows against 97 files, where the doc recorded 88 against 89. The table
is a deletion candidate and eight more rows of evidence have arrived for that.

## 2. The employer resolver, against the three cases that exist

`docs/employer-identity.md` has the policy. This is the resolver sketched
against real rows, because the cases that exist need different mechanisms and a
design written against one of them fails the others. The brief named two. There
are three, and the third one's fix is not in the resolver at all.

### Case A: ShareGate. A domain collision, already detected, never applied

```
ShareGate                       norm_name "sharegate"                       domain sharegate.com   1 posting
Sharegate Migrate Chezworkleap  norm_name "sharegate migrate chezworkleap"  domain (none)          1 posting   merged_into set
```

The backfill **already found this and recorded it**: `merged_into` is set on the
second row. Nothing reads it, so both rows still exist and the postings are
still split one and one.

So case A needs no new detection. It needs the *application* of a decision
already taken:

    resolve(name, domain):
        row = employer by norm_name
        while row.merged_into is not null:      # follow, with a cycle guard
            row = employer by id = row.merged_into
        return row

That is the whole of it for this case, and it is a read the upsert does before
it writes. `merged_into` must never be followed more than a bounded number of
hops, and a cycle is a data fault worth failing loudly on rather than looping.

### Case B: Appcast. Two employers, no domain on either, 140 postings split

```
Appcast Enterprise    norm_name "appcast enterprise"   domain (none)   80 postings
Appcastenterprise     norm_name "appcastenterprise"    domain (none)   60 postings
```

Domain equality cannot merge these, because neither has one. The names differ
by a single space. It is a **normalisation** failure rather than a collision the
current detector can see, and that is why case A's mechanism is not sufficient.

Measured 2026-09-03 over all **2,855** employers, of which **2,504 (87.7%) have
no domain at all** -- that is the population case B operates on. Collapsing
spaces and punctuation on the canonical name finds **13 groups**:

```
                          promote?  members (postings)
advancedrecoverysystems   yes       Advancedrecoverysystems(3) | Advanced Recovery Systems(2)
appcastenterprise         yes       Appcastenterprise(60) | Appcast Enterprise(80)
colibrigroup              yes       ColibriGroup(11) | Colibri Group(1)
jpmorganchase             yes       JPMorganChase(3393) | JPMorgan Chase(5)
nomadfoods                yes       Nomadfoods(3) | Nomad Foods(3)
planettechnologies        yes       Planettechnologies(6) | Planet Technologies(1)
publicisgroupe            yes       Publicisgroupe(3) | Publicis Groupe(1)
rcmhealthcareservices     yes       RCM Healthcare Services(1) | RCM Health Care Services(1)
soprasteria               yes       Soprasteria(3) | Sopra Steria(0)
telusdigital              NO        TelusDigital(55) | TELUS Digital(12)
tigeranalytics            yes       TigerAnalytics(44) | Tiger Analytics(2)
vastspace                 yes       Vastspace(3) | Vast Space(1)
vsgbusinesssolutions      yes       VSG Business Solutions(4) | Vsgbusinesssolutions(2)
```

**Scale first, because it is smaller than it looks.** Those groups touch 3,696
postings, but a merge only moves the smaller side of each pair: **79 postings
change employer**, and 60 of the 79 are Appcast. JPMorgan Chase is the biggest
group at 3,398 postings and moves 5 of them. This is a correctness fix, not a
volume one, and it should be argued as such.

### `michaelpage` is the reason for the whole design, and my first rule failed on it

**The refusal I wrote into the previous draft did not fire against the real
corpus. It passed Michael Page -- the single case it was written to catch.**

The rule was: refuse when one squashed name is a proper prefix of the other and
the remainder is a word. Against the corpus it returns PROMOTE for
`michaelpage`, and the reason is a use-versus-mention slip one layer down.

```
Michael Page Technology   canonical "Michael Page Technology"   norm_name "michael page"
Michaelpage               canonical "Michaelpage"               norm_name "michaelpage"
```

`Michael Page Technology`'s **`norm_name` has already had "Technology" eaten**
by the existing normaliser -- alone among eleven Michael Page rows, which keep
Finance, Legal, Digital, Sales, Marketing, Australia, Business Support,
Engineering & Manufacturing and Procurement & Supply Chain intact. So grouping
on `norm_name` puts the two in a group whose members squash *equal*, and a
prefix test on equal strings has nothing to refuse. Written against
`canonical_name` in my head, applied to `norm_name` in the query. Twelfth
instance of that shape in this repository, first one in a design doc.

Eleven Michael Page rows, 41 postings. That is the scale of the wrong
direction, and it is the reason the resolver is a candidate generator rather
than a decision.

**The fix is the grouping key, and it makes the refusal unnecessary.** Group on
the squashed `canonical_name` and a prefix pair can never group at all, because
`michaelpagetechnology` and `michaelpage` are not equal. The guard becomes dead
code by construction rather than by being correct. Measured, the two designs
give 13 groups each and *not the same 13*:

```
group on norm_name       finds michaelpage and tenthrevolution (both refused),
                         misses colibrigroup and planettechnologies
group on canonical_name  never proposes michaelpage or tenthrevolution,
                         finds colibrigroup and planettechnologies
```

Canonical-name grouping promotes a superset of what norm-name grouping
promotes, and needs no prefix clause. Take it. It carries one dependency worth
writing down: it only works while `canonical_name` holds the employer's raw
string. If a connector ever normalises before storing it, this design silently
degrades to the other one, and Michael Page comes back as a candidate.

`tigeranalytics` is the corpus's second warning and it argues the same way.
`Tiger Analytics` and `TigerAnalytics` are one company, and `Tiger Recruitment`
and `Tigera` sit right beside them and are not. Any rule looser than exact
equality of the squashed canonical name reaches them.

### Case C: TELUS. Both rows have a domain, and they disagree

```
TelusDigital    telusdigital.com        55 postings
TELUS Digital   jobs.telusdigital.com   12 postings
```

Case A cannot see it: the domains differ, so domain equality finds nothing.
Case B excludes it: its precondition is that neither row has a domain. It is a
third mechanism, and the only one of the 13 groups the rule refuses.

**It is not a resolver problem and it is filed as an ingest defect**, in
`phase3-inputs.md` section 11, with the reproduction and the two causes. The
short version: `company_logos.website_from_description` takes the first link in
the posting body whose host contains a company token, an apply link precedes the
corporate one on essentially every posting, and the filter that rejects
`boards.greenhouse.io` does not reject an employer's own `jobs.` host. Measured,
**24 of the 351 employers holding a domain have one whose first label is not the
company**, including Airbnb, Robinhood, Roblox and Snowflake, plus five blogs.

The resolver's refusal is containing this defect rather than fixing it, and the
two must not be confused. Fix it in ingest, backfill the 24, and this group
stops existing. A registrable-domain comparison in the resolver would also merge
TELUS, and would be the right repair in the wrong place.

### The promotion rule, corrected against the corpus

The previous draft's domain test was binary and the corpus refuted it.
`ColibriGroup` has `colibrigroup.com` and `Colibri Group` has nothing; that is
not a conflict, it is a merge where one side knows the answer. Three tiers:

```
neither has a domain     promote; merged row has no domain
exactly one has one      promote; merged row keeps the known domain
                         (the coalesce rule the mirror already applies, so
                          parity does not start reporting the merge)
both have one, different REFUSE -- and look upstream, because it is TELUS
```

Against the corpus: **12 promote, 1 refuse, 79 postings move.** Every promotion
is exact equality of the squashed canonical name. Everything else is written to
a queue and merged by nobody, because a decision whose wrong direction is
unrecoverable belongs to a person.

The false negatives are accepted and named rather than argued away.
`Tenthrevolution` / `Tenth Revolution Group` are one company and this rule will
never merge them. `Vast`(61) sits next to `Vast Space`(1) and `Vastspace`(3),
and the rule merges the small pair and leaves the 61 alone with no opinion.
`phase3-inputs.md` §3 already prefers a missed merge to a wrong one; these are
what that preference costs.

### Where it runs, and the ordering that matters

Before the upsert, never after. §5's connector takes the employer name from the
board's API, which is a fresh string every sweep; resolving after the insert
means the row already exists and the postings have already scattered.

And **the resolver must be the only place this decision lives**, for the reason
`posting_link.py` is the only place its decision lives: a backfill over history
and a connector writing a new posting cannot be allowed to disagree about
whether two names are one employer.

### What to measure before building it

Three of the previous draft's four are now measured and above. What is left:

- what the resolver does to `norm_name`, since the merge target needs one and
  the existing normaliser has demonstrably eaten a business unit from at least
  one row,
- whether the 128 employers holding zero postings are merge residue, feed
  artefacts, or something else, before a merge adds more of them,
- `merged_into` carries exactly **1** row today (ShareGate, case A, still
  unapplied), so there is no chain longer than one hop and the follow step can
  be written without a cycle guard -- but it needs one anyway before the
  resolver starts writing them.

## 3. The closure sweep. Read the warning before the table

**The empty-response case the plan is written against is already safe, and it
is safe by structure rather than by a check. A Phase 3 closure step that
reconstructs "what was seen" from the `postings` table would lose that
protection while looking equivalent.**

That is the headline because it is the way this port goes wrong. The key in
`seen` is created *by a posting arriving*:

```python
seen_by_board.setdefault((source, company), set()).add(match_id)
```

A connector that returns `ok` with zero postings therefore never creates its
key, brake 1 below never sees it, and nothing closes. There is no check
performing that. It falls out of the data structure. Ask the `postings` table
"what does this board hold" instead of asking the sweep "did a posting arrive
under this key" and the two agree on every healthy sweep and disagree on
exactly the one that matters.

This is not hypothetical. On **2026-08-30T01:02Z** the `smartrecruiters`
channel returned **0 postings with status `ok`**, and closed nothing, because
of that line.

### The four brakes, and what each one catches

`jobs_store.close_missing` has four. Its own docstring says "Three brakes" and
then lists four, so a porter reading the prose gets three; fix that wording
while porting.

```
1  a board key absent from `seen` is untouched
   catches  a board that 304'd, errored, timed out, or was not scheduled this
            run -- silence must never read as closure. Structurally it also
            catches the empty-response case above.

2  only `new` and `watched` records are eligible
   catches  a user's decision outliving the employer's listing. `applied` and
            `passed` are terminal, so Submitted, In flight and Needs you
            cannot lose a row because a board stopped advertising it.
            Measured over 75 sweeps: 4,424 `new` closed, 23 `watched`, 0
            `applied` or `passed`.

3  only jobs whose best sighting is an employer board (`ats`, `careers`)
   catches  an aggregator result page read as a catalogue. A search result is
            paginated and ranked; absence from it is absence from one page.

4  MAX_CLOSED_SHARE = 0.7, per board, once a board holds
   MIN_BOARD_FOR_SHARE_CHECK = 5
   catches  a truncated fetch that returned success. The only brake that has
            ever actually fired here.
```

### `closureRefused`, read for the first time

75 sweeps, 2026-08-20 to 2026-09-03. **It fired 22 times, all in one
contiguous window, all for one board.**

```
2026-08-25T23:31Z .. 2026-08-29T16:59Z   22 consecutive sweeps
rippling / astra    held 33, wouldClose 32, every single time
```

Four things follow, and only the first is reassuring.

**It was right.** All 34 Astra records are `new` today with `lastSeenAt`
2026-09-03T04:11Z. The board returned 1 of 33 for very nearly four days and
then came back whole. Without brake 4, 32 live postings would have been closed
on the first of those sweeps and stayed closed.

**It has no memory, so it cannot tell a bad fetch from a board that emptied.**
The refusal reason says "treating this as a bad fetch rather than a mass
closure", which is a defensible reading of one sweep and an increasingly poor
one by the twenty-second. Had Astra genuinely closed 32 of 33 roles, the brake
would have refused identically and forever, and the records would have left the
pool by ageing out of `prune` rather than being marked expired -- disappearing
rather than showing as closed, which is the worse of the two for a reader.

**It is silent.** `closureRefused` is written into the report, the report goes
to `lastIngest` and `ingest-runs.log`, and `grep` finds no other reader in the
repository. Twenty-two identical refusals over four days is precisely the
signal that says "this has stopped being a bad fetch, come and look", and this
is the first time anyone has looked.

**It is per-board, so a pool-wide event trips nothing.** On 2026-08-30T01:02Z
the sweep closed **1,860 records with zero refusals**, no config change (version
23 either side). That is not a footnote about this guard, it is a Phase 3
requirement, and it has its own section below.

### `sweep_runs` is not guarding a hypothesis. It is guarding 2026-08-30

**The half-complete sweep already happened, closed 435 Greenhouse postings, and
nobody noticed for four days.** That is a stronger argument than the design had,
and it should be the first thing anyone reads about this table.

```
2026-08-30T01:02Z     found before   found after   status
greenhouse                   5,909         3,209   ok
smartrecruiters                996             0   ok
oracle_orc                   9,357         9,356   ok
```

Greenhouse returned **54% of its previous yield and reported success**. Every
Greenhouse board that came back short had its missing postings evaluated for
closure, and only boards past the 70% per-board ceiling were spared. 435 closed.
SmartRecruiters returned zero, reported success, and closed nothing, because of
the structural property at the top of this section.

Nothing in the report can say whether those 435 were real closures or a
truncated fetch, and nothing could, because the sweep never recorded what a
complete fetch of that board looks like. That is the entire gap, stated as a
measured event rather than a risk.

### So what `sweep_runs` is actually for

Not the empty response. The half-complete one. It replaces an inference with a
declaration.

```
sweep_runs
  id              uuid
  account_id      uuid
  source          text        -- greenhouse, lever, ...
  board_slug      text
  started_at      timestamptz
  finished_at     timestamptz
  status          text        -- running | complete | failed | abandoned
  job_count       int         -- what this run actually wrote
  page_count      int         -- pages fetched, so a truncation is visible
  reason          text        -- why it failed, kept rather than dropped
```

Four requirements, each with the failure it prevents:

**`complete` is written after the postings are committed, never before.** A
status written first is a lie the moment the write fails, and closure would then
read a partial sweep as whole. Same ordering as `writes.ts`: commit, then
publish.

**A run that never finishes must not stay `running` forever.** A crashed worker
leaves `running`; if closure ignores `running` rows, one crash disables closure
for that board permanently. That is the safe direction and a silent one, which
this repository has been bitten by four times. A lease with an expiry, swept to
`abandoned`, keeps it visible. `runlock`'s docstring already argues why a pid
check is wrong for work spanning processes.

**`job_count` is the evidence and it wants a ratchet.** A `complete` run
returning 3,209 where that board's last complete run returned 5,909 is a
truncation wearing a success, and that is 2026-08-30 exactly. Held against the
board's own previous complete run with a tolerance; a fall refuses closure
rather than performing it. This is `population.py` pointed at a third thing, and
by then the shape has earned being factored out.

**Closure reads only `complete` runs from this sweep window, and the window is
explicit.** "The latest complete run per board" is not "a complete run in this
sweep", and a board last swept successfully a week ago must not have its
absences read as closures today.

### The fifth brake: a pool-level ceiling. This is a requirement, not an observation

**The largest closure on record closed 1,860 records, 11.5% of the pool, and
tripped the per-board brake zero times.** `MAX_CLOSED_SHARE` is per board, so a
failure correlated across boards is invisible to it by construction: 1,358
`oracle_orc` and 435 `greenhouse` closures, spread thin enough that no single
board crossed 70%. Phase 3 needs a pool-level ceiling alongside the per-board
one, built on the same idea and the same refusal behaviour.

**What it is measured against, and the denominator is the part to get right.**
Not the whole pool. The per-board brake compares against what is held *for that
board*, and the pool analogue is what is held *and could close*: state `new` or
`watched`, on an `ats` or `careers` board. Measured 2026-09-03 that is **14,692
of 23,202 records, 63.3%**. The rest is 3,959 expired tombstones plus 111
`applied` and `passed` rows that brake 2 can never touch and aggregator records
brake 3 can never touch, and letting them into the denominator means the
threshold drifts with tombstone retention rather than with closure.

It also has to be taken **before the sweep's own prune**, because prune shrinks
the denominator in the same pass. The same 1,860 reads three different ways:

```
9.25%   of the pool before that sweep      (20,103)
11.54%  of the pool after it               (16,123)
14.61%  of the closeable population        (at today's 63.3% ratio)
```

A 1.6x spread from the denominator alone, which is why it is pinned here rather
than left to whoever writes it.

**The corpus sets the value and it is not a delicate calibration.** Over the 45
sweeps that carry a closure count, closed as a share of the pool:

```
median   0.297%      p90   0.672%
p75      0.539%      p95   0.746%
next     1.08%   (2026-08-25)
max      11.54%  (2026-08-30)
```

There is a canyon between p95 and the one real event. Any threshold from 2% to
10% refuses exactly that sweep and nothing else, so the value is robust to the
sample being small even though 45 sweeps and one outlier is a small sample.
**5% of the closeable population** is the recommendation: about 4x p95 converted
to that denominator, about 3x below the event, and re-derived when the pool's
composition moves rather than left as a constant nobody revisits.

**What it does when it fires is refuse the whole sweep's closures and say so**,
the way the per-board brake refuses and records. A pool-level anomaly is
evidence about the sweep rather than about one board, and closing selectively
under it means choosing which boards to trust on no information. The cost of
refusing is stale cards for four hours. The cost of not refusing was 2026-08-30.

It does not replace `sweep_runs` and is not replaced by it. `sweep_runs` catches
a channel that under-delivered and reported success; the pool ceiling catches a
correlated failure across channels that each look individually fine. The
2026-08-30 sweep would have been caught by either, which is a reason to build
both rather than a reason to pick one.


### What must not change in the port

The four existing brakes are not superseded by `sweep_runs` and all four
survive it. The fifth is new and does not replace any of them. Brake 4 in
particular is the last line when every other signal says the sweep was fine, and
it is the only one with a firing record. Brake 1's structural half is the one to
write a test for, because it is the one that disappears without anybody deleting
anything.

And `closureRefused` needs a reader. It is already computed, already carries
the board and the counts, and has been sitting unread through the one window it
had something to say. A repeat count -- the same board refused N sweeps running
-- is the number worth alarming on, because one refusal is a bad fetch and
twenty-two is a question.

### What to measure before building it

- the distribution of postings per board, so `MAX_CLOSED_SHARE` and any
  `job_count` tolerance come from the corpus rather than a guess. The pool-level
  half of this is measured above; the per-board half is not,
- how many boards are reached through more than one channel, since `ingest.py`
  already warns that a board reached twice must not look half-empty in either,
- what the 1,358 `oracle_orc` closures of 2026-08-30 actually were, since that
  is the largest closure event on record and the only reason it looks benign is
  that the channel's total yield held steady.
