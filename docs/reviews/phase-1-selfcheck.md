# Phase 1 — implementer's self-check

Written by the session that built it, against the invariant checklist in plan
§8 item 2. This is **not** the review. It exists so the independent reviewer
starts from a list of claims with evidence attached rather than from a diff, and
so anything I could not verify is stated by me rather than found by them.

Every line names the file and the test. Where I could not check something, the
line says so and says why.

Suite: **505 passing**, 17 files, `python3 -m pytest tests/ -q` on a clean
checkout of `feature/discovery-engine-phase-1` (which runs the Node tests too:
21 of the 505). 171 before this phase.

**Revised after independent review.** The review found one Major and three
Minor; all four are closed, and `docs/reviews/phase-1.md` is the record. Where
a fix changed a claim below, the claim is rewritten rather than annotated —
except where the original claim was wrong in a way worth remembering, which is
noted as such.

---

## 1. Single-key KV discipline, with the ops/day maths redone

**Claim:** no new keys, no list operations, and the poll costs exactly what it
did before.

`watch`, `pass` and `searchConfig` all live in the existing `jobby:queue`
object. `deploy/kvqueue.mjs` has one `KEY` constant and it is unchanged.

| Operation | Per day | Ceiling |
|---|---|---|
| Poll (1 read, 3s interval) | 28,800 | |
| Config take — only when one is waiting; an empty take is **not** a write | ≤ 20 reads | |
| Claims for watch/pass — guarded by a non-empty check | ≤ 100 reads + 100 writes | |
| Board clicks (enqueue) | ~50 writes | |
| Board publishes: 24 hourly ingests + 6 sweeps + user actions | ~60 writes | |
| **Reads** | **~28,950** | 100,000 |
| **Writes** | **~210** | 1,000 |
| **Lists** | **0** | 1,000 |

Headroom is unchanged from before Phase 1, because the only new *recurring*
cost is zero: the config field rides the poll that was already happening.

- Evidence: `tests/kvqueue.test.mjs` — `one poll costs exactly one read`,
  `taking nothing is not a write`, `the eye and pass ride the existing key with
  no new one`.
- The cousin of the `claim` bug is pinned in both directions:
  `saving a search config does not clobber the queues`, `queueing a click does
  not clobber a saved search config`, `claiming a queue does not clobber a saved
  search config`, `claiming the watch queue leaves the apply queue alone`.
- Backward compatibility with the live key, which predates all three fields:
  `a value written before these fields existed still reads correctly`.

## 2. The watcher claims, runners never

**Claim:** untouched.

`claim_one` is still called only from `tick()`. `ingest.py` never reads the
queue — it is handed nothing and asks for nothing; the watcher spawns it and it
talks to the store and to HTTP only. `promote.py` takes `--id` and cannot reach
the queue either.

- Evidence: `tests/test_watch.py` — `test_the_runner_refuses_to_claim_for_itself`
  (pre-existing, still passing).
- `grep -n "queue.mjs" scripts/ingest.py scripts/promote.py` returns nothing.

## 3. One browser per slot

**Claim:** untouched, and not reachable from anything added.

Phase 1 opens no browser at all. Nothing in `scripts/sources/`, `ingest.py`,
`identity.py` or `promote.py` imports `browser`, and no Playwright dependency
was added.

- Evidence: `tests/test_watch.py::BrowserConfigTests` (pre-existing, 12 tests,
  still passing).
- `grep -rn "playwright\|import browser" scripts/sources scripts/ingest.py` is
  empty.

## 4. Atomic writes with unique temp names, on every new writer

**Claim:** every new writer goes through `store.write_private`, which is the
`mkstemp` fix from §13 item 7. No writer added here rolls its own.

Writers added: `jobs.json`, `passed.json`, `companies.json`,
`search-config.json`, `source-cache.json`, and `meta.json` via the new
`store.py meta-set-json`.

- Evidence: `tests/test_jobs_store.py::ReadFailureTests::test_every_new_writer_uses_the_shared_atomic_writer`
  spies on `store.write_private` and asserts all four store files route through
  it, and that `store.py` still uses `tempfile.mkstemp`.
- Permissions: `test_the_pool_is_written_owner_only` and
  `test_a_saved_config_is_written_owner_only` assert mode 0600.

## 5. No silent-success paths

**Claim:** every failure is visible somewhere a human looks, and an empty result
is never confusable with a failed one.

**This claim was incomplete when first written, and the review found the hole.**
`meta.lastIngest` reported `dedupedBy: {"id": 1}` on the measured run, which
looked like one ordinary merge and was in fact one requisition being eaten by an
id collision the matcher had explicitly declined (review finding 1). Every
failure was visible; one *success* was a lie. A merge is now only ever recorded
against a reason the matcher actually gave, asserted by
`test_no_merge_is_ever_recorded_without_a_stated_reason`.

- A corrupt pool raises instead of reading as empty:
  `test_a_corrupt_pool_raises_rather_than_reading_as_empty`,
  `test_a_pool_of_the_wrong_shape_raises`,
  `test_a_corrupt_config_raises_rather_than_reading_as_unsaved`,
  and in the watcher, `test_an_unreadable_pool_raises_rather_than_reading_as_empty`.
- Connectors distinguish three states, not two:
  `test_a_skip_is_never_an_empty_success`,
  `test_a_scan_that_found_nothing_differs_from_one_that_did_not_run`.
- A missing key is announced, not absent, and names the variable to set:
  `test_every_keyed_connector_reports_a_visible_skip`,
  `test_a_skip_names_the_variable_to_set`.
- `lastIngest` is written on the failure path:
  `test_last_ingest_is_written_when_the_run_fails`.
- One connector raising does not silence the others, and does not vanish:
  `test_a_connector_that_raises_does_not_take_the_others_with_it`.
- A dead board token is counted rather than swallowed:
  `test_a_dead_token_is_reported_rather_than_swallowed`,
  `test_one_dead_board_does_not_lose_the_others`.
- All of it reaches the board:
  `tests/test_dashboard.py::FoundHeaderTests` — skipped channels, connector
  outages, an outright failed scan, and running on defaults each render
  distinctly.
- A saved search that the single-key write lost is reported rather than assumed:
  `tests/test_found_script.py::SaveReconciliationTests` (review finding 3).
- A promote that fails carries its reason, and the recovery path is named in the
  code rather than left to be inferred (review finding 2).

## 6. CSP, and every colour in all three theme blocks

**Claim:** the page still loads nothing from anywhere, and no colour is defined
only inside a media query.

- Evidence: `tests/test_dashboard.py::CspTests::test_the_page_loads_nothing_from_anywhere`.
- Measured on a real render: 0 external `src`/`href` in markup, 0 `@import`,
  0 `url(http`.
- Theme completeness is now asserted structurally rather than by eye:
  `ThemeCompletenessTests::test_the_two_dark_blocks_define_the_same_colours`
  and `test_every_dark_override_has_a_light_definition`. The two colours Phase 1
  added (`--t-red`, `--red-ink`) are covered by
  `test_the_found_lane_colours_exist_in_all_three`.
- Escaping of untrusted job text:
  `test_a_hostile_title_cannot_escape_the_data_block` feeds
  `</script><img src=x onerror=alert(1)>` through a title and asserts it comes
  out as characters; `test_a_javascript_url_never_becomes_a_link`;
  `test_no_job_text_is_ever_concatenated_into_markup` asserts the client script
  contains no `innerHTML`, `insertAdjacentHTML` or `document.write`.
- Page weight at the agreed 800-job cap, measured: **1,005,399 bytes (0.96 MB)**,
  of which 828,831 is the embedded JSON. Target was < 4 MB. Pinned by
  `test_the_page_stays_under_four_megabytes_at_the_cap`.

## 7. Locks and races on anything concurrent

**Claim:** the Tier-1 scan cannot run twice, cannot run beside an agent sweep,
and cannot wedge the schedule if it dies.

- It takes the existing `scan` lock rather than a new one, so an agent sweep and
  an API ingest are mutually exclusive by construction (`scripts/watch.py`, the
  `if not lock_holder("scan")` guard).
- The schedule is advanced *before* the spawn is attempted, so a failed spawn
  cannot retry every three seconds for an hour:
  `test_the_schedule_moves_before_the_spawn_is_attempted`.
- An unreadable schedule rebuilds rather than stopping forever:
  `test_an_unreadable_schedule_rebuilds_rather_than_stopping_forever`.
- Idempotency where the board re-sends clicks:
  `test_the_eye_pressed_twice_creates_one_session`,
  `test_passing_tombstones_and_is_idempotent`,
  `kvqueue.test.mjs::the eye is idempotent per job id`.
- Existing slot/lock tests all still pass untouched (`LockTests`,
  `ApplySlotTests`, `SlotRaceTests`).

**Not verified:** two watchers ticking simultaneously with the scheduler armed.
The `single_watcher` lock makes it unreachable and I did not run a second
watcher, per the standing instruction not to.

## 8. PII scan of new artifacts, logs, fixtures and snapshots

**Claim:** clean.

- Every file in `git diff --name-only main...HEAD` scanned for name, phone,
  address, personal email, the healthchecks URL, and any assigned API key or
  token. No hits.
- Fixtures are scanned by the suite itself, so it stays true:
  `tests/test_sources.py::FixtureProvenanceTests::test_no_fixture_carries_personal_data`.
- The fixtures are public job postings from public board APIs, trimmed to two
  or three entries each. `syndicated_jd.json` is a fictional company written for
  the purpose.
- No Playwright snapshot was written: Phase 1 opens no browser (§13 item 11).
- The only personal-ish string in the diff is the repository URL in the
  `User-Agent`, which is deliberate — an honest User-Agent naming the project is
  the courtesy rule, and that URL is already public.

## 9. No Sahil-specific constants in code

**Claim:** nothing in the diff encodes this particular job search.

- `search_config.DEFAULTS` carries no titles, no keywords, no countries and no
  experience ceiling. Pinned by
  `test_the_defaults_carry_nobody_s_search`.
- `REGION_PACKS`, the rubric weights, the caps and the cadences are generic
  module constants, which the plan explicitly permits.
- `SEED_PACK` is 96 companies chosen for regional coverage of the nine target
  regions, not for one person's shortlist; region coverage is asserted by
  `test_the_pack_reaches_every_region_the_plan_names`.
- The clearance-gate phrases are about eligibility to hold a role, not about
  one person's visa position; the standing answers still own that.

---

## A limitation the review named that I had not

**The company alias list is load-bearing.** Rule 4 only compares fingerprints
within a company bucket, so an ATS calling a company "Northwind" and an
aggregator calling it "Northwind Analytics Inc." stay two records unless
`companies.json` carries an alias joining them. A missing alias shows up as a
duplicate rather than as an error, and nothing warns. Correct by design —
widening the company match would merge two real companies, which is worse — but
it belonged in the gaps list and was not there. Now recorded in both
`references/sources-registry.md` and the system reference.

## What I could not check

1. **The five keyed connectors have never had a live response.** No key is
   installed. They are written to each published contract, their fixtures are
   labelled `__fixture_note: "not a recorded response"`, and a test asserts that
   label is present. The first run with a key installed is the real check.
2. **The acceptance box "jobs from ≥ 8 target countries after a full day"** is
   therefore deferred, not met. Its own clause says "given keys installed".
   Tier 1a reached 8 countries in a single 13-second run (US, DE, PH, AU, BR,
   IL, UA, CA) but most remote-feed postings carry no country at all, so I would
   not claim that box as passed.
3. **No browser render.** The sandbox blocked a local HTTP server, so I could
   not photograph the Found lane. Instead the client script is parsed and
   executed by node against a DOM stub (`tests/test_found_script.py`, 20 tests)
   which covers the filtering, the sorting, the save reconciliation and a
   hostile title. Visual confirmation is the first thing to do in the supervised
   run, and the review agrees: a lane nobody has seen is not a lane that ships.
4. **Nothing has touched the live store or the production queue.** The live
   `~/.job-application/` has no `jobs.json`, `passed.json`, `companies.json` or
   `search-config.json`, and the daemon has been running the untouched `main`
   checkout throughout. The supervised end-to-end run (plan step 9) has **not**
   been done and needs Sahil present.

## The live run, and the two things it found

Both "could not check" items 3 and 4 are now closed. The board has been rendered
in a real browser against the real store, and `~/.job-application/` has gained
`jobs.json`, `search-config.json` and `companies.json`.

The run: **32,142 found, 13,422 kept, 3,500 merged, 15,220 filtered, 752
requests, 965s, zero errors.** Merge reasons were 529 canonical-url, 1,889
company-title-location, 1,082 fingerprint, and no bare-id merges, which is the
shape the identity rules predict. The store holds 13,721 records across 4,151
companies, 6,096 of them direct ATS or careers-page listings. Every configured
location clears the embedding floor: US 1,007 cards, Canada 296, and 155 each
for India, Australia, the UK, the UAE, Germany, Singapore, Ireland and the
Netherlands, against pool reach between 9.3% and 53.8%.

Rendering it turned up two defects that no test on either side would have
caught, because both live in the seam between the payload and the page.

**The workplace vocabulary did not match its own filter.** `compact` shipped
`wp` as a display label (`"Remote"`, `"On-site"`) while the workplace checkboxes
compare against slugs (`remote`, `onsite`). `indexOf` never matched, so every
job whose workplace was known was permanently invisible: 1,394 of 3,400 embedded
cards, 41%. Nothing failed. The board just showed a smaller number and looked
entirely healthy, which is the same failure mode as the payload slimming that
broke the description search.

The reason it survived is worth naming. `tests/test_found_script.py` builds its
payload rows by hand, so it asserted the client's behaviour against a payload
`compact` does not produce; and `test_dashboard.py` asserted `compact` produces
the label, pinning the wrong side of the contract. Two green tests, one on each
side of a seam neither crossed.

The fix ships the slug and lets the card render the label. The guard is a new
`RealPayloadTests`, which runs real `compact` output through the real script,
plus a vocabulary pin: every value `compact` can put in `wp` must be a value a
checkbox can select. All four fail on the old code.

**The page had no doctype, so phones rendered it in quirks mode.** `document.
compatMode` was `BackCompat`, which makes the browser ignore
`width=device-width` and lay the board out at 980px before zooming it down to
fit. The `<meta name="viewport">` tag was present and correct the whole time and
was simply not being honoured. Legible on a laptop, about a third size on a
phone. This one predates the branch (`main` has no doctype either), but the
board is the first page on this dashboard that anyone would read on a phone.
Fixed by emitting `<!doctype html>`. It switches the whole dashboard from quirks
to standards mode, so it is worth a reviewer's eye on the other five lanes
rather than the Found lane alone.

The narrow-screen CSS itself was already right: one column under 900px, the
full-bleed negative margins reset, four columns above 1180px.

Item 2 stays deferred on its own terms. The live run reached far more than eight
countries, but the acceptance box asks for a full day and this was sixteen
minutes.

## Where the live pass ended

Three runs against `~/.job-application`, and the numbers below are the third,
which is the one with every gate and the retention ceiling in force.

    found 17,874 | kept 58 | merged 11,466 | filtered 6,348
    pruned 1,249 | stored 13,812 | 752 requests | 878.9s | errors 2

A second and third run look nothing like a first, and that is the point: run one
kept 13,422 because the store was empty, run three kept 58 and merged 11,466
because almost everything it found was already there. Channels reporting
`found 0` are conditional requests working — ETag and If-Modified-Since mean an
unchanged board costs one 304 and contributes nothing.

The store now holds 13,812 records across 4,442 companies, 5,064 of them direct
ATS or careers-page listings, 4,254 with a required-experience figure pulled out
of the body. Zero dated postings sit past the two-month ceiling; 1,253 did before
retention learned to judge on the same clock the gate judges on. Every configured
location clears the embedding floor: US 998 cards, Canada 295, and 155 each for
India, Australia, the UK, the UAE, Germany, Singapore, Ireland and the
Netherlands.

The two remaining errors are question 14, diagnosed and left alone deliberately.

### The board, in a browser, at phone width

Every lane measured by cloning its cards into a 375px box and reading intrinsic
width, which answers the min-content question directly rather than relying on
viewport emulation.

Needs you fits, including the row that went from three controls to four: `.act`
wraps to a second row rather than widening the card, and an eighty-character
role title does not change that. In flight fits, including the Stop that is
built client-side and therefore invisible to any static read of the server HTML.
Watching, Submitted and Dropped hold tables 900–939px wide inside a wrapper that
is 375px with `overflow-x: auto`, so the table scrolls within the card and the
page does not. No lane scrolls the page sideways.

Found needed two fixes to get there, both in this document above: the doctype,
and the grid track that one tracking URL could blow out.

### What is still not done, and cannot be from here

**No automatic cycle has been observed.** The scheduler arms correctly — with a
search saved, `tier1_due()` returns True against the live store, where before a
config existed it always returned False. But the launchd agent (`ai.jobby.watch`,
pid 13068) runs the `main` checkout, and `main`'s `watch.py` has no `tier1_due`,
no `found_signature` and no `apply_found_decisions`. The scheduler cannot fire
until this branch is merged, because the daemon is not running this code.

Pointing the agent at this worktree would deploy unmerged code to the live
pipeline, and running `watch.py --once` by hand would contend with the daemon
for the single-watcher lock and could claim real apply requests. Neither is
worth doing to watch a timer. The first automatic cycle is a post-merge
observation, and it is the first thing to check after merging.
