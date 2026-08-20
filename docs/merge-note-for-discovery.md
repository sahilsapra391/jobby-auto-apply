# What changed on `main` under you, and why

Written for whoever merges `feature/discovery-engine-phase-1`. Everything below
landed on `main` on 2026-08-20. Four files overlap with that branch:
`store.py`, `dashboard.py`, `run_scan.py`, `SKILL.md`.

Run both suites after merging and report the combined count. Mine is 302 here.

## `scripts/store.py`

One change, in `cmd_session_save`, plus one new flag.

A **new watch entry** is now refused when the posting is already on record.
`seen.py` decides that from `roles.json`, the sessions (including dropped ones)
and the application history, matching on the application id, the URL with
tracking stripped, the ATS job id inside the URL, or the company and role
together. `--allow-known` overrides it, for deliberately restoring a role.

**Only new entries with status `watch`.** Updating an existing session, whatever
its status, is untouched, so `phase.py`, `finish.py --park` and `drop.py` behave
exactly as before.

This matters to the sourcing work directly: if the discovery engine writes watch
entries through `session-save`, a posting it has seen before will now be
refused, and the refusal message names where it was seen. Filter candidates
first with one call and save only what comes back:

    python3 scripts/seen.py --filter --input candidates.json

It also dedupes a sweep against itself, so one job found on three boards is kept
once. Matching is exact after normalisation, never fuzzy: "Android Maps" and
"Android Auto" stay separate, and so do "Product Manager" and "Senior Product
Manager".

## `scripts/dashboard.py`

Four changes, all in the board.

1. **Confirm submitted** on every Needs you card. It was there before, styled
   ghost and labelled "Mark submitted"; it is now green and says what it means.
2. **A queued card has a Stop.** The placeholder drawn client-side before the
   watcher marks anything had no controls at all.
3. **The remembered-Apply memory expires** after ten minutes and is stamped
   rather than a bare id list. The replay used to re-send `/api/apply` for any
   id that still had an Apply *or Retry* button, and a parked card has a Retry
   button, so every page load re-queued every parked application forever.
   Dropping now clears that memory too.
4. **The progress bar is five dots, not six.** "Documents ready" is no longer a
   phase any run reports.

If the discovery branch touches the Watching table or the card renderers, the
conflict will be in the button markup and in the client script near
`APPLY_KEY`.

## `scripts/run_scan.py`

Two changes.

1. **Step 1 no longer reads the watch list and role index.** Step 3b filters the
   whole candidate list in one call through `seen.py --filter`, and the store
   refuses a duplicate regardless.
2. **The interpreter and the allowlist.** The prompt was built with
   `sys.executable` while `SCAN_TOOLS` granted `Bash(python3:*)`, which is a
   prefix match on the literal command, so every python call the prompt asked
   for was refused. It now says `python3`, unquoted, and
   `toolpath.agent_env()` pins that name to this interpreter. `SCAN_TOOLS` also
   grants `ls`, `cat`, `find` and `head`, which give nothing `Read`, `Glob` and
   `Grep` did not already give.

If that branch rewrites the scan prompt, keep both: the filter call and the
bare `python3`.

## `SKILL.md`

Four sections added, no existing text removed except two corrections:

- **The working rhythm** — act, act, act, then review; `brief.py` and
  `finish.py` collapse the round trips at each end of a run.
- **Sourcing** — nothing already in the system gets sourced twice, with the
  `seen.py --filter` call.
- **Filling** — work-history description boxes are filled from that posting's
  own tailored resume via `experience.py`.
- **Tailoring** — the resume is built from `templates/resume.html` plus what
  changes; content gate first, layout gate second.

Corrections: the salary command takes single quotes (a `$70,000` band inside
double quotes is eaten by the shell), and the posting file is
`jd-<application-id>.txt`, one per application, because several roles at one
company share a folder.

## Two things worth knowing that are not conflicts

`ats_match.py` now loads the story bank by default. It only read it when handed
`--facts`, and across nine applications where the posting was still on disk, 8
of 9 reported unsourced terms without it and none did with it. Every one was a
false positive. If the discovery work calls `ats_match`, it no longer needs to
pass `--facts`, and `--no-facts` gives the old behaviour.

`publish.py` refuses to publish any store but the real one, with no `--root`
escape hatch. A board rendered from a redirected store still lands on the real
KV namespace, and `guard_render` cannot catch it because it compares the render
against the same redirected store.
