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

## The Scan button

The board carries a **Scan for new jobs** button. It is worth being exact about what that does, because the obvious reading is wrong.

A page hosted on Vercel cannot start a process on the machine running the agent. Nothing about a published page grants it that. So the button does not run a scan. It **queues a request**, and something local has to claim it.

The path, end to end:

1. The button POSTs to `/api/scan`, behind the same password as the board.
2. The function writes one blob per request under the `scan/` prefix in a private Blob store. The timestamp is in the pathname, so listing the queue never fetches content. The token lives in `BLOB_READ_WRITE_TOKEN` and never reaches the browser.
3. On this machine, `scripts/scan_queue.py --check` polls the queue and exits 0 when something is pending.
4. A Claude Code session running `/loop` notices and runs a scan cycle, then `--claim` removes the request.

The button says "Queued. The agent picks this up on its next check" rather than "Scanning...", because claiming otherwise would be a lie about what happened.

### The "Mark submitted" button

Same shape as Scan, same reason. When an application in **Needs you** was submitted by hand (a CAPTCHA you cleared, an account you created, a legal attestation only you can tick), pressing **Mark submitted** on its card:

1. POSTs the application id to `/api/submitted`, which queues it as a blob under `marked/`.
2. Moves the card out of Needs you and into Submitted immediately in the browser, so the tabs reflect what you just did rather than waiting on a round trip.
3. `scripts/apply_marks.py` claims the queue on this machine, logs a `completed` event, retires the watch-list session and republishes, making the change durable.

```bash
DASHBOARD_PASSWORD='<board password>' python3 scripts/apply_marks.py
```

The optimistic move is deliberate: the click is a statement of fact about something that already happened, not a request for the agent to do something, so showing it as done immediately is honest. Until `apply_marks.py` runs, the next republish would put the card back, which is why the poller matters.

The queue payload lives in the blob *pathname* (`marked/<timestamp>__<applicationId>.json`), not the blob body. The store is private, so its URLs cannot be fetched without a token, and reading the body from inside the function would have returned nothing.

### Setting up the queue

Until a queue exists the endpoint returns 503 and the button says so honestly instead of failing silently:

```bash
cd deploy && vercel blob create-store jobby-scan-queue --access private --yes
cd .. && python3 scripts/publish.py
```

That sets `BLOB_READ_WRITE_TOKEN` on the project automatically and writes it into `deploy/.env.local`, which is gitignored.

Note that `vercel storage add` does not exist: Vercel KV was discontinued and moved to the Marketplace in December 2024. Blob is the first-party option that needs no second account.

### Making it actually fire

The queue is only half of it. Something has to poll:

```bash
DASHBOARD_PASSWORD='<your board password>' python3 scripts/scan_queue.py --check
```

Wire that into a `/loop` in a Claude Code session and the button becomes a real trigger with a lag of one poll interval. Without a poller running, requests simply pile up until the next time someone looks, which is a perfectly reasonable way to use it, just not an instant one.

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
