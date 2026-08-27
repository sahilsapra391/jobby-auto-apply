# Silent staleness: a standing plan

The system reports healthy while silently doing nothing. It has now happened
three times in different clothes, and each time the individual bug was fixed
and the shape was not.

| # | What happened | What looked fine |
|---|---|---|
| 1 | The watcher ran pre-merge code for hours | A healthy process, ticking normally |
| 2 | `deploy/renderer/` served a stale bundle after a merge | Green tests, merged commits, old board |
| 3 | Publishing refused for hours on a 21 MB state against a 20 MB reader | Healthy ticks, a day-old board, every click invisible |

Case 3 is the one that prompted this. It was a stale measurement: posting
sections were sized at 265 bytes a record on a backfill covering a third of the
store from 1,500-character excerpts, and after the full-body backfill they were
9.38 MB of a 21.03 MB state. The number was true when taken and meaningless by
the time it mattered. Fixed in #78 by cutting the published copy to the five
points at 200 characters a card draws.

Fixing that number is not the point. The shape is: **a guard that refuses
correctly, and tells only a log, is indistinguishable from a system that is
working.**

## What is already here

Guard 1 as originally conceived is largely built, and the audit below is more
useful than rebuilding it. Recording what exists so the next reader does not
re-derive it:

- `watch.board_staleness()` measures seconds since the last **successful**
  publish, from `last-publish` (written by `publish.mark_published()` only after
  the KV write returns) and `last-deploy` (written by the watcher only after
  `publish()` returns success). Both are outcome markers, not attempt markers.
- `watch.stale_board_warning()` produces the operator-facing message past
  `PUBLISH_STALE_SECONDS`, which is `2 * PUBLISH_FLOOR_SECONDS` — exactly twice
  the publish cadence.
- The tick loop fails the Healthchecks dead-man ping on that warning rather
  than pinging healthy.
- `HEALTHCHECKS_URL` is configured on this machine.

**And it did fire during case 3** — as `ping_health("fail", ...)` from the
generic exception branch, roughly every eighteen minutes for hours. The alarm
was not missing. What was missing is that nothing a person actually looks at
said anything, so it rang for hours into a channel nobody was watching.

Grep of `watch.err` over the outage: `STALE BOARD` appears zero times. Every
one of those pings went out as `tick N: publish failed`, because the tick raised
before the staleness branch was reached. The alarm fired with the wrong message
for hours, which is why nobody reading it learned the board was a day old.

That reframes the work: the gap is guards 3 and 4, not guard 1.

## The four guards

### 1. A stale board is loud, at the source — MOSTLY DONE

Built, as above. Two holes remain, both small:

- **The stale branch is unreachable when the tick also raises.** The loop
  computes `stale_board_warning()` after `tick()` returns. In case 3 `tick()`
  raised `TickError` every time, so the ping said `tick N: publish failed`
  rather than the staleness message written for exactly this. The generic
  message is less actionable and does not say how long the board has been
  stale. Check staleness in the failure branch too.
- **The ping's own outcome is discarded.** `ping_health` returns a status and
  every caller ignores it; on any network error it returns `None` silently. A
  misconfigured or unreachable `HEALTHCHECKS_URL` would look identical to a
  healthy system from inside. Record the last ping result and surface it in
  `watch.status()`.

### 2. Every capacity guard is paired with a test that measures the real slice

The rule, stated once: **every hard limit the running system can hit has a test
that constructs the same artifact the guard measures, with the same serializer,
and fails when the limit is approached.**

`PageWeightAtCapTests` guarded the rendered HTML at 4 MB and had for a long
time. Nothing guarded the published state at 20 MB, which is how a change took
the board offline with every test green.

| Limit | Where enforced | Test that pins it |
|---|---|---|
| Rendered page ≤ 4 MB (budget 3.7) | `found_lane.EMBED_CAP` | `PageWeightAtCapTests::test_the_page_stays_under_the_target_at_the_cap` |
| Published state ≤ 20 MB | `publish.MAX_STATE_BYTES` | `PageWeightAtCapTests::test_the_published_state_stays_under_the_readers_ceiling` (added with #78) |
| Published sections ≤ 5 × 200 | `dashboard._trim_sections` | `PageWeightAtCapTests::test_published_sections_are_cut_to_what_the_card_draws` |
| Cloudflare KV value ≤ 25 MB | `publish.MAX_STATE_BYTES` sits under it | covered transitively by the row above |
| Logo response ≤ 750 KB | `deploy/api/board.js` | `board_provider_api.test.mjs` |
| R2 object size / manifest | `scripts/r2_sync.py` | **UNPINNED — next bug** |
| Sweep duration vs lock window | `scripts/runlock.py` | **UNPINNED — next bug** |
| Document payload bound | `scripts/document_job.py` | **UNPINNED — next bug** |

The three unpinned rows are the actionable output of this section. Each is a
limit the running system can reach with no test that constructs the artifact.

**And one more thing to enumerate the same way: every subprocess this system
spawns, and where its stderr goes.** Two instances make it a pattern rather than
a coincidence, and both are the component whose job is noticing being the one
that says nothing.

`ping_health` returned None on any network error and a status every caller
ignored, so an unreachable or misconfigured Healthchecks URL was invisible from
inside the watcher. `browser_mcp_proxy` passed `stderr=subprocess.DEVNULL` to the
Playwright child, so when the backend died during initialize the client saw
"connection closed: initialize response" and the reason was destroyed in the
same instant it was produced. That second one is why a browser fault that broke
a real run on 2026-08-27 answered every hand probe afterwards and was never
reproduced: the only occurrence that could have explained itself had already
thrown the explanation away.

Both are fixed. The audit is the open work: every `Popen` and `run` in
`scripts/`, and whether a failure of that child can be read after the fact.
A discarded stderr is not a small thing, it is a guarantee that the next
occurrence is as unreproducible as the last one.

**A guard can also stop guarding without failing.** `test_it_really_does_fail_
when_the_slice_is_over_the_ceiling` exists to prove the ceiling bites: it
disables slimming, builds the slice, and asserts it exceeds 20 MB. `_trim_
sections` was added to the same path afterwards and the test disabled only the
older trim, so the "unslimmed" slice came in at 20.14 MB against a 20.97 MB
ceiling and the proof stopped proving. Here it failed loudly, which is the good
case. A trim that had shaved slightly less would have left it green and
meaningless. Where a test neutralises machinery in order to prove something,
it has to neutralise all of it, and adding to that machinery means revisiting
the test.

That is not the whole of it, and the rest arrived while this was being written.
#81 and #84 found a third direction: the test built its slice from
`found_lane.EMBED_CAP`, which somebody else owns and which dropped to 3,350 on
2026-08-26 to make room for the Profile page. With both trims off but the cap
read live, the "untrimmed" slice measures 20,790,251, still 181,269 under the
ceiling, so the guard would have gone quiet again for a reason having nothing to
do with trimming. The cap is pinned in the test now. The general form is worse
than the first one: a proof whose premise is a constant somebody else maintains
will be broken by people who never read it, and the same guard has now been
taken down from three directions by three changes that each had nothing to do
with it.

### 3. Capacity has headroom alarms, not just cliffs

A guard that refuses at 100% is correct and too late: by then the board is
already frozen. Every capacity limit gets a warning threshold at 80% that
surfaces where a person looks.

Published state moved three times on 2026-08-27 and nobody watched it move:
**105%** of the reader ceiling in the morning, when the board froze; **48%**
after #79 and #80 landed on top of #78; **13%** that evening, 2,755 KB of 20 MB,
after #86 replaced the emergency trim with a real one. Three sessions each
changed it, each learned their own delta, and each inferred the rest. That is
the whole argument: the number is only useful if something records it over time.
A figure that exists only in the stdout of whoever last ran publish is not
monitored, it is witnessed.

So the headroom read belongs in the **ping payload**, hourly, where Healthchecks
keeps the history — not only on a status line that is true whenever somebody
happens to look at it. A status line is worth having and is another session's to
add; it is not where a trend lives.

A caution for whoever writes it: an earlier draft of this plan quoted 79% from a
measurement taken before #79 and #80 landed on top of #78. The number was stale
within the hour. Re-measure at the point of writing, and prefer emitting the
measurement to quoting it.

**And it must not know which store it is measuring.** The board state is moving
out of one KV value into an R2 object behind a pointer, after which the KV value
is a few hundred bytes and the ceiling that matters is R2's, where this number
stops being interesting at all. An alarm written against KV specifically would
be orphaned by that change without failing — the fifth instance of a guard whose
premise is a constant somebody else owns, after `_trim_sections`,
`found_lane.EMBED_CAP`, and two sessions repairing the same control in parallel.

So the publish path owns one function that answers *which store holds the state,
what its limit is, and what it currently weighs*, and the alarm reports the
ratio without naming a store. Agreed with the session that owns board state on
2026-08-27, and shipping as part of that move rather than as a follow-up, so
there is one place that knows rather than two that agree until they do not.

### 4. A fail-closed guard is never silent to the operator

Fail-closed is correct. Fail-silent is the bug. Any guard that refuses to act in
order to protect the system writes a visible, named reason where a human looks,
not only into a log that scrolls.

Audit of the existing fail-closed paths:

| Path | Refuses when | Visible to the operator? |
|---|---|---|
| `publish.py` state ceiling | state > 20 MB | Log + failed ping. **Not on the board.** Case 3. |
| `watch.publishing_paused` | Vercel deploy quota spent | Log only. **Gap.** |
| `providers.supports` | provider not fence-proven for a task | Refusal reaches the runner; **not surfaced in Settings.** |
| `resume_build.assert_integrity` | rewritten résumé fails claim checks | Surfaces on the card as a blocker. Good. |
| `finish.py` submission nonce | direct completion, reuse, cross-application | Surfaces as a blocked application. Good. |
| Browser MCP firewall | navigation, shell, storage tools | Refusal is the point; agent-visible. Acceptable. |
| Apply queue head cannot start | no slot available | Card reads "waiting for an earlier application". Good, and it was misleading until #76 — the reason was true and the cause was a deadlock. |

Three gaps: the state ceiling and the deploy-quota pause are log-only, and a
capability refusal is invisible in the one screen built to show provider state.

### 5. The template for anything that can only fully verify against live infrastructure

**A real-integration test that skips in CI, paired with a contract test that
runs in CI. Neither alone is enough and the pairing is cheap.**

This came out of the apply-path repair, where both faults were invisible to
1,838 passing tests because every test stopped at a seam: the firewall was
tested against a stub, the provider invocation against a list of strings, and
nothing ever asked a browser to take a file. Two behaviours in the fix were
findable only against a real form. That a page with several file inputs must be
refused rather than guessed at, because taking the first is how a cover letter
lands in the resume field. And that the input has to be described before the
attach, because React swaps the field out on accept, which made a working upload
report a 30-second timeout.

| half | example | runs where |
|---|---|---|
| real integration | `test_apply_end_to_end` drives the real firewall against a real Chrome and attaches real files | laptop; **skips** without a browser, excluded from CI |
| contract | `test_apply_capability_contract` compares the two halves of one contract, checks each provider's argv, checks the tag survives the publish filter | everywhere |

Rules that make the pairing work rather than decorate it. The integration half
**skips** rather than fails where the infrastructure is absent, or people learn
to ignore a red suite. The contract half must fail when the fix is reverted, and
that is checked by reverting it, not assumed. And the integration half is where
the fixture comes from: reproduce the real shape in `tests/fixtures/` once you
have seen it, so the next person inherits the finding.

Existing pairs to hold to this: `test_r2_contract` against the live bucket,
`LogoContractTests` against `/api/company-logo`, `test_resume_pdf` against real
Chrome. Each has the integration half; not all have the contract half.

## A status goes stale the same way a board does, and carries no timestamp

**"I am blocked on X" outlives X. Nobody re-derives a status, and unlike a
board it has no age on it to notice.**

On 2026-08-27 the session that owns board state had been reporting its Step 1 as
queued behind three named prerequisites. All three had landed. It found out
because checking somebody else's claim made it check its own, and it had twice
described the blocking list from memory and been wrong in the same direction.

This is the same failure as everything else in this document, moved one level
out of the system and into the reporting about it. It is not wrong in any
visible way. It is old, and everyone downstream treats it as current. The board
at least carries a timestamp, so `board_staleness` can measure it and the tick
can fail a ping on it. A status in a report carries nothing, so there is no age
to measure and no alarm to write.

There is no mechanism to propose here, which is why it is a section rather than
a guard. What it changes is a habit: a blocker is re-derived when it is cited,
not when it is discovered, and a status repeated from memory is a claim rather
than a measurement, subject to the same rule as every other claim in this
document. The three corrections that mattered most in that window each came from
someone checking a claim about their **own** area rather than accepting a peer's,
and in two of the three the check landed on something other than what it was
aimed at.

## The coordination problem wearing a test's clothes

`test_it_really_does_fail_when_the_slice_is_over_the_ceiling` has now been
disarmed from four directions by four sessions: `_trim_sections` added to the
path it neutralises, `EMBED_CAP` moved under its premise, and two sessions
fixing it in two branches while a third read it. None of those changes was
wrong. None of their authors had read the guard.

That is not a test problem and no further local patch fixes it. The standing fix
is the one-integrator rule: one session lands to `main`, in an order it decides,
and the others hold.

The same absence produced a second defect that looked nothing like it. #78 added
a published-section trim as the emergency fix for the 21 MB freeze, cutting 16%
of bullets mid-sentence — the right call against a board that had served nothing
for 22 hours. #79 landed an hour later with slimming of its own, and the two
stacked, and the tighter one won **by merge order rather than by anyone
deciding**. It stayed that way for a day, until #86 replaced it with one
trimmer. Nobody chose the outcome, nobody was wrong, and it took a measurement
on the live board to notice: 166 bullets, longest exactly 200, none over.

An outcome nobody chose is the signature of the missing integrator, and it does
not always arrive as a conflict. Sometimes it arrives as two correct changes
composing into a third thing neither author would have shipped. A guard that several branches repair in parallel is a guard
whose repairs conflict, and this one spent a day red on `main` for exactly that
reason while three sessions each believed they had fixed it.

## Scope

Guard 1's two holes and the guard 4 audit are cheap and belong in the first PR
alongside this document. Guards 2 and 3 are mine, confirmed with the board-state
session rather than assumed: they declined, having finished and being held for
the deploy window. Guard 5 is a template rather than work, and the pair it
describes already exists.

This is a plan and a small first PR, not a sprint.
