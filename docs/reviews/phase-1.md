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

## Sign-off

The implementing session does not sign this off, and does not merge.

**Independent review:** APPROVE WITH CONDITIONS — 1 Major, 3 Minor, 0 Blocking.
All four closed above, each with the test that pins it.

**Sahil:** ______________________  date: ____________

Merging to `main` deploys.
