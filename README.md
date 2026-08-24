<div align="center">

<img src="docs/logo.png" alt="Jobby" width="720">

**I stopped applying to jobs myself. Jobby agents do it now.**

One tailored resume and cover letter per posting. Forms filled and submitted end to end.
A board that tells me the truth about what is actually happening.

`MIT` · `OpenAI` · `Anthropic` · `Gemini` · `macOS`

</div>

---

Jobby is a provider-neutral agent pipeline with two local workers. Press **Apply** on a role and
the serial document worker builds and checks a resume and cover letter on Your Device. Once both
files pass the fixed gates, a separate local agent opens the form, fills it, and submits it.
OpenAI Codex, Anthropic Claude Code, and Gemini CLI use the same bounded task contracts.

It does not invent anything. Every claim on a generated resume has to trace back to a resume you
wrote, an answer you gave, or a story you told it. Where it cannot answer honestly, it stops.

## The board

Five lanes. Four are work; the fifth is a record of decisions.

### Needs you

Applications that hit something only a person can do: creating an account, typing a password,
clearing a CAPTCHA. Each card says what to do and what doing it buys you. Creating one Workday
account unblocks every future role at that company, because the agent's browser keeps the session
and the saved password.

<img src="docs/needs-you.png" alt="Needs you" width="900">

**Retry** re-runs the agent once you have cleared the blocker: it reuses the documents already
built and picks up at the form. **Open & finish** raises the tab the agent left behind, with the
form still filled, rather than opening a fresh copy of the application.

### Watching

Roles it found, newest first, with the experience bar, the location, the core skills, and an
honest read on how you line up, including where you fall short. **Apply** starts the pipeline.
The **×** drops the role.

<img src="docs/watching.png" alt="Watching" width="900">

### Submitted

What actually went out, with the exact resume and cover letter each employer received. Nothing
reaches this lane without a confirmation quoted off the page after submitting. Not "the click
succeeded", the words the site showed.

<img src="docs/submitted.png" alt="Submitted" width="900">

### In flight, and Dropped

**In flight** shows live progress through building documents, filling, submitting and verifying,
and lets you **Stop** a run mid-form. **Dropped** is what you said no to, and it records the lane
it came from and how far it had got: *not started*, *waiting on you: create a Workday account*, or
*Filling the form (14 of 18 fields)*.

<img src="docs/dropped.png" alt="Dropped" width="900">

## What makes it different

**A resume and a cover letter per posting.** Never a reused file. It clones your existing layout
exactly and rewords only the content. Before either is uploaded, `scripts/resume_pdf.py verify`
diffs the rebuilt PDF against your original and fails on a changed margin, a missing accent
colour, or a font size with no counterpart. Content is checked separately: employers, dates,
titles and numbers all have to trace back to your real resume.

**One application per job description, enforced.** Not audited afterwards, blocked before the
browser opens. The same posting reaches a watch list under different ids depending on where it was
found, so the guard matches on application id, on normalised company and role, and on the posting
URL with tracking stripped.

**It stops rather than guesses.** No account creation, no passwords, no one-time codes, no
CAPTCHAs, and no ticking a truth attestation on a form where anything was inferred. Those land in
Needs you with the exact clause quoted.

**Region-aware.** Indian postings get the India resume and phone; US postings get the US ones.
Your street address goes into application forms when they ask, and never onto a resume header.

## Setting it up

```bash
python3 scripts/store.py init          # create the local store
python3 scripts/publish.py --setup     # link Vercel, set a board password
python3 scripts/watch.py --install     # install watcher and local document service
python3 scripts/queue_check.py --write # prove the queue works end to end
```

The installer retains the Python executable recorded in the launchd plist. If
macOS privacy denies a new interpreter access to a repository under Documents,
rerun the install once with an interpreter that already has access:

```bash
python3 scripts/watch.py --install --service-python /absolute/path/to/python3
```

The queue lives in Cloudflare Workers KV and the board is served from it. Put these in
`deploy/.env.local`:

```
CF_ACCOUNT_ID=...
CF_KV_NAMESPACE_ID=...
CF_API_TOKEN=...            # scoped to Workers KV Storage: Edit, nothing else
```

Machine-local settings go in `~/.job-application/config.env`, which nothing overwrites:

```
HEALTHCHECKS_URL=https://hc-ping.com/<uuid>
JOBBY_WORKER_KEY=<local document queue 32-byte base64 key>
```

Use `python3 scripts/worker_key.py set-healthchecks https://hc-ping.com/<uuid>`
to replace the ping URL atomically without printing it or changing the worker
key.

Open `/settings` on the board and connect one or more providers. The first connected provider
stays active until you disconnect it. Resume tailoring, cover-letter creation, scans, and form
filling all use that provider on Your Device. Credentials remain in the provider CLI's native
account store. Connect opens the provider CLI's official browser login. OpenAI uses the ChatGPT
account flow, Anthropic explicitly uses the Claude subscription flow, and Gemini uses Sign in
with Google. Work counts against that account's subscription limits or usage quota. Jobby stores
only connection order, health, and exact-model verification time.
Every run uses a fixed model: `gpt-5.6-luna` for OpenAI, `claude-sonnet-5` for Anthropic, and
`gemini-3.7-flash` for Gemini. Jobby never inherits a provider CLI's changing default model.
If the requested model is unavailable or Gemini reports a different served model, the connection
fails closed. Jobby does not silently switch to another connected provider.

Home and Settings appear as two centred pills in the top app bar, between the Jobby logo and the
theme control. Home is the full application dashboard. Settings owns the three provider
connections. A future Profile pill can join the same control without changing the dashboard
lanes.

Then sign the agent's browser in once, which every later run inherits:

```bash
python3 scripts/browser.py --login https://www.linkedin.com
```

## How it actually works

A page on Vercel cannot start a process on your laptop. Every button queues a request.
`scripts/watch.py` reads the control queue and coordinates the pipeline. The launchd installation
also starts `scripts/document_worker.py --loop --local`, which claims one encrypted document job
at a time on the same Mac. Cloudflare KV sees ciphertext and fixed status fields, not the candidate
profile, job description, resume, or cover letter. The local result is authenticated, validated as
one complete set, and installed before form filling starts.

Runs are spawned detached and tracked by a pid lock file. One application at a time, and a
forty-five minute run never freezes the board. A dead pid releases the lock on its own, so a
runner killed mid-flight cannot wedge the pipeline.

Spawned form sessions attach over CDP to one long-lived Chrome per application slot. A local MCP
firewall exposes only bounded form actions. It removes arbitrary JavaScript, shell, network-log,
storage, and navigation tools, blocks Enter-based submission, and permits uploads only for the two
hash-verified PDFs staged for that application. That is what makes a parked application resumable
without giving page text a general code-execution path.

Submission is a two-step local state transition. `pre_submit` writes a `submitting` event and
returns a one-use nonce. `record_completed` requires that nonce, the same application, the latest
event still being `submitting`, and exact visible confirmation text. A page cannot move a role to
Submitted by merely telling the model to call the completion tool.

The watcher reports to [healthchecks.io](https://healthchecks.io), failures included. Its quiet
heartbeat is every 55 minutes, which fits a one-hour check with ten minutes of grace. If the local
service dies, the pings stop.

## Job discovery and ATS coverage

The Found lane is fed by twenty-six Tier-1 channels. Fifteen are direct ATS
board connectors: Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
Recruitee, Personio, Breezy, Rippling, Workday CXS, Pinpoint, Teamtailor,
JazzHR, Oracle Recruiting Cloud and Gem. The rest are public feeds and keyed
search APIs.
The scan header distinguishes jobs seen from jobs newly admitted. A connector
can be healthy and report zero new jobs when its boards are unchanged, or when
every result was already stored, filtered, or merged.

ATS coverage is company-driven. These systems do not expose a global stream of
every customer's jobs. Each request needs a verified company board token, so a
connector with no known tenants adds no postings. Public endpoints are connected
only after their live shape has been verified. Auth-walled systems remain marked
`planned` or `blocked` in [`references/sources-registry.md`](references/sources-registry.md)
rather than returning a convincing empty result. The implementation and next
connector groups are tracked in
[`docs/plans/ats-coverage-triage.md`](docs/plans/ats-coverage-triage.md).

The registry grows during every ingest. Any normalized sighting that contains a
real ATS URL can add or complete that employer's tenant record even when the job
itself fails today's search gate. Aggregator URLs never become tenant evidence.
The fifty explicitly requested ATS families have a machine-checked accounting
contract in `scripts/sources_registry.py`; connected, planned and blocked are
all visible states, while absence is a test failure.

Transient HTTP failures retry with exponential backoff. Once one host exhausts
those retries, a per-sweep circuit breaker prevents every tenant on that host
from being hammered. A 404 stays tenant-specific and does not open the circuit.
Failures remain visible in the scan report, while earlier stored sightings stay
in the pool.

Aggregator access follows the same honesty rule. Tsenta is planned pending an
authorized discovery-feed API. Simplify and Wellfound prohibit the collection
needed for a local job database, Indeed's APIs require an approved partner use,
and Naukri blocked automated access during verification. Their reasons are in
the source registry rather than hidden behind empty runs.

Date filtering preserves exact timestamps as compact epoch seconds. The 6-hour view uses the source's
exact posting instant where one exists. If an ATS provides only a calendar date,
it uses Jobby's exact first-seen instant for that sub-day view. Wider windows use
the employer's posting date. This distinction is pinned through the real compact
payload and client filter in `tests/test_found_script.py`.

## Things it learned the hard way

Each of these is a comment in the code, because each cost real time:

- launchd gives an agent `PATH=/usr/bin:/bin:/usr/sbin:/sbin` and nothing else. Homebrew is
  invisible. Every queue read died for hours while the watcher logged healthy cycles, because a
  crashed check and an empty queue both exit non-zero.
- `vercel` and `npx` are `#!/usr/bin/env node` scripts. An absolute path does not save you: `env`
  resolves `node` against the child's PATH.
- Free tiers price *listing* a collection far more harshly than reading a key. Workers KV allows
  100,000 reads a day and 1,000 list requests. The application queue uses one key. Provider
  connection actions use one local-device lane, and the retired remote-provider lane is not polled.
- Publishing the board used to redeploy the site. Vercel allows 100 deploys a day and one
  application reports six phases. The board is served from KV now; deployments carry only code.
- A worker that processes one item must delete one item. `claim` once destroyed fourteen queued
  Apply clicks after running the first.

## What it will not do

Solve CAPTCHAs. Create accounts or type passwords. Read one-time codes. Tick a truthfulness
attestation on a form where anything was inferred. Agree to a binding legal document you have not
authorised. Invent an answer to a required field.

## Tests

```bash
python3 -m pytest tests/ -q
```

The suite includes Python tests, Node API and queue tests, real Chrome render checks, provider
command-contract tests, and browser-firewall protocol tests.
