# Every legacy field, and where it went

`packages/schema/backfill.py` moves the file store's job records into
`postings`, `employers` and `job_decisions`. This is the map. It is not
documentation of the code: `tests/test_backfill.py` reads this file and fails
if a key present in the real records is neither mapped nor dropped here, and
fails again if a key listed here has disappeared from the records.

That check is the whole point. "No silent field loss" cannot be a rule someone
remembers during a 20,103-record migration; it has to be a list that a test
compares against reality.

Measured against the live store on 2026-08-29: 20,103 records, 30 distinct
top-level keys, plus the sub-keys of `extracted`, `location`, `score`,
`sources` and `sections`.

## Mapped

| Legacy key | Column | Note |
|---|---|---|
| `id` | `postings.legacy_id` | the 16-hex identity everything already references |
| `canonicalSource` | `postings.source` | |
| `canonicalUrl` | `postings.apply_url` | also the input to `board_key.derive` |
| (derived) | `postings.board_slug`, `postings.external_id` | `board_key.derive`, see that module |
| `company` | `employers.canonical_name` | 1,952 distinct; 3 records have an empty company |
| (derived) | `employers.norm_name` | `identity.norm_company`, the upsert key |
| `companyWebsite` | `employers.domain` | populated for 6,481 records |
| `logoUrl` | `employers.logo_url` | populated for 140 |
| `title` | `postings.title` | |
| `normTitle` | `postings.norm_title` | |
| `location` | (decomposed) | the container is not carried; its four sub-keys are the rows below |
| `location.raw` | `postings.location_raw` | |
| `location.city` | `postings.city` | |
| `location.region` | `postings.region` | |
| `location.country` | `postings.country` | 2 letters or absent; 1,029 empty |
| `workplace` | `postings.workplace` | remote / hybrid / onsite / null |
| (derived) | `postings.remote` | `workplace == 'remote'` |
| `postedAt` | `postings.posted_at` | null for 1,942 |
| `firstSeenAt` | `postings.first_seen_at` | |
| `lastSeenAt` | `postings.last_seen_at` | |
| `closedAt` | `postings.closed_at` | |
| `state` | `postings.closed_at` + `job_decisions.state` | see below |
| `extracted` | (decomposed) | the container is not carried; its sub-keys are the three rows below and the three dropped ones |
| `extracted.yearsMin` | `postings.years_min` | |
| `extracted.yearsMax` | `postings.years_max` | |
| `extracted.yearsText` | `postings.years_text` | |
| `descriptionExcerpt` | `postings.summary` | capped at 1,500 chars today, column allows 2,048 |
| `descFingerprint` | `postings.desc_fingerprint` | |
| `seenCount` | `postings.seen_count` | |
| `salary` | `postings.salary_raw` | free text like `127141-203388`; parsing it is not this migration's job |
| `lang` | `postings.lang` | |
| `tags` | `postings.tags` | |
| `sources` | `postings.sources` | jsonb, the full list and every element key with it; 29,523 elements across the pool |
| `sections` | `postings.sections` | jsonb, `requirements` and `responsibilities` |

### `state`, which is two facts

The legacy record has one `state` field holding five values. It becomes a
property of the posting and a property of the account's decision, and the split
is exact because `closedAt` is set if and only if `state` is `expired`,
measured 1,298 of 1,298.

| Legacy `state` | `postings.closed_at` | `job_decisions.state` |
|---|---|---|
| `new` (18,690) | null | no row |
| `expired` (1,298) | set | no row |
| `watched` (12) | null | `watching` |
| `passed` (9) | null | `dropped` |
| `applied` (94) | null | `submitted` |

`backfill.legacy_state` inverts it, and parity uses that one function rather
than restating the table, so the two cannot disagree.

## Dropped, with the reason

Each of these is read from the records and deliberately not carried. A key
appearing here is a decision; a key appearing in neither list fails the test.

| Legacy key | Why it is not carried |
|---|---|
| `industry` | null in 20,103 of 20,103. The field has never held a value. |
| `extracted.degree` | populated in 0 of 20,103. CLAUDE.md records the same measurement at 10,990 records on 2026-08-25, so this is not a recent regression, it is a field that has never been extracted. |
| `extracted.employmentType` | populated in 0 of 20,103, same as above. |
| `extracted.roleCategory` | populated in 0 of 20,103, same as above. |
| `configVersion` | the version of the scoring config that last touched the record. It describes a computation, not the posting, and the computation is being replaced: §2 and ADR 0007 make scoring lazy and per-account, so a global config version has no reader after Phase 1. |
| `score` | the same argument, and the strong form of it. `score` is a global rubric computed for one user's preferences, and ADR 0007 replaces it with `job_scores (account_id, posting_id, ...)` computed on the page being rendered. Carrying the old value into a shared corpus would put one account's preferences in every account's row. Its sub-keys go with it: `total`, `parts`, `weights`, `raw`, `why`, `unknowns`, `gates`, `configVersion`. |
| `keptByDecision` | 22 records. Prune metadata: it records that `jobs_store.prune` spared a row because a decision referenced it. In the new schema that is a foreign key from `job_decisions`, so the fact is structural rather than a note. |
| `stateRepairedAt` | 29 records. Repair metadata from a one-off state fix, describing an operation on the file store rather than the posting. |
| `stateRepairedWhy` | 29 records, same operation. |
| `sources[].tier` | ranking used when choosing a canonical URL among sightings. That choice has already been made by the time a record exists, and `canonicalUrl` holds its result. The raw list is still carried in `postings.sources`, so nothing is unrecoverable. |

Note the last one is dropped from the *mapping*, not from the data: `sources`
is carried whole as jsonb, so `tier` is still there to read. It is listed
because it is the only element key that gets no first-class treatment anywhere,
and saying so is cheaper than the next reader working it out.

## How a container covers its sub-keys

A key is accounted for when it appears above, or when a container that holds it
does. `score` is dropped whole, so `score.total` goes with it; `sources` is
carried whole as jsonb, so `sources[].url` goes with it. `extracted` and
`location` are the other case: the containers themselves are not carried at
all, their sub-keys are lifted into columns individually, and both have a row
above saying so.

The distinction matters because the two failure modes are different. A dropped
container takes its contents with it, which is a decision. A decomposed
container has to name every sub-key, because one left out is a column nobody
notices is missing.

## What is not backfilled at all

`postings.body_r2_key` stays null. The file store has no per-posting body in
R2: it holds a 1,500-character excerpt, which becomes `summary`. Populating
`body_r2_key` is Phase 3 work, where the cloud sweep writes bodies as it
fetches them. A null here means "no body stored", which is true, rather than
"body missing", which would be a fault.
