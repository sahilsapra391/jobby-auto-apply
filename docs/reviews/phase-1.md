# Phase 1 — code review record

Required by plan §8 item 3. The review was performed by a **separate session**,
not the one that wrote the code. It pulled both branches, ran the suite itself
on a clean checkout, and wrote its own adversarial tests rather than reading the
implementer's.

**Verdict: APPROVE WITH CONDITIONS.** One Major, three Minor, nothing Blocking.
All four are closed below.

| | Before review | After fixes |
|---|---|---|
| Suite | 480 (473 passed + 7 environment skips) | **505** |
| `main` baseline | 171 (164 + 7) | unchanged |

The 7 skips are the pre-existing Chrome and pymupdf environment skips, identical
on both branches. `test_kvqueue.py` genuinely runs rather than skipping, so the
Node tests really do execute inside pytest.

---

## What the reviewer verified independently

No action was needed on any of these; they are recorded because a gate is only
worth something if it says what was actually checked.

- **KV discipline.** One `KEY` constant, unchanged. `readQueue`'s
  `{...empty(), ...JSON.parse(raw)}` means the live key — which predates
  `watch`, `pass` and `searchConfig` — reads correctly rather than handing the
  watcher `undefined`. Clobber tests are bidirectional. Zero new keys, zero list
  operations.
- **Escaping.** The reviewer wrote its own hostile posting:
  `</script><img src=x onerror=…>` in the title, a cookie-exfiltration script as
  the company name, `javascript:` URLs, U+2028 in the body. All five attacks
  contained, blob still parsed. `script-src 'unsafe-inline'` was already on
  `main`, so no CSP regression.
- **Page weight.** 2,000 jobs rendered independently → 1.09 MB, exactly 800
  embedded, honest "of 2,000" footer.
- **Atomic writes.** Every new writer goes through `store.write_private`; the
  three direct `open(..., "w")` sites are `mkstemp` transfer files unlinked in
  `finally`. No shared temp names.
- **Polite fetching.** 1s per host, honest UA, 20s timeout, 3 attempts,
  conditional requests, 12 MB cap, `Passthrough` treating every status as data.
- **PII.** Clean. The only hits are the implementer's own banned-substring test
  and the repository URL in the User-Agent.
- **Coverage accounting.** 117 sources catalogued: 81 rotation, 16 planned,
  9 connected, 5 needs-key, 3 blocked, 3 excluded. Exceeds Appendix A.
- **≥6 sweeps enforced.** `1`, `5`, `0`, `−3`, `'x'`, `None` all rejected.
- **Deviations #1 and #2** both reasoned correctly. Keep both.

---

## Finding 1 — MAJOR — aggregator-only requisitions collide into one id

**Closed.** Commit `b57fd8c`.

### What was wrong

A seam between two mechanisms that were each correct alone. `match()` declines
to merge two requisitions whose bodies are far apart — deviation #1, working as
designed. But the fallback dedupe key is *exactly* the tuple rule 3 matches on,
so both derived the same id, and `upsert` folded them together on the id alone.
The matcher's refusal was overruled by the id derivation.

The result carried a "seen on 2 boards" chip, which is the most convincing
possible disguise, because that chip means precisely the thing that did not
happen. Nothing errored.

### It was live, not armed

The reviewer framed this as a Tier-1b risk that activates when keys land. It was
worse than that. `canonical_rank("feed")` is 2, so RemoteOK, We Work Remotely,
Remotive, Himalayas and Working Nomads all take the fallback path today, and
roughly 47 of the 53 jobs in the earlier measured run were fallback-keyed.

That run's own report said `dedupedBy: {"id": 1}`. Refetching the two URLs
behind it identified what it had eaten:

| | |
|---|---|
| Company | Peroptyx |
| Title | Data Analyst (No Experience Required) |
| Record kept | `workingnomads/job/go/1775628` — Canada |
| Record eaten | `workingnomads/job/go/1775548` — Japan |
| Hamming distance | **9** (threshold 6) |

Two genuinely different requisitions. Note that deviation #2's country guard
could not help: the feed marks everything remote, so both bucketed as `remote`
and only the bodies separated them.

### The fix

`identity.resolve()` now owns one guarantee: **an unmatched job never comes back
with an id that is already taken.** It can always keep that guarantee, because a
collision there implies rule 3 looked at this identity and refused it, and rule
3 only refuses through the fingerprint veto, which requires both fingerprints.
So there is always something to discriminate with exactly when it is needed.
Where there genuinely is not — a body too thin to fingerprint — the conservative
merge is left alone, because with no evidence merging is the better guess.

The discriminator is a parameter rather than folded into every fallback key, so
an ordinary record's id stays a pure function of what the record *is* and does
not move because an employer fixed a typo.

`jobs_store.upsert` gained the defensive half: told the matcher declined, it
refuses an occupied id loudly rather than folding two requisitions together
somewhere nobody would look. `resolve()` makes that unreachable; `ingest` catches
it per posting, counts it in `errors`, and keeps going.

### Verified after the fix

Re-running the live Working Nomads feed: both Peroptyx requisitions stored
separately, each keeping its own body and its own URL, and `dedupedBy` carrying
only matcher-agreed reasons.

### Tests that pin it

`tests/test_identity.py::UnmatchedIdCollisionTests` and `::IdStabilityTests`,
`tests/test_ingest.py::UnmatchedCollisionTests`,
`tests/test_jobs_store.py::UpsertAndMergeTests::test_the_store_refuses_to_merge_what_the_matcher_declined`.

Covering, in order: the exact reproduction; the real-world Peroptyx shape; the
counterpart that must still collapse (one JD across three aggregators with no
ATS URL anywhere); thin bodies keeping the conservative merge; one thin and one
thick body also merging; the store guard in both directions; and four
id-stability properties, including that both records keep their ids on the next
hourly run rather than piling up a new one every hour.

### Why the original suite missed it

`syndicated_jd.json`'s `sibling` carries a Greenhouse URL, so
`test_two_real_openings_at_one_company_stay_two` took the `url:` key path and
never reached the fallback. The test was correct; it simply could not get to the
branch. The new fixtures are aggregator-class with no ATS URL anywhere, which is
the shape every Tier-1b connector and every remote feed actually produces.

## Finding 2 — MINOR — a failed promote loses its click

**Closed by comment,** as the reviewer allowed. Commit `b98c0de`.

The behaviour was already correct and self-healing; what was missing was any way
for a reader to see that. The claim site now names the board's localStorage
replay as the recovery path, and says why re-queueing there would be wrong: the
replay re-sends every eye and pass on load and prunes the ones the published
board no longer carries, so a transient failure retries within seconds and a
permanent one is retried at most once more and then forgotten. Re-queueing would
race that replay and promote the same job twice.

Failures now carry their reason rather than a bare id, because "no job with that
id" and "the store would not write" want very different responses.

**No board surface was added, deliberately.** The condition self-heals, and a
banner for a self-healing condition teaches people to ignore banners. It is
visible in the tick line and in the healthchecks payload, which is where a
standing condition belongs.

## Finding 3 — MINOR — a lost config save is silent to the user

**Closed by implementing the reconciliation.** Commit `b98c0de`.

The reviewer identified the asymmetry exactly: a lost click is re-sent by the
replay, but a lost save leaves the banner reading "saved" while the next scan
quietly runs the old search.

The banner now carries `data-version`, the version the page was rendered from.
After a save the board reconciles against it:

| State | What the user sees |
|---|---|
| Version moved | "Saved search v8 is the one running." |
| Something still pending | "Saved. Waiting for the next check to pick it up." |
| Nothing pending, version unmoved, inside grace | "Saved. Waiting for the board to catch up." |
| Nothing pending, version unmoved, past grace | "That save did not land. Press Save search again." |

Grace is ten minutes: the watcher takes a config within three seconds and the
board republishes within a couple of minutes, so a slow cycle never reads as a
loss. A lost save is reported once and the marker cleared, rather than nagging
on every load.

Tests: `tests/test_found_script.py::SaveReconciliationTests` (five, run under
node against a DOM stub), plus two render tests asserting the version attribute
is present even when no search has ever been saved — absent would read as `NaN`
on the client and every save would look lost.

## Finding 4 — MINOR — company-name mismatch defeats the fingerprint rule

**Closed by recording it.** Not loosened, as advised. Commit `b98c0de`.

Recorded in `references/sources-registry.md` and in the system reference's known
gaps: the registry's alias list is load-bearing for cross-source dedupe, because
rule 4 only compares fingerprints within a company bucket. A missing alias shows
up as a duplicate rather than as an error, and nothing warns.

Widening the company match is not the fix — merging two real companies is the
worse failure and much harder to notice. Seeding aliases from company-name
variants observed across sources is noted as a **Phase 2** candidate and
explicitly not built here.

---

## Reviewer confirmations on the open questions

| Question | Outcome |
|---|---|
| Deviation #1 — rule 3 fingerprint veto | Keep, as reasoned |
| Deviation #2 — rule 4 location guard | Keep, as reasoned |
| #4 `sources.json` at repository root | Fine, no action |
| #5 defaults before a config is saved | Accepted; keep the "ran on defaults" line on the board |
| #6 add-your-own-link on the scan queue | Accepted — no new endpoint, no new key, http(s) only at both ends |
| #7 ingest does not publish by default | Keep; routing through the normal path preserves the quota back-off and the two-minute floor |
| #8 `roles.json` as URL source only | Confirmed, matches `dupe_check` |
| #9 The Muse needs no key | Good discovery, already recorded |
| #3 "≥ 8 countries" acceptance box | Stays **deferred pending keys**, not failed. Re-check within a day of keys landing |

---

## Outstanding before merge

- [ ] Supervised end-to-end run, plan step 9's second half. Needs Sahil at the
      keyboard. Nothing has touched `~/.job-application/` or the production
      queue.
- [ ] Look at the Found lane in a browser. It has never been seen; the sandbox
      blocked a local server, so the client script is verified by node against a
      DOM stub instead.
- [ ] Re-check the ≥ 8 countries box if the API keys land.

## Changes made after the review, at the sign-off's request

**These need a re-review before merge.** The reviewed diff is no longer the
merged diff, and that is stated here rather than left to be discovered.

All of them came out of the supervised run, with Sahil watching the board.

| Change | Why | Scope |
|---|---|---|
| Required-experience extraction | Cards read "experience unknown" on every job | Phase 2, pulled forward |
| Six more board platforms — SmartRecruiters, Workable, Recruitee, Personio, Breezy, Rippling | "I want all these sources" | Phase 2, pulled forward |
| Registry 96 → 304 verified boards | The largest multiplier on direct listings | Phase 1 |
| Greenhouse bodies with the list | Per-job fetching capped a sweep at 300 bodies | Phase 1 |
| Retention prefers direct listings | The cap was deleting 85% of ATS jobs | Phase 1, defect |
| Found leads with direct listings | "First class priority to direct listings" | Phase 1 |
| Bounded, rotating query fan-out | 400 requests/hour would burn the free quotas | Phase 1, defect |
| Careerjet over HTTP with a Referer | It refuses TLS and refuses undeclared callers | Phase 1, verify-at-build |
| Careerjet sightings collapse per source | Its URLs are per-request tracking tokens | Phase 1, defect |
| "Seen on N boards" counts boards | Adzuna lists one job twice | Phase 1, defect |
| 60d/90d windows, configurable storedCap | "No cap on how many job descriptions" | Phase 1 |
| Tab order, full-width lane, folded filters, drawn eye, one button shape | First look at the lane | Phase 1 |

### Defects the supervised run found that the tests did not

Worth reading as a group, because they share a shape: every one produced a
board that looked healthy.

1. **The cap was evicting direct listings.** 1,482 ATS postings came in, 214
   survived. Aggregator snippets are keyword-dense and out-scored real job
   descriptions. Fixed in `keep_priority`.
2. **An unmatched job inherited an occupied id** — the Major, found by review,
   confirmed on real data eating a Peroptyx requisition.
3. **Careerjet's URLs change every request**, so sightings would have grown to
   "seen on 24 boards" within a day.
4. **The query fan-out was unbounded**, and blew a ten-minute timeout on its
   first real run.
5. **`--limit 50` made the ATS channels look broken.** Not a code defect — the
   plan's own supervised step specifies that flag, and Greenhouse has 170
   boards. Worth recording because it nearly sent the diagnosis the wrong way.

### The final measured run

27,420 postings seen, 12,003 kept, 3,207 merged, 583 requests, 11.3 minutes,
one error. 4,576 direct ATS or careers listings across 3,885 companies, 3,684
with required experience extracted. Pool uncapped at 12,003 jobs in a 37 MB
file; the board embeds 800 and says so.

Three connectors reported `skipped — no boards in the company registry`, which
is the registry-driven point demonstrated rather than argued: Breezy, Personio
and Rippling work, and no company Jobby knows uses them yet.

## Round two — re-review after the supervised-run changes

Same independent session, both branches pulled again, suite run on its own, and
fresh adversarial tests rather than a read of mine.

**Verdict: APPROVE WITH CONDITIONS.** One Major, three Minor, nothing Blocking.
The Major is a design decision rather than a defect. All four closed below.

| | Round one | Round two |
|---|---|---|
| Suite | 505 | **729** |
| `main` baseline | 171 | 236 |

### What round two verified independently

- The round-one Major is genuinely closed. The reviewer re-ran its own
  reproduction against the fixed code: two aggregator-only requisitions with
  far-apart fingerprints produce two records; the syndicated counterpart still
  collapses; thin records stay conservative. It rates the fix better than the
  finding asked for.
- `keep_priority` is right, and the discovery behind it — aggregator snippets
  are keyword-dense and out-score real job descriptions — is the sharpest thing
  in the session.
- The merge with main is clean where it counts: `SLOT_COUNT = 5` identical on
  both sides, and Tier-1 ingest takes the scan lock rather than an apply slot,
  so a 14.8-minute sweep and five concurrent applications never contend. That
  was the highest-risk hypothesis and it is disproved.
- Storage is a non-issue: 12,000 jobs is 24 MB, 0.39 s to write, 0.29 s to read.
- Careerjet over plaintext is accepted: an affiliate ID is not an account
  credential, and the downgrade is documented rather than papered over.

### Finding 1 — MAJOR — the filters reached 6.7% of the pool

**Closed.** Commit `e1bc513`.

`select()` took the top `EMBED_CAP` globally by score, so every regional filter
got a slice biased by whichever region was densest — and the footer said
"refine the filters", implying refining searched the pool when it re-sorts the
same records.

Three changes. The budget is now spent in two passes: each location the saved
search names gets a guaranteed floor from its own best, and the remainder plus
any unused floor is spent globally on the same ordering. Direct listings keep
their priority inside the selection. The payload lost a third of its weight —
weights hoisted, explanations interned, the duplicated excerpt dropped, 1,576 →
857 bytes a job — which paid for the cap going from 800 to 3,400 at a measured
3.12 MB. And the footer stopped claiming a reach the page does not have, with a
per-country pool census travelling alongside so the count names the pool total
for the locations actually selected.

Measured on the real 12,802-job pool:

| Region | In pool | Before | After |
|---|---|---|---|
| India | 1,278 | 32 (3%) | 155 (12%) |
| Japan | 13 | 0 (0%) | 5 (38%) |
| UAE | 442 | 3 (1%) | 155 (35%) |
| Singapore | 411 | 7 (2%) | 155 (38%) |
| United Kingdom | 1,628 | 32 (2%) | 155 (10%) |
| Canada | 1,634 | 115 (7%) | 319 (20%) |
| Australia | 764 | 1 (0%) | 155 (20%) |
| Germany | 943 | 16 (2%) | 155 (16%) |
| Netherlands | 673 | 4 (1%) | 155 (23%) |
| United States | 2,595 | 342 (13%) | 1,034 (40%) |
| Remote | 2,096 | 252 (12%) | 875 (42%) |

Raising the cap exposed the next thing: `render()` drew a card per match, which
at 3,400 is ~50,000 DOM nodes. Cards are drawn a window at a time now — the
lazy render §5 asks for and I had not built.

Two bugs fell out. `cap=EMBED_CAP` was a default argument bound at import, so
changing the constant did nothing and three measurements in a row returned the
same number, which is what gave it away. And the keyword haystack still
referenced `job.ex` after the payload slimming removed it, so description-scope
search silently matched nothing in the body.

Pinned by `tests/test_dashboard.py::EmbeddingReachTests`, `::PoolCensusTests`,
`::FooterHonestyTests`, `::PageWeightAtCapTests` and
`test_found_script.py::test_description_scope_searches_the_description`.

### Finding 2 — MINOR — the fixture-provenance claim had gone stale

**Closed.** Commit `c991806`. All five keyed fixtures re-recorded from live
authenticated responses; the note is gone and the test asserts the opposite.

The re-recording found what the review hoped it might: **Adzuna exposes no
employer origin link.** Plan §3.2 assumes aggregators usually do. Adzuna's
`redirect_url` has nothing embedded, so identity rule 1 can never fire for an
Adzuna sighting — its copies merge on company/title/country, on the
fingerprint, or not at all. The contract-derived fixture had invented an
embedded link and the test on it was passing against fiction.

A second test checks no fixture carries a live key, which matters because
Adzuna echoes the app id inside every `redirect_url` it returns.

### Finding 3 — MINOR — eight modules tested less than they reported

**Closed.** Commit `20519ab`. `unittest.main()` above the last classes meant a
direct run silently omitted them. Eight modules had it, four predating this
work; `test_watch` was skipping seven classes including every slot-race test.
Standalone now matches pytest module for module, and `test_suite_shape.py`
keeps it true.

### Finding 4 — MINOR — two extractor recall gaps

**Closed.** Commit `c991806`. Spelled-out numbers, and figures qualified by a
role noun rather than the word "experience". Every one of the twelve refusals
still refuses, including the spelled-out forms of the same traps.

## Sign-off

The implementing session does not sign this off, and does not merge.

**Independent review:** APPROVE WITH CONDITIONS — 1 Major, 3 Minor, 0 Blocking.
All four closed above, each with the test that pins it.

**Merged:** 2026-08-20, commit `a5565ab`, on Sahil's call. The reviewer's note:
the core went through the gate twice and the delta since was UI and location
parsing, a lower-risk surface than the identity, ingest and scoring work.
Anything a further pass finds is a follow-up, not a blocker.

Merging to `main` deploys.

## What landing it actually did

The merge alone did not arm anything, and that is worth writing down because it
surprised everyone including the reviewer. The launchd agent runs
`watch.py --interval 3` as **one long-lived process**. Python does not reload a
module already in memory, so merging changed the file on disk and the running
watcher — up since 12:09, three and a half hours before the merge — never saw
it. The scheduler and the Found lane's decision-draining both live in-process,
so both stayed dark. Publishing was unaffected, because that shells out.

The arming event is the daemon restart, not the merge.

Restarted 19:57:26 with nothing in flight. What followed, from the daemon's own
log rather than from anything anyone typed:

    20:00:01  tier1ScanStarted  pid 91984, nextAt 2026-08-20T23:56:41Z
    20:01:03  board published, three apply requests queued behind the scan lock
    20:17:14  board published again, mid-scan
    20:24:30  scan lock released after 24m29s
    20:24:41  five apply runners spawned, within 8s of the lock freeing

**The first automatic cycle:** 19,589 found, 121 kept, 11,856 merged, 7,610
filtered, 0 pruned, 14,292 stored, 767 requests, 1,469.7s, 8 errors. Merge
reasons were 6,001 canonical-url, 4,520 company-title-location and 1,335
fingerprint.

Six of the eight errors were `HTTP 503` from Adzuna across two countries, which
is a third-party bad hour rather than anything on this side — and it is also why
the run took 24.5 minutes against the 16.1-minute ceiling. 1.92 seconds per
request against 1.17 in the manual runs: the retry backoff dominating, which is
what a throttle-bound sweep looks like when a host misbehaves. The other two are
the Careerjet identity refusals of question 14, the same two ids as every run
before.

The interaction worth remembering: a scan holds the lock for its whole duration
and applies wait behind it. Five queued applications sat through 24 minutes and
then all five started at once. That is the design working, not a fault, but at
six sweeps a day it is a real window.
