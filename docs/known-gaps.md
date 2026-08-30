# Known gaps, parked deliberately

Things found while working on something else, written down rather than fixed on
the spot. Each has enough detail to act on without rediscovering it.

## CI cannot run 22 of the tests, and they are the ones with teeth

`checks.yml` runs on pull requests and on push to main, and main is green. But
22 tests skip on a runner because they need the real corpus, and they are
disproportionately the ones a fixture cannot replace:

- `test_no_pii`: the tracked-file scan, and its own "the scan has something to
  look for" guard. It reads `~/.job-application/profile.json` to learn what the
  personal data *is*, then searches tracked files for it. A runner has no
  profile, so there is nothing to search for and the scan is structurally
  incapable of firing there.
- `test_verify_parity`: the live merged-field-drop check.
- Resume rendering: eight tests needing the real base resume and a local Chrome.
- `test_the_real_state_fits_under_the_publish_ceiling`.
- `test_every_resume_ever_written_round_trips_exactly`.
- `NeonMatchesThePin.test_its_major_is_the_pinned_one`.

**Measured 2026-08-30**: a personal email sat in `tests/test_browsers_stay_
signed_in.py` from 2026-08-29 and CI passed every run in between. It surfaced
only when a full local suite ran and blocked a merge.

CLAUDE.md already says "if seven ever becomes eight, notice" about the skips on
a redirected local run. Nobody wrote the equivalent for the CI side, and this is
it: **22 is the number, and a rise in it means CI just stopped checking
something.** Worth a test asserting the count, the way the local one is.

## test_deploy_lock is flaky on CI

`test_take_release_round_trip` failed on CI on 2026-08-30 with `2 != 0` -- an
exit code of 2 from `deploy_lock take`, which means refused-because-held -- and
passed on a re-run with no change. It passes locally on the branch, under a
runner-like `HOME`, and on main.

A gate that fails at random is the same family as one that passes while
measuring nothing: the signal means nothing in either direction. Not diagnosed.
Suspect test ordering, since a new test module landed in the same run and
`unittest discover` orders alphabetically.

## applications.jsonl is appended, so it misses every write_private guard

`store.cmd_history_append` opens the file and appends a line rather than going
through `store.write_private`. Everything hung off that function -- the class B
mirror, the write-path coverage hook, and now the foreign-store refusal -- has
to be repeated by hand for history events, and was, twice, after the omission
caused a leak.

Five of the seven rows that reached production on 2026-08-30 came through this
path. It is guarded now, but the shape stays: **a second way to reach disk is a
second place every future guard must remember to go.** Worth folding into
`write_private` with an append mode, which is a bigger change than that day
allowed.
