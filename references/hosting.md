# Hosting the dashboard

The dashboard is a single self-contained HTML file built from the local store. Two ways to get at it, in increasing order of setup.

| | Setup | Who can read it | Good for |
|---|---|---|---|
| Local file | none | you, on this machine | working at your desk |
| Vercel | one login | you, with the password | your own URL, readable from your phone |

Use Vercel when you want a URL you control, on your own domain if you like.

## What Vercel hosting actually does

`scripts/publish.py` renders the board from `~/.job-application/` and writes the HTML and changed application documents to Cloudflare Workers KV. The Vercel function reads that content from KV. Publishing is a content write, not a deployment, so code deployments and board refreshes have separate limits.

The hosted board still holds a **snapshot**. It never reaches back into the local store, so nothing changes until you publish again. Every publish updates the same URL, so you keep one bookmark.

## The password gate

The board names companies, roles, referral contacts and drafted messages. It does not belong on a public URL.

Vercel's own Deployment Protection does not cover production domains on the Hobby plan (Standard Protection leaves the production domain public; protecting it needs Pro or Enterprise). So the gate is in the function instead: HTTP Basic Auth against a `DASHBOARD_PASSWORD` environment variable, compared as SHA-256 digests so the check does not leak the password's length.

It fails closed. With no `DASHBOARD_PASSWORD` set the function serves `503`, never the board.

The page also goes out with `noindex`, `no-store`, `no-referrer` and a `default-src 'none'` content security policy, so a posting whose markup somehow reached the store still cannot call home. `/healthz` is the one unauthenticated route, and it returns nothing but a timestamp.

## Setup

Sign in first. This opens a browser and is the one step that cannot be scripted:

```bash
vercel login
```

Then link the project and generate a password. It prints the password once and does not store it, so put it in your password manager before closing the terminal:

```bash
python3 scripts/publish.py --setup
```

Run it from anywhere by absolute path; the script resolves its own location.

Pass `--password` to choose your own, and `--project` to change the Vercel project name, which becomes the hostname. It defaults to `jobby-board` rather than anything naming the search, because the hostname is visible in a browser bar before the password prompt is even answered.

## Publishing

```bash
python3 scripts/publish.py
```

Renders the board and writes it to KV at the production URL already recorded as `dashboardUrl`. A fresh session finds that URL with:

```bash
python3 scripts/store.py meta-get --key dashboardUrl
```

Useful variants:

```bash
python3 scripts/publish.py --local              # serve on localhost:3000
python3 scripts/publish.py --deploy --preview   # also deploy code to a throwaway URL
```

## What a button actually does

A page hosted on Vercel cannot start a process on the user's device. Nothing about a published page grants it that. So no button runs anything directly. Each one **queues a request**, and the local watcher claims it.

The Apply and Settings paths use three launchd services on one device:

1. The button POSTs to `/api/apply`, behind the same password as the board. The function appends the application id to the control queue stored under one Cloudflare Workers KV key.
2. `scripts/watch.py`, running as a launchd agent on the Mac, polls the control queue every three seconds and claims the request. Marks are still applied inline, and Scan starts its own detached runner.
3. The watcher live-checks connections in saved order, selects the first provider that can invoke its pinned model, saves that choice with the document job, and submits the encrypted resume-and-cover-letter request. Cloudflare receives AES-256-GCM ciphertext plus an opaque job id, timestamps, state, and a fixed error code.
4. `scripts/document_worker.py --loop --local` claims one document job at a time on the same device. It runs the requested provider CLI with no browser, then accepts output only after the fixed content, provenance, layout, PDF, and cover-letter checks pass.
5. The document client authenticates the verified result, validates the complete set before installing any file, and replaces each file atomically. It then starts `run_apply.py` with the currently healthy provider and the exact resume and cover letter. This stage boundary is where failover may hand work to another connected account.
6. `ai.jobby.providers` independently polls the Settings control lane every two seconds. It opens login before any model probe and publishes Disconnect immediately, so application work cannot hold a connection click for minutes.

A control click still reaches the watcher within a few seconds. Document generation is a separate serial local stage.

### Provider selection is ordered and health-aware

Jobby supports the native OpenAI Codex, Anthropic Claude Code, and Gemini CLIs. One `/settings` Connect click queues the provider for **Your Device**. Both local services share the provider registry.

OpenAI Connect runs `codex login` for supported local ChatGPT subscription access. Anthropic Connect opens the Anthropic Console key page. Gemini Connect opens Google AI Studio. The API connectors collect the key in a private terminal, save it mode 0600 on Your Device, and validate the exact model before reporting Connected. Disconnect deletes that key. The keys never enter the board, queue, Vercel, or Cloudflare.

The first healthy provider in connection order handles new work. A health, authentication, usage, or exact-model failure moves new work to the next healthy connection. If none work, the board shows the blocked banner. An existing document process keeps the provider saved when it was created, while the form stage and a retry may hand off.

A consumer Gemini membership is not Gemini API authorization. Jobby therefore uses the official Google AI Studio API-key path for the Gemini CLI and its pinned model check.

Every spawned task names a fixed model in its command: OpenAI uses `gpt-5.6-luna`, Anthropic uses `claude-sonnet-5`, and Gemini uses `gemini-3.7-flash`. Document, form, and scan tasks never inherit a CLI default.

The provider process is the actor, not the approval gate. Document acceptance, duplicate refusal, exact-file checks, `finish.py --pre-submit`, and terminal-outcome verification remain fixed program logic shared by all three providers.

### Retry, Drop and the Dropped lane

**Retry**, on a Needs you card, is the apply queue again. When the user has done the thing that was blocking an application (created the account, cleared the CAPTCHA, accepted a legal agreement), Retry re-runs the agent, which reuses the documents already built and picks up from the form rather than starting over. There is no separate retry queue because retrying and applying are the same operation.

**Drop** says the user has decided against a role. It is available in three places: an `x` on each Watching row, a Drop button on a Needs you card, and Stop on an In flight card. Dropping something in flight also kills the local runner: the runner is spawned in its own session, so the whole process group is signalled, or the selected provider CLI keeps driving the browser. `drop.py` checks the note on the lock before signalling anything, since by the time a drop is claimed the lock may be held by a different application and killing the wrong run is worse than not killing at all.

Dropped is deliberately not the same as `abandoned`. Abandoned records that a submission attempt failed or was logged in error; dropped records a decision. So the session is kept rather than deleted, which also stops the next sweep re-finding the role and offering it again.

Each dropped row keeps where the application had got to, because "dropped while it sat in Watching" and "dropped halfway through filling a form" are different facts:

| Dropped from | Where it had got to |
| --- | --- |
| Watching | not started |
| Needs you | waiting on you: *the first blocker*, and a count of the rest |
| In flight | the phase it reached, e.g. *Filling the form (14 of 18 fields)* |

**Restore** puts a row back in Watching, for the misclick that an `x` in a dense table invites.

The lane sits apart from the other four in the tab bar. Nothing in it is waiting on anyone, so it should not read as a queue.

### Five local form slots, one serial document worker

Up to five form-filling runs can work concurrently on the Mac. Each local run
owns one browser slot until it reaches a terminal outcome. The local worker builds
documents one job at a time. The document process ends
before the separate local form process begins, so no provider session owns an
application from start to finish. Slots are lock files holding a pid, so a
runner that dies frees its own slot and a watcher restart does not lose track.

Two details here are load-bearing, and both were measured rather than assumed.

**The watcher claims, never the runner.** The queue is a single KV key updated by
read-modify-write with no compare-and-swap. Two runners calling `claim-one` at
the same instant both take the head of the queue, so the same job is applied to
twice. The tick is single-threaded, so claiming there is sequential however many
runners are in flight; the application id is then passed to the runner, which
refuses to start without one.

**One browser per slot, not one shared.** Two `@playwright/mcp` servers attached
to the same Chrome over CDP share a browser context and drift onto the same tab:
navigate one to example.com and the other to example.org, and afterwards both
report being on example.org. With several concurrent applications that is one agent
typing into another's form, or pressing Submit on it.

Chrome will not let two instances share a `user-data-dir` (`SingletonLock`), so
each slot has its own profile on its own debugging port. To avoid signing in
three times, sign in once and copy the profile across:

```bash
python3 scripts/browser.py --login https://www.linkedin.com
python3 scripts/browser.py --stop
python3 scripts/browser.py --clone-logins
```

Cloning refuses while any browser is running, because copying a profile Chrome
has open produces a corrupt one.

```bash
python3 scripts/browser.py --check     # every slot, its port, profile and tabs
python3 scripts/watch.py --status      # which slots are busy and with what
```

### Never blocking

An application can take forty five minutes. The watcher spawns runners detached and tracks them with a lock file under `~/.job-application/locks/`, so a long application never freezes the board or stops other clicks being noticed.

The locks are also what cap local form concurrency at five and keep one browser slot to one application. They live on disk, so they survive a watcher restart. A parked application keeps its slot after the runner exits, and Retry plus Open & finish return to that same browser. Completed, abandoned, and dropped sessions clear their tabs and private browser artifacts before the slot is released.

### Setting up the queue

The queue lives in Cloudflare Workers KV. It started on Vercel Blob, and Blob suspended the store once the watcher began polling it. The interesting part is not the vendor but the shape of the limit: free tiers price *listing* a collection far more harshly than reading a key. Workers KV allows 100,000 key reads a day and only 1,000 list requests, so a queue you have to list in order to read is unusable at any poll rate worth having.

The application queue is one key, so the five-second local poll costs 17,280 reads a day. Provider settings use their own local control key, so claiming a connection request can never write an older copy over a fresh Apply click. The local worker republishes sanitized provider health every ten minutes, inside the API's fifteen-minute freshness window. The retired remote-provider key remains only for backward compatibility. R2 has the same trap in different units: list is a Class A operation with a 1,000,000/month allowance, which a short poll nearly exhausts on its own.

The cost of coalescing is that a write becomes read-modify-write with no compare-and-swap, so a click landing in the same instant as a claim can be lost. That is already covered: the board re-sends clicks it remembers, and enqueue dedupes by application id.

One-time setup:

1. Create a namespace, or reuse the `jobby-queue` one:

```bash
npx wrangler kv namespace create jobby-queue
```

2. Create an API token at **dash.cloudflare.com → My Profile → API Tokens → Create Token**, using the *Custom token* template with a single permission: **Account → Workers KV Storage → Edit**. Scope it to the one account. Nothing else.

3. Put three values in `deploy/.env.local`, which is gitignored:

```
CF_ACCOUNT_ID=<the id in your dashboard URL>
CF_KV_NAMESPACE_ID=<from step 1>
CF_API_TOKEN=<from step 2>
```

4. Give the same three to the Vercel project, so the board's buttons can write:

```bash
cd deploy
vercel env add CF_ACCOUNT_ID production
vercel env add CF_KV_NAMESPACE_ID production
vercel env add CF_API_TOKEN production
```

5. Prove it works, including that the token can actually write, which a read-only token hides until the first click:

```bash
python3 scripts/queue_check.py --write
```

### Poll interval, and why it is not two seconds

The watcher polls every few seconds so a click reaches an agent almost immediately. It is tempting to make that number very small. Do not: at two seconds the old Blob-backed queue ran roughly 1.3 million operations a month and the store was suspended.

Five seconds keeps a click feeling immediate while leaving room for encrypted document status and settings reads. `scripts/watch.py --install --interval 5` sets it.

### Installing the watcher

```bash
python3 scripts/watch.py --install
python3 scripts/watch.py --status
```

The installer retains its launchd Python across later reinstalls. A repository
under macOS Documents can be protected by Privacy controls per interpreter. If
launchd reports `Operation not permitted` while opening `scripts/watch.py`,
reload once with an interpreter that already has Documents access:

```bash
python3 scripts/watch.py --install --service-python /absolute/path/to/python3
```

`--status` prints the queue depths, which runner holds a lock, and whether anything is blocking the pipeline. It has no side effects, so it is the right first thing to run when a button seems not to work.

## Four ways this pipeline has failed

Each of these produced a board that looked fine while doing nothing, which is why the current design is shaped the way it is.

**launchd does not give you your PATH.** It provides `/usr/bin:/bin:/usr/sbin:/sbin` and nothing more. `node`, `vercel` and the then-only `claude` CLI all lived in `/opt/homebrew/bin`, so every queue read died with `FileNotFoundError` and every publish failed. This ran for hours. `scripts/toolpath.py` now resolves every binary, including `codex`, `claude`, and `gemini`, to an absolute path. The plist sets `EnvironmentVariables.PATH` as a second line of defence. Verify with:

```bash
env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin python3 scripts/toolpath.py
```

**A crashed check looks exactly like an empty queue.** Both exit non-zero. The old watcher treated the crash as "nothing to do" and logged a healthy cycle every thirty seconds throughout the outage. `poll_queues()` now raises `TickError` rather than returning empty, and the tick reports health from whether the work actually happened.

**Claiming a whole queue to process one item.** `queue.mjs claim` deletes every pending request. `run_apply` read the queue, ran the first item, then called it, destroying fourteen Apply clicks that had never been touched. Use `claim-one`. A worker that processes one item deletes one item.

**A runner that cannot authenticate still eats the request.** This first happened when an expired `claude` OAuth token made every spawn exit instantly with a 401. Because the request was claimed first, one user click vanished per cycle. Runners now live-check connections in saved order before claiming and select the first one that can invoke its pinned model. If none work, the reason appears as a banner on the board rather than consuming the request.

```bash
python3 scripts/agent.py --check
```

If that fails, reconnect the named provider in `/settings` or use its native login command on the host named by the error. The watcher re-checks on its own and clears the banner.

## The browser a spawned agent gets

A noninteractive Codex, Claude Code, or Gemini CLI process does not inherit a browser connection. `scripts/providers.py` adds a bounded local browser server explicitly for form tasks. Local document tasks never receive a browser, and the form task cannot create or change the documents it receives. `scripts/browser_mcp_proxy.py` removes arbitrary navigation, code execution, network and storage tools, blocks Enter-based submission, and binds uploads to the two staged PDFs.

Each local form agent drives Chrome against a persistent slot profile under `~/.job-application/`.

```bash
python3 scripts/browser.py --check
```

**The browser outlives the run.** It is started once by `serve()` and agents attach to it over CDP (`--cdp-endpoint`), rather than each run starting and stopping its own. That is not a detail: form values live in the page, not in the profile, so a browser that dies with its run takes twenty filled fields with it. That was why a parked application used to send the user to a blank form with all the work gone. Now the tab is still sitting there.

```bash
python3 scripts/browser.py --serve    # start it (the watcher does this on its own)
python3 scripts/browser.py --tabs     # what it currently holds
python3 scripts/browser.py --focus <url>
```

**Sign in once.** The profile persists, so a session is inherited by every later run. LinkedIn matters most here: Easy Apply needs a signed-in session, and without one those postings can only ever park.

```bash
python3 scripts/browser.py --login https://www.linkedin.com
```

That brings the running browser to the front on that page. It used to launch a second Chrome on the same profile, which stopped being safe the moment the browser became long-lived: two Chromes cannot share one `user-data-dir`.

It is deliberately not the everyday Chrome. Playwright cannot attach to a browser that is already running, and driving a signed-in personal profile unattended is not something to do quietly.

The practical consequence is that most Greenhouse, Lever and Ashby forms go end to end unattended, because they need no account. Anything behind a sign-in parks in **Needs you** with the tailored documents already built and filed, which is a decent outcome rather than a failure: the expensive part is done and what is left is a click.

## Knowing when it breaks again

The watcher pings [healthchecks.io](https://healthchecks.io) on every meaningful tick, and `/fail` with the error when a tick cannot do its job. The point is the silence: if the process dies or every tick starts failing, healthchecks notices the pings stopped and alerts. That is precisely the failure that went unnoticed for hours, and no amount of logging catches it, because the logs looked healthy.

Add the ping URL to `~/.job-application/config.env`, **not** to `deploy/.env.local`:

```
HEALTHCHECKS_URL=https://hc-ping.com/<your-uuid>
JOBBY_WORKER_KEY=<local document queue 32-byte base64 key>
```

The safe setter replaces only that assignment, collapses stale duplicates, and
does not print the URL:

```bash
python3 scripts/worker_key.py set-healthchecks https://hc-ping.com/<your-uuid>
```

Generate `JOBBY_WORKER_KEY` once with `python3 scripts/worker_key.py ensure`. Do not put it in the Vercel project. Both local services read the same mode-600 config file. `references/oci-worker.md` is retired historical recovery material.

The config-file distinction matters. `vercel env pull` regenerates `deploy/.env.local` from the Vercel project and silently discards anything added by hand, which is exactly how the first ping URL disappeared. That file belongs to Vercel. Settings that belong to this machine go in `~/.job-application/config.env`, which nothing overwrites, and which wins when both define the same key.

Set **period 1 hour, grace 10 minutes**.

The watcher sends a quiet heartbeat every 55 minutes and can ping sooner when real work occurs. Failures use the Healthchecks `/fail` endpoint. A one-hour period with ten minutes of grace tolerates ordinary scheduling drift while still reporting a stopped local service. `python3 scripts/watch.py --status` reports `health: true` once it is configured.

### Recovering lost requests

The board remembers Apply clicks in `localStorage` and re-sends any the server has not acted on when the page loads. `/api/apply` dedupes by application id, so that retry is safe and a lost request heals itself on the next visit.

To queue one by hand:

```bash
python3 scripts/enqueue.py apply <applicationId>
```

## Rotating the password

```bash
python3 scripts/publish.py --setup --password 'something-else'
python3 scripts/publish.py
```

The environment variable is read at request time, but a redeploy is the reliable way to be sure the change is live everywhere.

## Files

```
deploy/api/board.js         the gate, response headers, queue and settings endpoints
deploy/kvcontent.mjs        board, submitted documents, and sanitized host status
deploy/kvqueue.mjs          the one-key control queue
deploy/kvjobs.mjs           encrypted document-job transport and fixed status records
deploy/document-jobs.mjs    fixed local command bridge for document jobs
deploy/vercel.json          routes every path to the function
deploy/public/robots.txt    disallow all
deploy/dev-server.mjs       runs the same handler locally; never uploaded
scripts/publish.py          render and write content to KV, record the stable URL
scripts/document_job.py     Mac client for encrypted document work
scripts/document_worker.py  serial local document worker
```

Board HTML and application documents no longer enter the repository or a deployment. `publish.py` sends them to KV through owner-only temporary files.

## Costs and limits

A personal board sits far inside Vercel's Hobby free tier: one function, a handful of requests a day, a payload in the tens of kilobytes. Hobby is for non-commercial personal use.

## Known edges

- **The board is a snapshot.** It reflects the store as of the last publish. Republish at the end of every cycle.
- **Basic Auth has no logout.** Browsers cache the credentials for the session. Close the tab, or use a private window on a shared machine.
- **`Mark submitted` is per-browser.** It writes to `localStorage`, so marking on your laptop does not grey the card out on your phone, and it does not change the store. The real record is the `completed` event.
