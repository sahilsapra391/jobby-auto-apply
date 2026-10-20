# Jobby: architecture and contributor guide

This file is what an agent or a person needs before changing Jobby. It covers
the shape of the system, the rules that have to hold, and how to test a change.
A local, untracked `CLAUDE.local.md` may sit beside it with notes specific to
one person's machine; it is gitignored and never published.

Read the code before trusting a sentence here. A mechanism claim in a document
describes the code as it was when the sentence was written, and the code is the
only authority on what it does now. Where this file names a function, open it.

## What runs where

Jobby has two halves.

**The device** is the user's Mac. Every piece of agent work runs there:
resume tailoring, cover-letter writing, sourcing scans, form filling,
submission and confirmation checking. It is a set of Python scripts under
`scripts/`, started by three launchd services that
`python3 scripts/watch.py --install` writes:

    ai.jobby.watch       the queue watcher and the form orchestrator
    ai.jobby.documents   serial local document generation
    ai.jobby.providers   the Settings control lane, polled every two seconds

The provider lane is separate so a slow provider or application probe can never
delay a Connect or Disconnect click. Do not merge it back into the watcher.
`packages/runner/` is the planned single supervised process that replaces all
three; until it lands, these three are production.

The device's private store is `~/.job-application/` (overridable with
`JOB_APPLICATION_DIR`): profile, answer bank, sessions, the job pool, provider
registry and secrets, browser profiles, logs and `config.env`. Nothing in it
belongs in the repository.

**The board** is `apps/web`, a Next.js app on Vercel that reads Postgres
(`JOBBY_DATABASE_URL`) and serves the Found, Watching, In flight, Needs you and
Submitted lanes plus Profile and Settings. Clicks on the board are written as
rows and queue entries; the device claims them and does the work. Generated
documents live in a private R2 bucket and are proxied through the app after its
own checks. `apps/worker` holds the Cloudflare Worker and the per-account
`TenantChannel` Durable Object that carries live updates.

The device and the board meet in two places: Postgres, which the device's
class A and class B mirrors write as its store changes, and a Cloudflare KV
namespace holding the click queue (`deploy/kvqueue.mjs`) and the few status
keys the device publishes (`deploy/device_keys.mjs`). The device reaches KV
through the small node scripts in `deploy/`, which it runs by absolute path.

Sourcing sweeps can also run in a container (`deploy/sourcing/`) that fetches
from connectors and stages results for the device to merge. The merge, the
closure sweep and the prune stay on the device, where the pool is.

## Repository layout

    apps/web/            the board (Next.js, TypeScript, Postgres)
    apps/worker/         Cloudflare Worker and the TenantChannel Durable Object
    apps/scanner/        placeholder for the sourcing container's own app
    scripts/             the device: watcher, document worker, form runner,
                         firewall, store, sourcing connectors (scripts/sources/)
    packages/schema/     Postgres migrations, the migration runner, feature flags
    packages/runner/     placeholder for the single device process
    deploy/              device helpers (KV queue, document jobs, status keys,
                         R2 signer), the sourcing image, the OCI recovery path
    templates/           resume and cover-letter templates, fictional base content
    references/          reference data the scripts read (questionnaire taxonomy,
                         model prices, source registry, probed tenants)
    docs/                architecture decisions (docs/adr/), migration policy,
                         write-path inventory, the v2 rebuild plan
    tests/               Python unittest and node:test suites

## Providers

OpenAI, Anthropic and Gemini share one local registry at
`~/.job-application/providers.json` (`scripts/providers.py`). New work probes
connected accounts in the user's saved order and runs on the first one that can
invoke its pinned model. A failed login, usage or model check moves new work to
the next healthy connection. A running document job keeps its provider; the
form stage and a retry may hand off. Never choose or connect a provider on the
user's behalf.

Every agent task pins an exact model, set in `scripts/providers.py`. Gemini
must report the served model through its telemetry record, and a remap or a
missing record fails closed.

OpenAI credentials stay in Codex (`codex login`). Anthropic and Gemini keys live
in owner-only files under `~/.job-application/provider-secrets/` and enter only
the matching provider child's environment. Disconnect deletes the key. Never
log a key or copy one into the registry, a queue, Vercel, Cloudflare or a test.

Provider processes get an allowlisted environment, bounded output, a fixed task
contract and no inherited model default.

## Documents

The document worker (`scripts/document_worker.py`) receives an encrypted job
bundle carrying everything it needs: the profile, the base resume wording and
the posting. It reads no fallback from the repository. A missing input fails
the job by name, because a silent substitution is indistinguishable from
working. The bundle schema is versioned; bump it when the shape changes, and
check that no job is queued or claimed under the old schema before deploying.

The base wording lives in `~/.job-application/resume-content.json` once the
user has one. `templates/resume-content.json` is a fictional example that only
seeds a fresh install. The fixed lines of a resume (employers, dates, titles,
education) are the content's `frame` object and fill the `{{frame_*}}` slots in
`templates/resume.html`; a provider can never override them.

The gates live in trusted Python, behind every provider, and must never be
moved into a prompt: a prompt is advice to one model, the pipeline is the
contract for all of them.

- `resume_build.assert_integrity()` runs between the override merge and the
  render. It refuses duplicate and near-duplicate bullets, changed metrics or
  versions, low source-token retention, changed bullet counts and oversized
  rewrites.
- `cover_letter.validate_content()` strips a leading greeting the template
  already supplies and a trailing sign-off above the template's own signature,
  and refuses doubled words, near-duplicate paragraphs and a body paragraph
  that opens with a greeting.
- Rendering, PDF output, ATS checks and page geometry all run locally after the
  provider output has passed schema and claim checks.

## The form-filling firewall

Form agents never see a raw browser. `scripts/browser_mcp_proxy.py` exposes a
small static Playwright tool set and removes arbitrary navigation, JavaScript,
shell, storage, routing and network-log tools. It blocks Enter-based submission
and binds uploads to the two staged PDFs by path, inode, size and SHA-256.

The trusted runner opens the application URL (`browser.open_application`,
called from `run_apply.py` before the provider starts). The agent has no
navigation tool, so without that call it would look at a blank tab.

Every advertised `inputSchema` must be an object schema; a top-level `oneOf`
makes a client drop the whole server's tool list. Responses to
`server/discover`, `tools/list` and `tools/call` carry the stateless completion
markers `application_mcp.py` sends, because some clients never send
`initialize`. Either fault leaves the server "connected" with no tools.

`browser_attach_file` attaches a document without a file chooser. Its code is
the fixed `ATTACH_FILE_CODE` and is never assembled from agent text; the agent
picks a target and one of its own two bound PDFs. With several file inputs and
no target it refuses rather than guessing.

The proxy writes its call counter (`activity.json`) on SIGTERM, SIGINT and
SIGHUP as well as on exit, and keeps the Playwright child's stderr. A park with
zero browser calls is classified as a capability fault rather than a question
for the user.

## Submission

Submission uses a one-use nonce (`scripts/finish.py`,
`scripts/application_mcp.py`). `pre_submit` writes the `submitting` event and
returns a nonce. `record_completed` must present that nonce for the same
application while the latest event is still `submitting`, along with visible
confirmation text. Direct completion, reuse and cross-application use fail. A
second `pre_submit` while one is unresolved is refused. Nonces begin with
`jobby_` so one can never start with `-` and be read as a command-line option.

A fill-only run (used when a person finishes the form by hand) is never given
`pre_submit` or `record_completed` at all. An absent tool is a fact for every
model; a prompt asking a model not to submit is not.

## Answers

`scripts/store.py` resolves form questions against the user's answer bank.
Category matching uses word boundaries, never `marker in text`: "age" is inside
"average" and "race" is inside "embrace". Measure a classifier against a real
corpus before trusting it.

Work-authorisation and sponsorship answers come from
`~/.job-application/work-authorization.json`, never from code. With no file, or
a file missing any cell, those questions go to the user. A wrong answer there
is a false legal statement.

`scripts/answer_record.py` records what each form asked and what went into it.
It withholds one-time codes, passwords and national or bank identifiers, keeps
the question, and never back-fills an answer it did not observe. A record with
`capture: questions-only` has no answers by design.

## Rules for working in this repository

**Worktrees, always.** The launchd services run the scripts by absolute path
from one checkout. That checkout only ever has `main` checked out, and all
agent work happens in worktrees. A branch checked out there is a deployment,
and applications submitted from it go out under a real name.

**A pull of the production checkout is a deploy.** A long-lived daemon keeps
the modules it imported; anything it spawns loads from disk. So a pull without
a restart leaves the system running two commits at once. The sequence:

```
0. run the build, after the last edit, against the tree being deployed
1. take the deploy lock, and stop if somebody already holds it
2. announce it, and say which services are going down
3. pull the production checkout, staying on main
4. restart all three services, dependencies before the orchestrator
5. verify no reflog entry fell between the pull and the restarts
6. verify the web half deployed, by commit SHA and not by the dashboard
7. release the deploy lock
```

```bash
python3 scripts/deploy_verify.py --build
python3 scripts/deploy_lock.py take --owner <session> --note "<from> -> <to>"
python3 scripts/watch.py --status
```

Before restarting, read `locks` in `watch.py --status`: `null` is free, a lock
with `"parked": true` is safe to restart over, and anything else is a browser
in the middle of an application.

**Tests never touch the real store or the real database.** A test module that
drives store-writing code calls `store_isolation.begin()` in `setUpModule`.
`migrate.device_url_refusal()` refuses to fall through to the device database
from inside a test runner; tests use `JOBBY_TEST_DATABASE_URL`. A store path is
never a default argument, because Python binds defaults at import.

**New files are declared.** `.githooks/pre-commit` refuses to add a file the
commit did not name, and refuses images outright outside the brand directories.
Bind it once per clone with `python3 scripts/install_hooks.py`, then:

```bash
JOBBY_ADD="scripts/thing.py tests/test_thing.py" git commit -m "..."
```

**Diagnostics are never discarded.** A subprocess's stderr is captured or
inherited into a service log, never sent to `/dev/null`;
`tests/test_diagnostics_are_kept.py` walks every launch. A check that cannot
read its subject says so in its own words instead of returning an empty answer.

**Verify by observation.** A guard, alarm or counter is unverified until it has
been seen firing. Before believing a new check, break the thing it guards and
watch it fail. A green suite says the code is consistent with itself; running
the pipeline says it works.

**Generated files are generated.** The `apps/web/app/*.generated.ts` and
`apps/web/app/_ui/*.generated.mjs` files come from `scripts/sync_v2_styles.py`,
which copies a value the device and the board share out of its one source
(`v2_lanes.py`, `company_logos.py`, `answer_categories.py`,
`provider_status.py`, `deploy/*.mjs`). Edit the source and re-run the script.
`board.css`, `chrome.ts`, `rubric.ts`, `icons.ts`, `lane_chrome.ts`, `search.ts`
and `apps/web/public/` are ordinary source; edit them directly.

**Migrations are expand-only and checksummed.** Read `docs/migrations.md`
before writing one, and never edit an applied migration file, comments
included: its checksum is recorded in every database it ran against.

## Testing

From the repository root:

```bash
HOME=$(mktemp -d) PYTHONPYCACHEPREFIX=/private/tmp/jobby-pycache \
  python3 -m unittest discover -s tests
node --test tests/*.test.mjs
python3 -m py_compile scripts/*.py
(cd apps/web && npm ci && npx tsc --noEmit && npx next build)
```

Redirecting `HOME` keeps a run away from a real `~/.job-application`. On a
machine without a profile, a handful of tests that check the real corpus skip
themselves, and one resume test that needs a real profile fails; read the skip
reasons rather than the count. `tests/test_apply_end_to_end.py` drives a real
Chrome on a free apply slot and skips when none is available, so check
`watch.py --status` before running the full suite on a machine that is also
applying.

CI (`.github/workflows/checks.yml`) runs the Python and node suites against a
throwaway Postgres, builds the web app and crawls the traced bundle, and fails
when a generated file in `apps/web` has drifted from its source.

## One owner-only surface: the resume wording editor

The site is public. The resume wording editor is the single exception, because
the wording is the owner's employment history. `isOwner()`
(`apps/web/app/_ui/owner.ts`) gates its page section, `GET /api/profile/wording`
and `POST /api/profile/wording`, and nothing else;
`tests/wording_stays_owner_only.test.mjs` fails if a new caller appears. The
session is an HMAC-signed HttpOnly, Secure, SameSite=Lax cookie, 30 days, keyed
on `JOBBY_SESSION_SECRET` and bound to `JOBBY_OWNER_PASSWORD`, so rotating either
ends every session. With either variable unset (or shorter than 12 and 32
characters) the editor says how to turn it on.

The wording never enters Postgres. The device publishes it, with its rendered
preview, to the KV key `jobby:owner:resume-wording` (`scripts/wording_publish.py`,
spawned by `watch.refresh_owner_wording` when its inputs change and forced after
an accepted wording edit), and only the owner route reads that key. Saves go
through `resumeEdits`. The public `/api/profile` refuses a `content` edit, and a
wording edit's outcome records no `submitted`, because `edit_outcomes` reaches
the public Profile page.
