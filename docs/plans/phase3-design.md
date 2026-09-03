# Phase 3 design notes, written in the parity window

Design only. Nothing here is built, and nothing in this document touches a
parity gate path: the clock started 2026-09-02T22:58:53Z and opens
2026-09-05T22:58Z.

Three things: what has gone stale in `phase3-inputs.md`, the employer resolver
sketched against the two real cases rather than a hypothetical, and what the
`sweep_runs` gate actually needs.

## 1. What is stale in `phase3-inputs.md`

Measured against the live database on 2026-09-03. The document is two weeks old
and some of it was written before things it assumes changed.

| claim | as written | measured now | verdict |
|---|---|---|---|
| §3 employers | 1,934, one domain collision | **2,818**, zero unmerged domain collisions | numbers stale, conclusion intact |
| §5 `posting_id` | NULL for all 224 rows | 205 applications, **103 linked** | superseded, and see below |
| §5b backfill | 121 of 204 linked | 103 of 205 | **the link decays** |
| §6 fixtures | 27 in production | **none** | done |
| §7 `sessions` table | 88 rows against 89 files | 88 against **97** | drift grew from 1 to 9 |
| §8 the leak | one `active` orphan, shows in Needs you | orphan still there, **does not show** | consequence stale |
| §10 / §5 numbering | two sections numbered 5 | -- | fix while editing |
| extracted attrs | 0 of 22,762; years 9,483 (41.7%) | 23,141; years 9,596 (41.5%) | holds |

Four of those are worth more than a row.

### §5 is not just stale, it describes a decay nobody has named

The doc treats "a pruned posting cannot be resolved to" as a **ceiling on the
backfill**. It is worse than that: `applications.posting_id` carries
`ON DELETE SET NULL`, so pruning a posting **destroys a link that was already
resolved**. Measured: the backfill wrote 121 on 2026-09-01 and 103 remain,
**18 lost in two days** with nothing recording that they ever existed.

That changes the design input. `posting_id` alone cannot be how an application
knows its own name, because it evaporates on a schedule. Either the application
keeps a copy of the company and role at link time -- with a field saying where
they came from, the way `answer_record`'s `capture` level does -- or postings
that an application points at are exempt from pruning. The second is cleaner and
cheaper: 103 rows out of 23,141 postings.

This is also the first thing the population ratchet would have caught, and it
predates it. `applications.posting_id` sat at 59% and is at 50%.

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

## 2. The employer resolver, against the two cases that exist

`docs/employer-identity.md` has the policy. This is the resolver sketched
against real rows, because the two cases that exist need different mechanisms
and a design written against one of them fails the other.

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

### Case B: Appcast. Two employers, no domain on either, 137 postings split

```
Appcast Enterprise    norm_name "appcast enterprise"   domain (none)   79 postings
Appcastenterprise     norm_name "appcastenterprise"    domain (none)   58 postings
```

Domain equality cannot merge these -- neither has one. The names differ by a
single space. This is a **normalisation** failure, not a collision the current
detector can see, and it is why case A's mechanism is not sufficient.

Measured across all 2,818 employers, collapsing spaces and punctuation finds
**10 such groups**:

```
advancedrecoverysystems   Advanced Recovery Systems / Advancedrecoverysystems
appcastenterprise         Appcast Enterprise / Appcastenterprise
jpmorganchase             JPMorgan Chase / JPMorganChase
michaelpage               Michael Page Technology / Michaelpage
nomadfoods                Nomad Foods / Nomadfoods
publicisgroupe            Publicis Groupe / Publicisgroupe
rcmhealthcareservices     RCM Health Care Services / RCM Healthcare Services
soprasteria               Sopra Steria / Soprasteria
```

Eight of those are obviously one company each. **`michaelpage` is the warning
in the same list**: "Michael Page Technology" and "Michaelpage" squash together,
and so would "Michael Page Legal" and "Michael Page Finance". A rule that merges
on the squashed name would fold three business units into one, which is the
stranger's-logo failure at lane scale -- the thing `phase3-inputs.md` §3 says to
prefer a missed merge over.

So the squash is a **candidate generator, not a decision**. What promotes a
candidate:

- both rows have no domain, **and** their squashed names are equal, **and**
  neither name is a proper prefix of the other with the remainder being a word.
  That last clause is what separates Appcast (`appcastenterprise` ==
  `appcastenterprise`) from Michael Page (`michaelpage` is a prefix of
  `michaelpagetechnology`, remainder `technology`, a word).
- or the domains are equal, which is case A and already detected.

Everything else is recorded as a candidate and merged by nobody. A queue a human
reads is the right answer for a decision whose wrong direction is unrecoverable.

### Where it runs, and the ordering that matters

Before the upsert, never after. §5's connector takes the employer name from the
board's API, which is a fresh string every sweep; resolving after the insert
means the row already exists and the postings have already scattered.

And **the resolver must be the only place this decision lives**, for the reason
`posting_link.py` is the only place its decision lives: a backfill over history
and a connector writing a new posting cannot be allowed to disagree about
whether two names are one employer.

### What to measure before building it

- how many of the 2,818 employers have no domain at all (the population case B
  operates on),
- what the prefix-plus-word rule refuses, listed by name, so the refusals can be
  read rather than trusted,
- whether any `merged_into` chain is longer than one hop today.

## 3. The closure sweep, and what `sweep_runs` actually needs

The highest-consequence thing in Phase 3, and the existing file-store
implementation is better defended than the plan's one-line description suggests.
Read `jobs_store.close_missing` before designing anything: it already has four
brakes.

```
1  a board key absent from `seen` is untouched -- a board that errored, 304'd
   or was not fetched contributes nothing, and silence never reads as closure
2  `applied` and `passed` are terminal and are never closed
3  only jobs whose best sighting is an employer board are eligible; an
   aggregator result page is partial by construction
4  MAX_CLOSED_SHARE -- no single board may close more than a share of what is
   held for it
```

**And there is a fifth, structural one that is easy to lose in the port.** The
`seen` key is created *by a posting arriving*:

```python
seen_by_board.setdefault((source, company), set()).add(match_id)
```

A channel that returns `ok` with zero postings therefore never creates its key,
and brake 1 covers it. **The empty-response case is already safe, and it is safe
by accident of structure rather than by a check.** A Phase 3 closure step that
reconstructs "what was seen" from the `postings` table instead would lose that
property silently, because it would be asking a different question: not "did a
posting arrive under this key" but "what does the table hold".

### So what `sweep_runs` is actually for

Not the empty response. The case the current design cannot see is the
**half-complete** one: a connector that fetched page 1 of 5, wrote 40 of 200
postings, and returned success. The key exists, 160 postings look absent, and
only `MAX_CLOSED_SHARE` stands between that and closing four fifths of an
employer's catalogue.

`sweep_runs` replaces an inference with a declaration.

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

Four things it needs, and each has a failure it prevents:

**`complete` is written by the worker after the postings are committed, never
before.** A status written first is a lie the moment the write fails, and
closure would then read a partial sweep as whole. Same ordering argument as
`writes.ts`: commit, then publish.

**A run that never finishes must not stay `running` forever.** A crashed worker
leaves `running`, and if closure ignores `running` rows then one crash disables
closure for that board permanently -- which is the safe direction but silently.
A lease with an expiry, swept to `abandoned`, keeps it visible. `runlock`'s
lease is the precedent and its docstring already argues why a pid check is wrong
for work spanning processes.

**`job_count` is the evidence, and it wants the same ratchet as everything
else.** A complete run returning 40 where the last complete run for that board
returned 200 is a truncation wearing a success. Held against the board's own
previous complete run, with a tolerance, and a fall is a refusal to close rather
than a closure. This is `population.py` pointed at a third thing, and by then
the shape will have earned being factored out.

**Closure reads only `complete` runs from this sweep window**, and the window
has to be explicit. "The latest complete run per board" is not the same as "a
complete run in this sweep", and a board last swept successfully a week ago must
not have its absences read as closures today.

### What must not change in the port

The four existing brakes are not superseded by `sweep_runs` and should survive
it. `MAX_CLOSED_SHARE` in particular is the last line when every other signal
says the sweep was fine, and it is the one that has actually fired.

### What to measure before building it

- how often `closureRefused` fires today, and for which boards -- the report
  already carries it and nobody has read it,
- the distribution of postings per board, so `MAX_CLOSED_SHARE` and any
  `job_count` tolerance are set from the corpus rather than guessed,
- how many boards are reached through more than one channel, since the comment
  in `ingest.py` says a board reached twice must not look half-empty in either.
