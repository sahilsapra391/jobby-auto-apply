# Deploying the production checkout, and checking it landed

**A pull without the restarts leaves the machine running two commits at once,
and nothing on it will say so.** That happened four times between 2026-09-01
17:16 and 2026-09-02 18:09 and went unnoticed for 35 hours. Every check anyone
ran passed, because the checks asked whether the checkout was current and a
long-lived Python process holds whatever it started on.

`scripts/deploy_verify.py` is the check that asks the other question. Run it
after the restarts, read its exit status, and stop if it is not zero.

## The sequence

```bash
# 0. take the lock. It refuses and names the holder if somebody else has it.
python3 scripts/deploy_lock.py take --owner <you> --note "<from> -> <to>"

# 1. gate: no form run, no document job, nothing inflight
python3 scripts/watch.py --status        # every lock null or "parked": true
ls ~/.job-application/document-tasks/    # empty
ps aux | grep -E "run_apply|document_worker|browser_mcp_proxy" | grep -v grep

# 2. announce it where the other sessions read, and say the slots are going

# 3. pull, staying on main
git -C ~/Documents/Development.nosync/jobby-auto-apply pull --ff-only origin main

# 4. restart, dependencies before the orchestrator
launchctl kickstart -k gui/$UID/ai.jobby.documents
launchctl kickstart -k gui/$UID/ai.jobby.providers
launchctl kickstart -k gui/$UID/ai.jobby.watch

# 5. check the after-state rather than assume it
python3 scripts/deploy_verify.py         # exit 0, or read what it says

# 6. release
python3 scripts/deploy_lock.py release --owner <you>
```

Step 1 asks a program rather than globbing a directory, and that is not
fussiness. A session once ran `ls ~/.job-application/locks/apply-*.json`, got
"no matches found" because the files are `.lock`, and restarted over a live form
run that had been claimed 88 seconds earlier. **A safety check whose failure
mode is indistinguishable from success is not a safety check.**

## What `deploy_verify` asks

Four things, and the third is the one nothing else asks.

1. The checkout is on `main` and level with `origin/main`.
2. Every service is running and started after the last pull.
3. **For each service, no file in its own resident import closure has an mtime
   newer than that service's start time.** Per file, per service, named.
4. No reflog entry fell between the pull and the restarts.

The fourth exists because checking "the start times postdate the entry I
expected" passes for two sessions separately while production ends on two
commits at once, which is what happened on 2026-08-28.

**It fails when it cannot look.** An unreadable start time and a fresh start
time are not the same claim, and an unreadable reflog is not "nothing moved".
Both are failures rather than passes, because a check that can only fail in one
direction is half a check.

## What it prints when the deploy worked

Exit 0, one line per service, no `STALE`, no `reflog` line, and the last line:

```
checkout  <the production checkout>
          branch main, HEAD <new>, origin/main <new>, behind 0
ai.jobby.watch       pid <n>   started <after the pull>
ai.jobby.documents   pid <n>   started <after the pull>
ai.jobby.providers   pid <n>   started <after the pull>

NOTE      the import closure is static: a dynamic import or a module reached
          through a string is invisible to it, so a clean run is a floor and
          not a proof

OK        every service postdates the pull and holds no module newer than itself
```

That NOTE is not decoration. The closure is parsed from `import` statements, so
a module reached through `importlib` or a `subprocess` built from a string is
invisible to it. A clean run means nothing statically reachable is stale; it
does not mean nothing is.

## What it prints when a service silently did not restart

This is the important one, and it does not need imagining, because production
is in exactly that state as this is written. Run on 2026-09-03, exit 1:

```
checkout  <the production checkout>
          branch main, HEAD 633b421, origin/main f37dd7c, behind 8
ai.jobby.watch       pid 92892   started 2026-09-01T20:49:57Z
                       STALE         scripts/class_b.py     modified 2026-09-02T22:09:56Z
                       STALE         scripts/dual_write.py  modified 2026-09-01T21:16:02Z
                       ...
ai.jobby.documents   pid 88611   started 2026-09-01T20:39:43Z
                       STALE         scripts/class_b.py     modified 2026-09-02T22:09:56Z
                       STALE         scripts/dual_write.py  modified 2026-09-01T21:16:02Z
                       ...
ai.jobby.providers   pid 88615   started 2026-09-01T20:39:43Z
                       STALE         scripts/settings_panel.py modified 2026-09-02T22:09:56Z
reflog    2026-09-02T22:09:56Z  633b421  pull --ff-only origin main: Fast-forward
reflog    2026-09-02T00:51:26Z  1a87673  pull --ff-only origin main: Fast-forward
...

FAIL      the checkout is 8 commits behind origin/main
FAIL      ai.jobby.watch started 2026-09-01T20:49:57Z and imports 5 module(s)
          modified since: class_b, dual_write, parity_clock, settings_panel,
          write_path_coverage
FAIL      ai.jobby.documents started 2026-09-01T20:39:43Z and imports 4 ...
FAIL      ai.jobby.providers started 2026-09-01T20:39:43Z and imports 1 ...
FAIL      HEAD moved to 633b421 at 2026-09-02T22:09:56Z, after a service had
          already started: pull --ff-only origin main: Fast-forward
```

Three things to read out of that shape.

**The failure is per service and per file**, so "restart everything again" is
never the only available response. If only `ai.jobby.documents` is listed, only
that one missed its `kickstart`.

**`dual_write.py` and `class_b.py` appearing under two services is the
mirror**, and it is why this state matters rather than being untidy.
`watch -> jobs_store -> dual_write` and
`document_worker -> document_contract -> store -> class_b` are both resident, so
those daemons hold the pre-fix copies while every spawned `ingest.py` loads the
current ones off disk.

**`INDETERMINATE` is not a pass.** `ps -o lstart` prints whole seconds, so a
file written in the same second a process started cannot be ordered against it.
It is reported as a failure naming which case it is, because a check that
cannot see is not a check that saw nothing. A real deploy restarts seconds after
the pull and never lands there.

## What it is not

It does not tell you the deploy was safe to take; `watch.py --status` before the
pull is that. It does not know about work running from a worktree against
production's browsers, which holds no production lock. And it says nothing about
whether the code is correct, only about which copy is loaded.
