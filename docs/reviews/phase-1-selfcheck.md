# Phase 1 — implementer's self-check

Written by the session that built it, against the invariant checklist in plan
§8 item 2. This is **not** the review. It exists so the independent reviewer
starts from a list of claims with evidence attached rather than from a diff, and
so anything I could not verify is stated by me rather than found by them.

Every line names the file and the test. Where I could not check something, the
line says so and says why.

Suite: **480 passing**, 17 files, `python3 -m pytest tests/ -q` on a clean
checkout of `feature/discovery-engine-phase-1` (which runs the Node tests too:
21 of the 480). 171 before this phase.

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
   executed by node against a DOM stub (`tests/test_found_script.py`, 15 tests)
   which covers the filtering, the sorting and a hostile title. Visual
   confirmation is the first thing to do in the supervised run.
4. **Nothing has touched the live store or the production queue.** The live
   `~/.job-application/` has no `jobs.json`, `passed.json`, `companies.json` or
   `search-config.json`, and the daemon has been running the untouched `main`
   checkout throughout. The supervised end-to-end run (plan step 9) has **not**
   been done and needs Sahil present.
