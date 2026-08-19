# Hosting the dashboard

The dashboard is a single self-contained HTML file built from the local store. Three ways to get at it, in increasing order of setup.

| | Setup | Who can read it | Good for |
|---|---|---|---|
| Local file | none | you, on this machine | working at your desk |
| Artifact | none | you, unless you share it | the default; a private page on claude.ai |
| Vercel | one login | you, with the password | your own URL, readable from your phone |

The Artifact path is what `SKILL.md` Phase 5 describes and it needs no account. Use Vercel when you want a URL you control, on your own domain if you like.

## What Vercel hosting actually does

`scripts/publish.py` renders the board from `~/.job-application/`, inlines that HTML into a serverless function, and deploys it to your project's production alias. The deployment holds a **snapshot**. It never reaches back into the store, so nothing on Vercel updates until you publish again.

Every publish overwrites the same URL, so you keep one bookmark.

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

Renders the board, deploys to production, and records the URL in the store as `dashboardUrl`. A fresh session finds it with:

```bash
python3 scripts/store.py meta-get --key dashboardUrl
```

Useful variants:

```bash
python3 scripts/publish.py --local     # serve on localhost:3000, deploy nothing
python3 scripts/publish.py --preview   # throwaway URL, leaves production alone
```

## What a button actually does

A page hosted on Vercel cannot start a process on the machine running the agent. Nothing about a published page grants it that. So no button runs anything directly. Each one **queues a request**, and the local watcher claims it and spawns the agent that does the work.

The path, end to end, is the same for all three:

1. The button POSTs to `/api/scan`, `/api/apply` or `/api/submitted`, behind the same password as the board.
2. The function writes one blob per request under the matching prefix in a private Blob store. The payload lives in the blob **pathname** (`apply/<timestamp>__<applicationId>.json`), not the body, because the store is private and its URLs cannot be fetched without a token. Listing the queue therefore never fetches content.
3. `scripts/watch.py`, running as a launchd agent, reads all three queues in one call every two seconds.
4. Marks are applied inline. A Scan or an Apply spawns a detached runner (`run_scan.py`, `run_apply.py`) which starts a headless Claude session to do the actual work.

Two seconds is the whole lag. A click reaches a running agent almost immediately.

### One at a time, and never blocking

An application can take forty five minutes. The watcher spawns runners detached and tracks them with a lock file under `~/.job-application/locks/`, so a long application never freezes the board or stops other clicks being noticed.

The lock is also what enforces the standing rule that only one application runs at a time. It holds a pid rather than living in memory, so it survives a watcher restart, and a dead pid releases it automatically: a runner killed mid-flight cannot wedge the pipeline forever.

### Setting up the queue

The queue lives in Cloudflare Workers KV. It started on Vercel Blob, and Blob suspended the store once the watcher began polling it. The interesting part is not the vendor but the shape of the limit: free tiers price *listing* a collection far more harshly than reading a key. Workers KV allows 100,000 key reads a day and only 1,000 list requests, so a queue you have to list in order to read is unusable at any poll rate worth having.

So the whole queue is one key. A poll is a single read, 28,800 a day at a three second interval against a ceiling of 100,000. Writes happen only when somebody clicks or a runner claims, a few dozen a day against 1,000. R2 has the same trap in different units: list is a Class A operation with a 1,000,000/month allowance, which a three second poll very nearly exhausts on its own.

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

Three to five seconds keeps a click feeling instant while using well under a fifth of the daily KV read allowance. `scripts/watch.py --install --interval 3` sets it.

### Installing the watcher

```bash
python3 scripts/watch.py --install
python3 scripts/watch.py --status
```

`--status` prints the queue depths, which runner holds a lock, and whether anything is blocking the pipeline. It has no side effects, so it is the right first thing to run when a button seems not to work.

## Four ways this pipeline has failed

Each of these produced a board that looked fine while doing nothing, which is why the current design is shaped the way it is.

**launchd does not give you your PATH.** It provides `/usr/bin:/bin:/usr/sbin:/sbin` and nothing more. `node`, `vercel` and `claude` all live in `/opt/homebrew/bin`, so every queue read died with `FileNotFoundError` and every publish failed. This ran for hours. `scripts/toolpath.py` now resolves every binary to an absolute path, and the plist sets `EnvironmentVariables.PATH` as a second line of defence. Verify with:

```bash
env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin python3 scripts/toolpath.py
```

**A crashed check looks exactly like an empty queue.** Both exit non-zero. The old watcher treated the crash as "nothing to do" and logged a healthy cycle every thirty seconds throughout the outage. `poll_queues()` now raises `TickError` rather than returning empty, and the tick reports health from whether the work actually happened.

**Claiming a whole queue to process one item.** `queue.mjs claim` deletes every pending request. `run_apply` read the queue, ran the first item, then called it, destroying fourteen Apply clicks that had never been touched. Use `claim-one`. A worker that processes one item deletes one item.

**A runner that cannot authenticate still eats the request.** An expired or revoked `claude` OAuth token makes every spawn exit instantly with a 401, and because the request was claimed first, one user click vanished per cycle. Runners now preflight with `scripts/agent.py` before claiming, the watcher will not spawn while auth is broken, and the reason appears as a banner on the board rather than in a log nobody opens.

```bash
python3 scripts/agent.py --check
```

If that fails, run `claude login` in a terminal. The watcher re-checks on its own and clears the banner.

## The browser a spawned agent gets

A `claude -p` session started by the watcher has no MCP servers at all, so by default it has no browser. The Claude for Chrome extension attaches to one session at a time, and that one belongs to whoever is at the keyboard. Left alone, the daemon could tailor an application's documents and then had nowhere to put them.

So spawned agents get their own: `@playwright/mcp`, pinned, driving Chrome against a persistent profile at `~/.job-application/browser-profile`.

```bash
python3 scripts/browser.py --check
```

Persistent matters. The `--isolated` flag throws the profile away on exit, which would mean signing in to every ATS on every run. With a profile that survives, a sign-in done once is a sign-in every later run inherits. To do that sign-in by hand, in the same profile the agents use:

```bash
python3 scripts/browser.py --login https://www.linkedin.com
```

It is deliberately not the everyday Chrome. Playwright cannot attach to a browser that is already running, and driving a signed-in personal profile unattended is not something to do quietly.

The practical consequence is that most Greenhouse, Lever and Ashby forms go end to end unattended, because they need no account. Anything behind a sign-in parks in **Needs you** with the tailored documents already built and filed, which is a decent outcome rather than a failure: the expensive part is done and what is left is a click.

## Knowing when it breaks again

The watcher pings [healthchecks.io](https://healthchecks.io) on every meaningful tick, and `/fail` with the error when a tick cannot do its job. The point is the silence: if the process dies or every tick starts failing, healthchecks notices the pings stopped and alerts. That is precisely the failure that went unnoticed for hours, and no amount of logging catches it, because the logs looked healthy.

Add the ping URL to `deploy/.env.local`:

```
HEALTHCHECKS_URL=https://hc-ping.com/<your-uuid>
```

Set the period to a few minutes and the grace to about ten. `python3 scripts/watch.py --status` reports `health: true` once it is configured.

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
deploy/api/board.js        the gate and the response headers
deploy/api/_dashboard.js   generated by publish.py; holds real application data, gitignored
deploy/vercel.json         routes every path to the function
deploy/public/robots.txt   disallow all
deploy/dev-server.mjs      runs the same handler locally; never uploaded
scripts/publish.py         render, inline, deploy, record the URL
```

`_dashboard.js` carries your applications, so it is written owner-readable and excluded from git. Check that it is still ignored before committing, particularly after editing `.gitignore`.

## Costs and limits

A personal board sits far inside Vercel's Hobby free tier: one function, a handful of requests a day, a payload in the tens of kilobytes. Hobby is for non-commercial personal use.

## Known edges

- **The board is a snapshot.** It reflects the store as of the last publish. Republish at the end of every cycle.
- **Basic Auth has no logout.** Browsers cache the credentials for the session. Close the tab, or use a private window on a shared machine.
- **`Mark submitted` is per-browser.** It writes to `localStorage`, so marking on your laptop does not grey the card out on your phone, and it does not change the store. The real record is the `completed` event.
