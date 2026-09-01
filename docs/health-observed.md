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

## Never observed here

| guard | why not, as far as can be told |
|---|---|
| `chrome_flag_warning` | needs a slot missing `--use-mock-keychain` that also could not be recycled. Every slot has relaunched since #111. |
| `chrome_drift_message`, version half | needs Chrome to update under a running agent browser |
| `browser.open_application` failure | needs a dead Chrome behind a passing preflight |
| `parkClass: capability` | needs a park with zero browser calls. 0 of 96 sessions carry the tag. |
| `stale_sweep_warning` | added 2026-09-01. Its first firing would be an 8-hour sweep outage. |

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
