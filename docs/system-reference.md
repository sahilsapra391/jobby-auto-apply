# Jobby — system reference

A complete description of what this is, how it works, and what it does today.
Written to be handed to someone who has never seen the codebase and needs enough
to design changes against it.

Everything here reflects the system as it actually runs, verified against the
live store and a passing test suite rather than from memory. Numbers are as of
2026-08-20.

**One caveat on this revision.** The Discovery Engine (§9a) is described here as
it behaves on the `feature/discovery-engine-phase-1` branch, which is under
review and not merged. Everything else describes production. Where the two
differ, the difference is called out.

---

## 1. What it is

Jobby applies to jobs. Not "finds jobs" or "drafts a cover letter": it sources
roles, writes a resume and cover letter specific to each posting, fills the
employer's form, submits it, and records the confirmation the site showed. A
person presses one button and, about three seconds later, an agent is working.

It is two things bolted together:

1. **A Claude Skill** (`SKILL.md` plus `references/`) that tells an agent how to
   do a job application well: what to extract, what to never invent, how each ATS
   behaves, how to tailor a resume without lying.
2. **A daemon and a board.** A launchd agent on the user's Mac watches a queue,
   spawns headless Claude sessions to do the work, and publishes a password-gated
   dashboard the user drives from their phone.

The design constraint that shapes everything: **applications go out under a real
name and cannot be recalled.** A wrong salary figure or an overstated year count
follows the user into interviews. So the system stops rather than guesses, and
several of its mechanisms exist purely to make "stop" the cheap option.

### Current live state

| | |
|---|---|
| Applications submitted | 52, each with a confirmation quoted off the page |
| History events | 107 (`submitting` write-ahead, then `completed`) |
| Roles ever seen | 96, in a durable index that survives session deletion |
| Live sessions | 49: 25 watching, 13 in flight, 9 needing the user, 2 dropped |
| Stored answers | 118, of which 16 are standing policies |
| Company document folders | 61 |
| Tests | 480 passing, across 17 files (171 before Phase 1 of the Discovery Engine) |
| ATS platforms encountered | Greenhouse (28), Ashby (20), Lever (9), LinkedIn (7), Workday (10), SmartRecruiters, Workable, Recruitee, Zoho, Keka, Oracle Recruiting Cloud, Google Careers, Amazon Jobs, Microsoft Careers, Instahyre |

---

## 2. End-to-end flow

What happens between a click and a submitted application.

```
  BOARD (Vercel, password-gated)
    user presses Apply on a role
        │  POST /api/apply   { applicationId }
        ▼
  CLOUDFLARE WORKERS KV        one key: jobby:queue
    { apply: [...], scan: [...], marked: [...],
      drop: [...], restore: [...], focus: [...] }
        │
        │  polled every 3s, one read
        ▼
  WATCHER  (launchd agent on the Mac, scripts/watch.py)
    ├─ single_watcher()      only one watcher may tick
    ├─ poll_queues()         all six queues in one call
    ├─ marks applied inline
    ├─ reserve(slot)         O_EXCL, one of apply-1/2/3
    ├─ claim_one("apply/")   claiming happens ONLY here
    └─ spawn detached ───────────────┐
                                     ▼
  RUNNER  (scripts/run_apply.py --id <app> --slot apply-N)
    ├─ dupe_check --guard            already applied? already in flight?
    ├─ agent.authenticated()         can a spawned agent even run?
    ├─ browser.serve(slot)           this slot's Chrome, its own port
    └─ claude -p <prompt> ───────────┐
                                     ▼
  AGENT  (headless Claude session, one application, start to finish)
    1. brief.py --id <app>           ONE call: watch entry, posting, duplicate
                                     verdict, base resume, identity, what
                                     documents already exist. Writes jd-<app>.txt.
    2. reuse or build resume + cover letter
         ats_match --suggest      which of the posting's words are earned
         resume_pdf verify        layout matches the base exactly
         ats_match                unsourcedTerms must be empty
    3. open the form in its slot's browser, fill, read every value back
    4. finish.py --pre-submit        ONE call: cancelled? duplicate? then the
                                     write-ahead `submitting`. Refuses closed:
                                     no verdict from the guard means no submit,
                                     and a refusal writes nothing at all.
    5. submit once → verify
    6. finish.py --completed         ONE call: the `completed` event with the
       --confirmation '<quoted>'     confirmation actually observed, plus
                                     sync_board. Or --park to send it to
                                     Needs you with the blocker named.
        │
        ▼
  STORE (~/.job-application) → dashboard.py renders → KV → board
```

Each numbered stage reports a phase (`phase.py`), so the board shows live
progress: Queued → Building documents → Filling the form →
Submitting → Verifying.

---

## 3. Why the architecture looks like this

Three constraints produced most of the design, and a change that ignores them
will regress something.

**A page on Vercel cannot start a process on the user's laptop.** Nothing about
a hosted page grants that. So every button *queues a request* and a local watcher
claims it. This is why there is a queue at all, and why the board's optimistic UI
matters: the round trip is seconds, and a reload inside that window used to make
a click look like it had failed.

**The daemon cannot do the work itself.** It cannot tailor a resume or drive a
browser. So it spawns headless Claude sessions. That makes the CLI's auth a
load-bearing dependency and makes the spawn boundary the place where most of the
safety checks live.

**Free tiers price the wrong thing cheaply.** Two separate outages came from
this, covered in §12.

---

## 4. Components

### Local, on the Mac (`scripts/`)

| File | Lines | Responsibility |
|---|---|---|
| `watch.py` | 741 | The daemon. Polls, claims, reserves slots, spawns runners, sweeps stuck work, publishes, reports health. |
| `store.py` | 586 | All persistence. Profile, answers, history, sessions, role index, meta. Atomic writes, topic-matched answer lookup. |
| `browser.py` | 398 | One long-lived Chrome per slot, on its own port and profile. Tab focus, profile cloning. |
| `publish.py` | 364 | Renders the board, pushes it and the documents to KV, guards against a degraded render. |
| `resume_pdf.py` | 341 | Renders a tailored PDF via headless Chrome and verifies it geometrically against the base. |
| `ats_match.py` | 338 | Keyword coverage, and the `--suggest` pass that separates earned vocabulary from unclaimable. |
| `dashboard.py` | 1470 | Generates the entire board as a single self-contained HTML page. |
| `found_lane.py` | 640 | The Found lane: job payload, filter bar, and the client script that renders the cards. |
| `ingest.py` | 400 | One sourcing pass: fetch, gate, enrich, score, upsert, prune, account. |
| `jobs_store.py` | 420 | The discovered-jobs pool, the passed tombstones and the company registry. |
| `identity.py` | 380 | One job description, one record: URL, redirect, alias and simhash matching. |
| `match_score.py` | 300 | The explainable match %: rubric v1, hard gates, breakdown. |
| `seed_companies.py` | 300 | Fills the company registry from `roles.json` and a verified region pack. |
| `search_config.py` | 250 | The saved search: schema, region packs, validation, versioning. |
| `sources_registry.py` | 150 | The source catalog and the accounting that keeps "every source" honest. |
| `promote.py` | 130 | 👁 and Pass: a Found job becoming a Watching session, or a tombstone. |
| `sources/` | 1,200 | Fourteen Tier-1 connectors and the polite-fetching base they share. |
| `run_apply.py` | 300 | Runs one application end to end by spawning an agent. Never claims for itself. |
| `drop.py` | 187 | The user deciding against a role. Kills the runner if it is mid-flight. |
| `run_scan.py` | 149 | Runs one sourcing sweep by spawning an agent. |
| `file_docs.py` | 148 | Files documents into `applications/<Company>/`, role-qualified when a company has several. |
| `toolpath.py` | 158 | Absolute paths to `node`, `vercel`, `claude`, `npx`. Merged config from two files. |
| `dupe_check.py` | 146 | One application per job description, enforced before the browser opens. |
| `seen.py` | 214 | Has this posting been through here before? Enforced on every new watch entry. |
| `brief.py` | 194 | Everything a run needs to start, in one call. Replaces ~12 round trips. |
| `finish.py` | 226 | The two ends of a submission, one call each, every check kept. |
| `experience.py` | 200 | Lifts one role's content out of that posting's tailored resume. |
| `apply_marks.py` | 128 | Applies "Mark submitted" clicks. |
| `agent.py` | 101 | Preflights whether a spawned Claude session can authenticate. |
| `sync_board.py` | 94 | Retires submitted roles out of Watching, republishes. |
| `phase.py` | 91 | Live phase on an in-flight application; remembers the lane it came from. |
| `brand.py` | 182 | Logo, favicon, mascot, lifted from the vendored brand pack. |
| `queue_check.py` | 72 | Proves the queue is configured and writable. |
| `scan_queue.py` | 63 | Scan queue polling. |
| `enqueue.py` | 57 | Puts a request in a queue from the machine (recovery, testing). |
| `start_date.py` | 48 | Earliest start date from the notice period. |

### Hosted (`deploy/`)

| File | Responsibility |
|---|---|
| `api/board.js` | The whole serverless function: Basic auth, CSP, the board, documents, six queue endpoints. |
| `kv.mjs` | Cloudflare KV REST primitives, shared. |
| `kvqueue.mjs` | The click queue: one key, `enqueue` / `claimOne` / `claimAll`, deduped by application id. Also holds the `watch` and `pass` arrays and the `searchConfig` object. |
| `kvcontent.mjs` | Board HTML and documents in KV, with a short in-memory cache. |
| `queue.mjs` | Local CLI over the queue: `poll`, `list`, `enqueue`, `claim-one`, `claim`. |
| `publish-content.mjs` | Pushes board and documents to KV, skipping documents whose bytes are unchanged. |

### The skill (`SKILL.md`, `references/`)

| File | Contents |
|---|---|
| `SKILL.md` | The agent's operating manual: intake, sourcing, filling, the answer policies, what it will not do. |
| `references/hosting.md` | The board, the queue, the watcher, the browsers, and every way this has broken. |
| `references/platforms.md` | Per-ATS mechanics: what actually costs time on Greenhouse, Ashby, Workday, Lever, iCIMS, Google Careers. |
| `references/sourcing.md` | Every channel, with search URL patterns and how to sweep each. |
| `references/tailoring.md` | How to rewrite a resume into a posting's vocabulary without inventing. |
| `references/storage.md` | The store schema and what each status means. |

---

## 5. Data model

Everything lives in `~/.job-application/`, mode 700.

| Path | Shape | Purpose |
|---|---|---|
| `profile.json` | object | Identity, work history, education, and `resumes[]` — one base per region, each with its own `identity` (phone, location, full address) and exactly one marked `default`. |
| `answers.json` | key → entry | 118 reusable answers. Keys are normalised question slugs. Entries carry `question`, `value`, `state`, `tags`. |
| `applications.jsonl` | append-only | 107 events. `started`, `reviewed`, `submitting`, `completed`, `abandoned`, plus outcomes `response` / `interview` / `offer` / `rejected`. |
| `sessions/<id>.json` | one file per role | The working state of a role. Deleted once submitted. A session promoted from Found takes the job's id as its `applicationId`, which is what makes 👁 idempotent. |
| `roles.json` | id → role | Durable index of all 96 roles ever seen. Exists because sessions are deleted on completion, which would otherwise lose the posting URL. |
| `jobs.json` | id → job | The discovery pool. One record per job description however many boards carry it, each with its sightings, its score and its state (`new` / `watched` / `passed` / `applied` / `expired`). Capped at 3,000; only `new` records are prunable. |
| `passed.json` | id → tombstone | Jobs the user passed on. Outlives the job record, so pruning cannot resurrect one. |
| `companies.json` | key → company | 177 companies with their ATS and board token. The flywheel: every company here is covered by Tier 1 for free, forever. |
| `search-config.json` | object | The saved search, versioned. Its absence is meaningful — the hourly scan stays disarmed until it exists. |
| `source-cache.json` | url → validator | `ETag` / `Last-Modified` per endpoint, so an unchanged board costs a request and no payload. |
| `meta.json` | object | `dashboardUrl`, `blocked` (the banner), scan timestamps, `nextTier1ScanAt`, and `lastIngest` — the full account of the last sourcing pass. |
| `config.env` | env | Machine-local settings, notably `HEALTHCHECKS_URL`. Separate from `deploy/.env.local` because `vercel env pull` regenerates that one and discards hand edits. |
| `locks/` | one file per lock | `apply-1/2/3`, `scan`, `watcher`. Each holds a pid. |
| `browser-profile*/` | Chrome profiles | One per slot. Persist logins. |

### Session status → board lane

| Status | Lane | Meaning |
|---|---|---|
| `watch` | Watching | Found, not started |
| `inflight` | In flight | Queued or being worked on |
| `review` / `active` | Needs you | Blocked on something only the user can do |
| `dropped` | Dropped | The user said no. Records `droppedFrom` and `droppedStage`. |
| deleted | Submitted | Lives on as a history event plus the role index |

The write-ahead pattern matters: a `submitting` event is logged *before* the
submit click and `completed` only after a confirmation is read off the page. A
`submitting` with no `completed` after it means a run died mid-submit and the
platform needs checking by hand. That distinction is what makes the Submitted
count trustworthy.

---

## 6. The board

One self-contained HTML page, generated by `dashboard.py`, served from KV behind
HTTP Basic auth with a strict CSP (`default-src 'none'`).

### Lanes

| Lane | Contents | Controls |
|---|---|---|
| **Found** | Everything the last scans turned up, newest-and-best first, each with an explainable % match | 👁 Watch, Apply, Pass, Details, Posting |
| **Needs you** | Blocked applications, each card naming the blocker and what clearing it buys | Retry, Open & finish, Mark submitted, Drop |
| **Submitted** | Everything sent, with the exact resume and cover letter each employer received | Posting, Proof, Resume, Cover letter |
| **Watching** | Roles found, newest first: posted date, experience bar, location, core skills, honest fit read | View posting, Apply, × |
| **In flight** | Live progress through six phases | Posting, Stop |
| **Dropped** | Roles the user said no to, with the lane they came from and how far they got | View posting, Restore |

Set apart in the tab bar: Dropped is a record, not a queue.

### Buttons and their queues

| Control | Endpoint | Queue | Effect |
|---|---|---|---|
| Scan for new jobs | `/api/scan` | `scan` | Spawns a sourcing agent |
| Apply | `/api/apply` | `apply` | Spawns an application agent |
| Retry | `/api/retry` | `apply` | Same queue: re-running after a blocker is cleared *is* applying |
| Mark submitted | `/api/submitted` | `marked` | Logs a `completed` the user performed by hand |
| Drop / Stop / × | `/api/drop` | `drop` | Records the decision, kills the runner if mid-flight |
| Restore | `/api/restore` | `restore` | Puts a dropped role back in Watching |
| Open & finish | `/api/focus` | `focus` | Raises the tab the agent left, with the form still filled |
| 👁 Watch | `/api/watch` | `watch` | Moves a found job into Watching, creating its session |
| Pass | `/api/pass` | `pass` | Tombstones a found job; never offered again |
| Save search | `/api/search-config` | `searchConfig` field | Parks the saved search for the watcher to persist |

Every endpoint is idempotent per application id, because the board re-sends
remembered clicks on load.

### Client behaviour worth knowing

- **Optimistic UI with localStorage replay.** A click moves the card immediately
  and is remembered. On load, anything the server has not caught up with is
  restored *and re-sent*. Restoring only the appearance was not enough: a queue
  wipe once destroyed fourteen requests while the board still drew them as in
  flight, so nothing looked wrong.
- **Search and filter per lane**, hidden when a lane is empty. Found is the
  exception: its jobs travel as JSON and its cards are built in the browser, so
  its much larger filter bar can respond per keystroke without a round trip to
  a machine that is not running. Every value from a posting goes in through
  `textContent`; none of it is ever concatenated into markup.
- **Contextual empty states** naming which button fills the lane.
- **Theme-aware**, with every colour defined in all three theme blocks. A colour
  defined only inside a media query disappears under the other theme.
- **Hero mark morphs into the mascot on hover**, anchored to grow upward so it
  never lands on the headline.

---

## 7. Concurrency

Up to **five applications at once**, each owned by one agent start to finish. No
agent ever handles two.

Two mechanisms carry this, both established by measurement rather than reasoning:

**The watcher claims, never the runner.** The queue is a single KV key updated by
read-modify-write with no compare-and-swap. Two concurrent `claimOne` calls were
tested and both returned the *same* application — applying twice to one job. The
watcher's tick is single-threaded, so claiming there is sequential however many
runners are in flight. `run_apply.py` refuses to start without `--id`, so it
cannot reach into the queue even when invoked by hand.

**One browser per slot.** Two `@playwright/mcp` servers attached to the same
Chrome over CDP share a browser context and drift onto the same tab: drive one to
example.com and the other to example.org, and afterwards both report example.org.
With several applications that is one agent typing into another's form, or pressing
Submit on it. Each slot has its own Chrome on its own debugging port (9222 upward), and the count is
defined once in browser.py with watch.py deriving its locks from it. Chrome's `SingletonLock` forbids sharing a profile, so each has its own,
and `--clone-logins` copies a signed-in profile across so the user signs in once.

Supporting guarantees:

- Slot reservation is `O_CREAT|O_EXCL`. Check-then-act let two watchers hand out
  the same slot, which really happened.
- A watcher lock stops a second watcher ticking at all.
- Locks hold a pid, so a dead runner frees its own slot and a restart loses nothing.
- The tick discards a claimed request whose application is already running.
- `dupe_check` refuses when another run is in flight on the same company+role or
  posting URL, not just when something is already submitted.
- Each slot logs to its own file.

---

## 8. Document generation

Every application gets its own resume and cover letter. Never a reused file.

**Layout is cloned, not re-designed.** `resume_pdf.py render` uses headless
Chrome `--print-to-pdf` (not WeasyPrint, which does not match). `verify` then
diffs the output against the base geometrically and fails on a changed margin, a
missing accent colour, a font size with no counterpart, or a contact block that
wrapped to two lines.

**Content is checked separately.** Employers, dates, employment titles and
numbers are frozen. Only the profile title at the top adapts.

**Vocabulary is pushed hard, within evidence.** `ats_match.py --suggest` splits a
posting's vocabulary into:

- `rewritable` — the posting's word for something the base resume or story bank
  already supports, with the evidence attached (`experimentation` ← `launchdarkly`).
  These are a rewrite, not a claim.
- `notClaimable` — no evidence. Never used.

Agents then re-pick and re-order **Areas of Expertise**, regroup **Technical
Proficiencies** under the posting's names, and lead **experience bullets** with
the posting's phrasing. Suggestions carry the posting's own spelling, because
matching runs on stems and "manag" in Areas of Expertise is worse than nothing.

**The check that keeps it honest:** `unsourcedTerms` must be empty before upload.
A term there appears in both the posting and the tailored resume but in neither
the base nor the story bank, which is the exact shape of a keyword lifted off the
posting. High coverage with a non-empty unsourced list is not a good resume.

Documents are filed to `applications/<Company>/`, role-qualified when a company
has several open roles, and published to KV under their basename so the board can
link them.

---

## 9. Sourcing

Documented in full in `references/sourcing.md`. Channels swept:

- **Aggregators** — LinkedIn, Indeed, Glassdoor, Google Jobs
- **Startup / tech-native** — Y Combinator (workatastartup), Wellfound, HN Who's
  Hiring, Otta, Built In
- **Regional** — India: Naukri, Instahyre, Cutshort, Hirist, Foundit. Middle East:
  Bayt, GulfTalent. Europe: Welcome to the Jungle, Honeypot. Remote: RemoteOK,
  WeWorkRemotely, Remotive
- **Indian ATS platforms** — Zoho Recruit, Darwinbox, Keka, RippleHire, Naukri RMS,
  each with its URL shape and the caveat that custom subdomains hide many from a
  `site:` sweep
- **Company boards directly** — Greenhouse, Lever, Ashby, Workable, SmartRecruiters
- **VC portfolio boards**

Process: query set (titles vary more than expected), parallel sweep, dedupe by
company plus normalised title preferring the company's own ATS, read every
surviving posting's full body, rank on an explicit fit rubric, and report channels
that returned nothing — silence about a channel reads as coverage when it was
absence.

**Two hard-won rules.** Search indexes for ATS platforms are badly stale: half of
Greenhouse results 404, roughly 60% of SmartRecruiters and Workable, half of
Recruitee and Personio return 410. Every kept role is verified live against the
company's own ATS. And sweeps have *hallucinated* postings — one scored 8/10 on a
description matching no real posting anywhere. A description that matches nothing
live is a phantom and is dropped.

Filters: Product Manager and Business Analyst titles, roles asking up to 5 years
(7+ and 10+ rejected), primarily US and India with secondary UK, Canada, Australia.

---

## 9a. The Discovery Engine

*On the `feature/discovery-engine-phase-1` branch, under review. Not merged.*

Sourcing used to be one thing: an agent sweep, driven by the Scan button, which
costs tokens and meets hourly bots with the CAPTCHA walls Jobby refuses to
solve. That is fine for a daily cycle and useless for catching a posting in its
first hour. So sourcing is now two tiers, and only the cheap one runs hourly.

**Tier 1** is HTTP and JSON: no LLM, no browser, no key for nine of its
fourteen channels. Greenhouse, Lever and Ashby boards driven off the company
registry, plus RemoteOK, We Work Remotely, Remotive, Himalayas, Working Nomads
and The Muse. Adzuna, Jooble, Careerjet, Reed and USAJobs join them once their
free keys are installed; until then each reports *skipped (no key)* on every
run, visibly, rather than being quietly absent. A measured full pass across all
fourteen: 212 postings seen, 53 kept, 15 requests, 13 seconds.

**Tier 2** is the existing agent sweep, on a rotating channel pack. Phase 3.

The company registry is the flywheel. Every company in `companies.json` costs
one JSON request an hour, forever, so coverage is a function of how many
companies are known rather than how much an hour of scanning costs. It seeds
from `roles.json` — every company Jobby has ever applied to, with its ATS read
straight off the URL it applied through — plus a curated per-region pack whose
96 board tokens were each fetched once against the platform's public API before
being committed. Eight of 104 candidates did not resolve and were dropped
rather than shipped hopefully: a wrong token is a 404 on every ingest forever.

### One job description, one record

A single posting is routinely syndicated to Greenhouse, Adzuna and LinkedIn
within an hour, with three URLs, three company spellings and three truncations
of the same body. Matching runs in order and the first hit merges: canonical
URL (with the employer link pulled out of the aggregator's query string), then
capped redirect resolution, then company plus normalised title plus country,
then a 64-bit simhash of the body within six bits. The merged record keeps
every sighting, the earliest posted date, and the ATS copy as the one the board
links to.

Two guards go beyond the plan, both because the evidence to separate two
postings sometimes already exists: the title-and-country rule does not merge
when both bodies are known and their fingerprints are far apart, and the
fingerprint rule does not merge across two known-but-different countries. Those
are two applications with two different work-authorisation answers.

### The match %

Deterministic, no LLM per job: title 35, experience 20, location and workplace
15, keyword coverage 20, freshness 10. Unknown attributes take half their
weight rather than zero — Phase 1 extracts none of the deep attributes, and
scoring unknowns as zero would rank every posting that states its requirements
below every posting that does not, which is a fact about the prose rather than
about the job. Hard gates (citizenship or clearance language, failed liveness,
already applied) cap at 25 and say which fired; they never drop a role, because
"not shown" is indistinguishable from "the scan missed it".

Every card can show the arithmetic, and the parts always sum to the total.

### Accounting

`meta.lastIngest` records every run — including the runs that fail — and the
Found header renders it: how many were seen, kept, merged and filtered out,
which channels were skipped and why, and which failed. **A scan that found
nothing has to look different from a scan that did not run.** That is §13 item 1
in a new place, and it is the property most of this design exists to preserve.

---

## 10. Standing answers

118 stored answers, 16 of them policies. Lookup is three-layered: exact key,
then word-overlap scoring at a deliberately high 0.75 threshold (because "product
management" and "project management" are one word apart and one is wrong), then
**topic matching on distinctive phrases** for questions that arrive buried in
legalese and never match on overlap.

| Question | Standing answer |
|---|---|
| Relocation, any wording | **Yes** |
| US visa sponsorship, now or in future | **No** |
| Active immigration case | **No** |
| US work authorization | **Student visa, sponsorship never required** |
| Current location | **Charlotte, NC** (US) / **Navi Mumbai, MH** (India) |
| Recruitment privacy notices | **Confirm** |
| Contact about future opportunities | **Yes** |
| Accuracy attestation ("I certify… true and accurate") | **Tick — only if nothing on the form was inferred** |
| Arbitration agreement acknowledgement | **Tick** |
| Post-employment restrictions (non-compete, non-solicit, garden leave) | **No** |
| Political contributions, any lookback | **No** |
| Relationships / conflicts of interest | **No** |
| Background check *willingness* | **Yes** |
| Interview recording / AI notetaker consent | **Yes** |
| How did you hear about us | **Company website** (never Referral) |
| Free-text screening questions | **Six sentences max, his own voice** |

Compensation, demographics, notice period (15 days), start date (Monday nearest
15 days out) and the story bank are stored separately.

**The boundaries are tested as hard as the matches**, because a marker that is
too loose fills the wrong field, which is worse than parking. Authorising a
consumer report, an FCRA disclosure, criminal history, compensation, and "do you
have a degree in political science" must not resolve to a standing answer, and
do not.

---

## 11. What it will not do

Absolute, regardless of instruction:

- **Solve or bypass CAPTCHAs.** A CAPTCHA exists to establish a human is present
  on someone else's site.
- **Create accounts, type passwords, or read one-time codes.**
- **Sign a binding legal document that grants rights over the user** — background
  check and consumer-report authorisations, FCRA disclosures, non-competes, IP
  assignment. Note the line: being *willing* to undergo a check is a statement of
  intent and is answered Yes; *authorising* one is a different act.
- **Tick a truth attestation on a form where anything was inferred.**
- **Invent an answer to a required field.**

These land in **Needs you** with the exact clause quoted and, since a recent
change, with the tab left on the page the user actually needs and an instruction
saying what clearing it buys them ("this is once per company, not once per
application").

---

## 12. Infrastructure, and the limits that shaped it

| Layer | Technology | Why |
|---|---|---|
| Board hosting | **Vercel** serverless function, `framework: other` | Stable password-gated URL |
| Board + document storage | **Cloudflare Workers KV** | See below |
| Click queue | **Cloudflare Workers KV**, one key | See below |
| Browser automation | **`@playwright/mcp` 0.0.79** over CDP to real Chrome | File uploads and native dropdowns |
| PDF rendering | **headless Chrome `--print-to-pdf`** | WeasyPrint does not match the layout |
| PDF inspection | **pymupdf** | Geometric verification |
| Vector tracing | **potrace** | Favicon: 40KB raster → 263-byte vector |
| Agent runtime | **Claude Code CLI** headless (`claude -p`), Opus 5 | |
| Scheduling | **launchd** user agent, 3s interval | |
| Monitoring | **healthchecks.io** dead-man's switch | |
| Store | **JSON + JSONL on disk**, stdlib only | |
| CI/CD | **GitHub → Vercel Git integration**, root directory `deploy/` | Merging to main deploys |
| Tests | **pytest** + **`node --test`** run from inside pytest | |

### Two rate-limit outages, and what they taught

**Vercel Blob suspended the queue store.** A 2-second poll is ~1.3M list
operations a month. Moved to Workers KV — but the naive port would have died
faster, because KV allows 100,000 key reads a day and only **1,000 list
requests**. Free tiers price *listing a collection* far more harshly than
*reading a key*. So the entire queue lives under one key: a poll is a single
read, 28,800/day at a 3s interval against a 100,000 ceiling. R2 has the same trap
in different units.

**Vercel's 100 deploys/day ran out.** Publishing the board meant redeploying the
whole site, and the board's change signature included each in-flight `phase` —
one application reports six. Thirty applications spent the lot in an afternoon.
The board is served from KV now and deployments carry only code. Phase is out of
the change signature, there is a two-minute floor between deploys, and a spent
quota is recognised so the watcher stops trying for an hour instead of burning
every tick.

---

## 13. Failure modes already fixed

Worth reading before changing anything: each cost real time and several are
non-obvious.

1. **launchd gives an agent `PATH=/usr/bin:/bin:/usr/sbin:/sbin`.** Homebrew is
   invisible. Every queue read died on `FileNotFoundError` and every publish
   failed *for hours* while the watcher logged healthy cycles, because a crashed
   check and an empty queue both exit non-zero.
2. **`vercel` and `npx` are `#!/usr/bin/env node` scripts, not binaries.** An
   absolute path does not save you: `env` resolves `node` against the child's PATH.
3. **`claim` deleted the whole queue** after processing one item, destroying
   fourteen Apply clicks that had never been touched. A worker that handles one
   item must delete one item.
4. **`--permission-mode acceptEdits` does not cover Bash.** Every `python3` call
   in a spawned agent was denied in a session that cannot ask for approval. It
   reported the blockage clearly and exited 0, which read as success.
5. **A spawned CLI session has no MCP servers**, so no browser.
6. **Form state lives in the page, not the profile.** A browser that dies with its
   run takes twenty filled fields with it, which is why "Open & finish" used to
   land on a blank application.
7. **`write_private` shared one temp filename across writers.** With three agents
   one truncated the temp file while another was writing it, and the replace moved
   a half-written file into place. A render then caught the store mid-write and
   published a board with an empty Submitted tab over a good one — 34 applications
   appeared to vanish while every one was on disk.
8. **Store reads were `json.loads(stdout or "[]")`**, turning any failure into a
   convincing empty result. In `apply_marks` that silently emptied the check
   standing between a mark and a duplicate.
9. **A `completed` retracted by a later `abandoned`** was still counted, so a role
   was retired out of Watching while correctly absent from Submitted, and fell off
   the board entirely.
10. **Documents recorded as a path rather than a filename** were silently dropped
    from the publish bundle, and later produced links the `/doc` route rejected.
11. **Playwright wrote page snapshots into the repo** — 33 of 47 containing name,
    phone and home address.
12. **Two watchers handed out the same slot**, putting two agents on one browser.
13. **A relative posted date ("~23 hours") had nothing sortable**, so the freshest
    roles sank to the bottom of a list ordered newest first.

The lesson that recurs: **a system that reports success while doing nothing is
worse than one that is visibly down.** Most of the health machinery exists because
of item 1.

---

## 14. Testing

480 tests, 17 files, run with `python3 -m pytest tests/ -q`. 171 of those
predate the Discovery Engine.

| File | Covers |
|---|---|
| `test_watch.py` | Slots, locks, races, tool paths, browser config, heartbeat |
| `test_dashboard.py` | Rendering, posted dates, document links, escaping |
| `test_publish.py` | Inlining, the degraded-render guard, CLI resolution |
| `test_store.py` | Persistence, schema validation |
| `test_answer_topics.py` | Topic matching **and its boundaries** |
| `test_dupe_check.py` | Duplicate guards including in-flight |
| `test_phase.py` | Lane transitions and hand-back on failure |
| `test_ats_match.py` | Keyword weaving, evidence, noise |
| `test_drop.py` | Drop provenance, runner termination |
| `test_kvqueue.py` → `kvqueue.test.mjs` | Queue logic under `node --test`, run from pytest so the JS cannot drift |
| `test_resume_pdf.py` | PDF verification |
| `test_jobs_store.py` | The pool, tombstones, prune caps, the saved-search schema, the company and source registries |
| `test_identity.py` | The syndication trio in all six arrival orders, simhash thresholds, alias matching, redirect capping, and what must **not** merge |
| `test_match_score.py` | Weights, unknown neutrality, and nine clearance phrases that must fire against nine that must not |
| `test_sources.py` | Per-connector fixtures, visible skips, throttling, backoff, validator caching, fixture provenance and a PII sweep |
| `test_ingest.py` | The config gate, `lastIngest` on success *and* failure, tombstones, already-decided states |
| `test_found_script.py` | The Found lane's client script, parsed and executed by node against a DOM stub |

The JS tests run inside the Python suite deliberately: a separate command nobody
remembers to run is how it drifts.

---

## 15. Known gaps

- **India is a registry-coverage gap, not a scoring gap.** Filtering the board
  to India returns the embedding floor and nothing more, while the United States
  returns six times as much. The instinct is to reach for the scoring dial.
  Don't: India's median score is **69 against the United States' 63**, and its
  mean title match is the **best of any market at 30.3 of 35**. Indian postings
  match the searched titles better than American ones do.

  What India lacks is jobs in the 80+ band — 17 against 165 — and that tracks
  direct-listing share almost exactly:

  | market | direct listings | share of pool | jobs at 80+ |
  |---|---:|---:|---:|
  | US | 1,854 | 59.1% | 165 |
  | Canada | 544 | 35.1% | 49 |
  | India | 251 | 20.4% | 17 |
  | UK | 255 | 15.6% | 17 |
  | Germany | 113 | 12.1% | 11 |
  | UAE | 20 | 4.5% | 7 |

  Canada has a smaller pool than the UK, is twice as direct, and took 140 of the
  remainder while the UK took none. Pool size does not predict the award;
  direct-listing share does. A direct listing carries a full body, so it earns a
  real keywords and experience read instead of the half-weight an unknown gets,
  and that is what reaches 80. Sixty of the United States' 165 top scorers come
  from USAJOBS, which by definition returns nothing for anywhere else.

  So the selection remainder is score-ordered globally and ends up sorting on
  coverage while calling it merit. **The fix is Indian employers in
  `companies.json`**, which is what the per-tenant registry is for. Weighting the
  floor toward the two home markets is a legitimate stopgap for the board today,
  but it treats the symptom, and it should never be described as a scoring fix.

  Blocked on one thing: `companies.json` records no country, so all 516 entries
  read as unknown and the registry cannot answer "how many Indian employers do
  we poll" — which is the number this gap is judged on. Add the field first.

- **Slots 2 and 3 have empty browser profiles.** LinkedIn Easy Apply parks there
  until `browser.py --stop && browser.py --clone-logins` is run with nothing in
  flight.
- **8 of 30 watch entries carry no posted date**, because the sweep that found
  them recorded none. Not repairable without re-visiting each posting.
- **Interleaved agent logs** are per-slot now, but the auth-failure sniffer still
  reads a shared log tail.
- **`meta.blocked` is global**, so any runner can clear a banner another set.
- **No cost tracking.** A batch of applications has a real token cost that nothing
  currently records.
- **Outcome tracking is manual.** `response` / `interview` / `offer` / `rejected`
  events exist in the schema but nothing ingests them from email.
- **No Tier-1b API keys are installed.** Adzuna, Jooble, Careerjet, Reed and
  USAJobs all report *skipped (no key)*, so Phase 1's international reach comes
  entirely from the 140 fetchable company boards. Roughly twenty minutes of
  registration closes this, and it is the single highest-value thing outstanding.
- **Five connectors have never had a live response.** The same five. They are
  written to each published contract and their fixtures are labelled as
  contract-derived rather than recorded; the first run with a key is what
  confirms the shapes.
- **Four Found filters have nothing to filter on.** Required experience, degree,
  role category and employment type are not extracted until Phase 2, so those
  controls filter the `unknown` bucket and the filter bar says so.
- **Twenty registry companies have no derivable ATS.** They carry a careers URL
  and `ats: unknown`; Phase 3's resolver is what clears them.
- **A promoted job has no fit read.** 👁 creates the Watching session
  immediately and the card shows the rubric breakdown, labelled as the rubric.
  The lazy enrichment that writes real prose is not built.
- **The company alias list is load-bearing for cross-source dedupe.** Rule 4
  only compares fingerprints within a company bucket, so an ATS calling a
  company "Northwind" and an aggregator calling it "Northwind Analytics Inc."
  stay two records unless `companies.json` carries an alias joining them. A
  missing alias shows up as a duplicate rather than as an error, and nothing
  warns. Widening the company match is not the fix: merging two real companies
  is worse and harder to spot. Seeding aliases from observed variants is a
  Phase 2 candidate.
- **A sweep takes about 15 minutes and holds the scan lock.** 497 boards, 742
  requests, 14.8 minutes measured. Fine at an hourly cadence, and it cannot
  collide with an application because Tier-1 ingest takes the scan lock rather
  than an apply slot. It is the next ceiling though: the reviewer's read is that
  it is throttle-bound rather than bandwidth-bound, so running hosts in parallel
  while keeping the one-second spacing *within* each host is a cheaper lever
  than splitting the sweep across the hour. Phase 3 needs it; Phase 1 does not.
- **The board holds 3,400 of the pool, not all of it.** Selection gives every
  location in the saved search a floor and spends the rest globally, so a
  regional filter reaches 10–40% of that region rather than the 0–3% a global
  top-N gave. The page says so rather than implying filtering searches
  everything.
- **Adzuna sightings cannot be URL-matched to the employer's posting.** Its
  redirect carries no origin link, so those copies merge on company, title and
  country, or on the description fingerprint, or not at all. Measured on a
  12,802-job pool: 2,539 of 2,592 Adzuna-only records are from employers who
  appear nowhere as an ATS listing, so the channel is overwhelmingly additive
  rather than duplicative. 38 records (1.5%) look like the same requisition as
  an ATS listing already held.
- **The fingerprint veto misfires when one description is a truncation of the
  other.** Of those 38, the 15 whose company, title and country all match
  exactly were each stopped from merging by the veto — and their bodies are the
  same text at different lengths, 500 characters against 1,500, giving a Hamming
  distance of 26 to 32 purely from truncation. The veto's premise is that
  far-apart fingerprints mean different jobs; a prefix breaks it. 15 of 5,567
  ATS records, so 0.27%, and the effect is a duplicate card rather than a lost
  one. The fix is to store the description length and skip the veto when the
  two bodies differ in length by more than a set ratio, falling back to the
  conservative merge — deliberately not done here, because changing merge
  behaviour after a re-review means the merge behaviour is unreviewed.
- **Country is unknown for most remote-feed postings.** "Remote — US only" is a
  restriction rather than a location, so those jobs bucket as `remote` and their
  country stays null rather than being guessed.

---

## 16. Repository

```
jobby-auto-apply/
├── SKILL.md                  the agent's operating manual
├── README.md                 product-facing, with screenshots
├── LICENSE                   MIT
├── references/               sourcing, platforms, tailoring, storage, hosting,
│                             and the source registry (117 channels, status-accounted)
├── sources.json              the machine copy of that registry
├── scripts/                  32 Python modules (§4), incl. sources/ (14 connectors)
├── deploy/                   Vercel function + KV modules
├── assets/brand/             logo pack, favicon, mascot
├── docs/                     screenshots, this document, plans/ and reviews/
├── tests/                    480 tests, incl. fixtures/ recorded from live boards
└── applications/             gitignored; 60 company folders of real documents
```

Deployment: branch → PR → merge to `main` → Vercel deploys from `deploy/`.
Board content is a KV write and needs no deployment.
