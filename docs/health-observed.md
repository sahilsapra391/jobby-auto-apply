# Which guards have actually been seen firing on this Mac

**A guard that has never fired is not broken. It is unverified**, which is a
different claim and the honest one. This is the list, audited 2026-09-01 by
grepping every log under `~/.job-application/logs/` for the message each guard
actually emits, in the file it actually writes to.

Rebuild it the same way. It goes stale the moment a guard's wording changes.

## Observed firing here

| guard | evidence |
|---|---|
| `parked_slot_warning` | 286 lines in `watch.log` |
| `ping_health("fail", …)` | 223 `UNHEALTHY` lines in `watch.err` |
| `guard_state_size` | 80 lines, the 20.87 MB episode |
| `stale_board_warning` | 9 lines in `watch.err` |
| `guard_render` | 2 lines, "read the store mid-write" |
| `mcp_preflight` refusal | 2 lines in an apply run log |
| firewall activity counter | real `browserCalls` in apply logs: 10, 28, 59, 62 |
| `close_missing` share brake | 22 firings in `ingest-runs.log`, read 2026-09-03 -- see below |

## Never observed here

| guard | why not, as far as can be told |
|---|---|
| `chrome_flag_warning` | needs a slot missing `--use-mock-keychain` that also could not be recycled. Every slot has relaunched since #111. |
| `chrome_drift_message`, version half | needs Chrome to update under a running agent browser |
| `browser.open_application` failure | needs a dead Chrome behind a passing preflight |
| `parkClass: capability` | needs a park with zero browser calls. 0 of 96 sessions carry the tag. |
| `stale_sweep_warning` | added 2026-09-01. Its first firing would be an 8-hour sweep outage. |

## The share brake fired 22 times and nothing read it

`closureRefused` is the one guard here whose *record* was complete and whose
*reader* did not exist. Over 75 sweeps from 2026-08-20 to 2026-09-03 it fired
22 times, every one of them `rippling / astra`, held 33 and wouldClose 32, on
consecutive sweeps from 2026-08-25T23:31Z to 2026-08-29T16:59Z.

It was right. All 34 Astra records are `new` today, last seen 2026-09-03T04:11Z:
the board returned 1 of 33 for four days and came back whole, and without the
brake 32 live postings would have closed on the first of those sweeps.

**Three ways it is still only half a guard**, which is why it sits here with the
detail rather than as a bare row. It has no memory, so the twenty-second refusal
reads exactly like the first and a genuinely emptied board would be refused
forever. Nothing reads the field -- `grep` finds one writer in `ingest.py` and
no reader anywhere. And it is per-board, so the largest closure on record,
1,860 records on 2026-08-30T01:02Z, tripped it zero times.

Recorded properly in `docs/plans/phase3-design.md` section 3, because it is a
Phase 3 design input as well as a health observation.

## Two readings in the first pass were wrong, both in the reassuring direction

**`activity.json` is absent from the disk by design.** `browser.activity_path`
puts it beside the staged uploads specifically so it is removed with them and
never outlives the run it describes. Finding zero of them says nothing about
the counter. Its output is in the run log, and there it is: `browserCalls` of
10, 28, 59 and 62, alongside 6 nulls from before the signal-handler fix.

**`attempts[]` is not a session field.** It is built in `run_apply` and printed
into the run's own log. Counting sessions with `attempts` returns 0 on a
healthy machine and reads as "the counter never ran".

Both were the instrument pointed at the wrong place, which is the same defect
as the guards being audited. That is why each row above names the file the
evidence was found in rather than just a verdict.

## What this list is for

It is the standing answer to "has this ever worked here". Before trusting a
guard in an incident, check whether it is in the top table or the bottom one. A
guard in the bottom table may be perfect; nobody knows.
