# Jobby Discovery Engine — Phased Plan

**Status:** Proposed · v2 · 2026-08-19
**Author:** Product/eng planning session (Sahil + Claude)
**Executor:** Claude Gold, working in `jobby-auto-apply`
**Format:** PRD + engineering spec in one. Phase 1 is written at handoff depth;
Phases 2–4 are planning depth and get their own handoff docs when reached.

**v2 changes (Sahil, 2026-08-19):** worldwide source coverage with a maintained
source registry and per-region packs (US · India · Canada · Australia · all of
Europe · Middle East incl. UAE & Saudi · Japan · Korea · Southeast Asia);
hardened cross-source dedupe (one JD posted in three places = one record);
the eye button (👁 Watch) on every Found card; **hard code review gate after
every phase**; Tier-2 sweeps **at least 6×/day** with channel rotation.

---

## 1. Problem & goal

Jobby applies brilliantly but sources thinly. Sahil wants:

- **Maximal coverage:** every reachable job posting / curation / viewing
  platform and every company board, across **US, India, Canada, Australia, all
  of Europe, the Middle East (UAE, Saudi Arabia, wider Gulf), Japan, Korea,
  and Southeast Asia**. Coverage is *accounted for*, not assumed: every ingest
  reports which channels ran, which failed, which yielded nothing.
- **Zero duplicates:** one job description syndicated across three boards
  enters the system exactly once, merged, with all sightings recorded.
- **A discovery surface** as the *first* board section: every job found for
  you, each with an explainable **% match**, filterable tsenta-style
  (keywords include/exclude, date, location, workplace, companies only/hide,
  degree level, max experience, role category, job type, employment type).
- **Hourly auto-ingest** driven by the *most recently saved* search config;
  manual "Scan now" on the same page; config changes take effect next run.
- **The eye:** 👁 on every Found card moves that job to **Watching** — it is
  being watched now, with Apply and the full card affordances available there.
- **Not** "pull everything into view": scans and retention are scoped by the
  saved config.

Reference interfaces (screen recordings, 2026-08-19): **tsenta** (% match
ring, card grid, deep filter bar, include/exclude keyword pills, Companies
only-these/hide, add-your-own-link) and **Careerminds** ("matched and queued
for your review" framing, "Open to all" experience semantics, jobs-with-X-only
toggles). Jobby keeps: honest fit reads, explainable scores, unknowns shown as
unknown, ATS-preferred dedupe, live-verified postings, and **nothing
auto-applies** — Apply is always explicit (§9, decision 2).

---

## 2. Architecture: two-tier sourcing, one registry

Hourly scanning is only viable if the hourly path costs ~nothing. Agent sweeps
cost tokens, and LinkedIn/Indeed meet hourly bots with the CAPTCHA walls Jobby
refuses to solve. So:

```
                    saved search config (one object, versioned)
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                           ▼
TIER 1a · OPEN APIs        TIER 1b · FREE-KEY APIs      TIER 2 · AGENT SWEEPS
(hourly, $0)               (hourly, $0, key in env)     (≥6×/day, rotating packs)
per-company ATS boards:    Adzuna (~20 countries:       LinkedIn · Indeed · Naukri ·
Greenhouse/Lever/Ashby/…   US CA GB AU DE FR NL PL      SEEK · Bayt · GulfTalent ·
+ remote feeds:            ES IT AT BE CH SE SG NZ      Wellfound · YC · HN · VC boards ·
RemoteOK/WWR/Remotive/     BR MX ZA IN…), Jooble        JobStreet/JobsDB · Wanted ·
Himalayas                  (60+ incl. Gulf/JP/KR),      JapanDev/TokyoDev · regional
        │                  Careerjet, Reed (UK),        packs (§Appendix A)
        │                  USAJobs, The Muse                    │
        │                          │                            │
        │                          │        every company found → ATS resolved once
        │                          │        → registry → future coverage is Tier-1-cheap
        ▼                          ▼                            ▼
   ┌────────────────────────────────────────────────────────────────┐
   │ INGEST: normalize → IDENTITY RESOLUTION (dedupe/merge) →       │
   │ config gate → extract → score → upsert → prune → publish       │
   └────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                     BOARD · "Found" lane (first tab)
              client-side filters · % ring · 👁 Watch / Pass / Apply
```

**Company registry** (`~/.job-application/companies.json`) is the flywheel:
`name → { aliases[], ats, token, careersUrl, industry?, region, addedFrom,
lastFetchAt, lastStatus }`. Seeded from `roles.json` (96 roles already carry
company + ATS) plus curated per-region packs (§Appendix A); grown automatically
by Tier 2 (Phase 3).

**Source registry** (`references/sources-registry.md` + `sources.json`): the
canonical catalog of every channel, its class, tier, region, and live status.
"Every single source covered" is enforced as accounting: a channel is either
*connected*, *swept on rotation*, *blocked (reason)*, or *excluded (reason)* —
never silently absent. Appendix A is its initial content.

Courtesy rules: per-domain throttle (≥1s/host), `ETag`/`If-Modified-Since`,
backoff on 429/5xx, **loud** failure accounting (a connector that silently
returns nothing is the §13-item-1 failure mode reborn).

**Honest limits (unchanged Jobby law):** no CAPTCHA solving, no account
creation, no passwords, no paywall circumvention. Auth-walled channels
(e.g. BizReach, Handshake, FlexJobs) run only through the user's own signed-in
browser slots via Tier-2, or are marked *excluded (auth)* in the registry.
"Every job on earth" is the goal; the mechanism is exhaustive cataloging +
coverage accounting + the registry flywheel, with structural exclusions
documented rather than papered over.

---

## 3. Data model

### 3.1 Jobs store — `~/.job-application/jobs.json`

`id → job`, atomic writes via the existing `write_private` pattern.
`id = sha1(dedupeKey)[:16]` where `dedupeKey` is the *canonical identity*
(§3.2), not the sighting URL.

```json
{
  "id": "a1b2c3d4e5f6a7b8",
  "company": "Datadog",
  "title": "Manager, Internal Product Analytics",
  "normTitle": "manager internal product analytics",
  "canonicalUrl": "https://boards.greenhouse.io/datadog/jobs/123",
  "canonicalSource": "greenhouse",
  "sources": [
    { "source": "greenhouse", "tier": "1a", "url": "…", "firstSeenAt": "…" },
    { "source": "adzuna",     "tier": "1b", "url": "…", "firstSeenAt": "…" },
    { "source": "linkedin",   "tier": "2",  "url": "…", "firstSeenAt": "…" }
  ],
  "location": { "raw": "New York, NY", "city": "New York", "region": "NY", "country": "US" },
  "workplace": "hybrid",
  "postedAt": "2026-08-13T00:00:00Z",
  "firstSeenAt": "2026-08-19T20:00:00Z",
  "lastSeenAt": "2026-08-19T21:00:00Z",
  "seenCount": 3,
  "lang": "en",
  "descriptionExcerpt": "…first ~1500 chars…",
  "descFingerprint": "simhash64:…",
  "tags": ["sql", "data warehousing"],
  "salary": null,
  "extracted": { "yearsMax": null, "degree": null, "employmentType": null, "roleCategory": null },
  "industry": null,
  "score": { "total": 82, "parts": { "title": 30, "experience": 14, "location": 15, "keywords": 15, "freshness": 8 }, "gates": [] },
  "configVersion": 7,
  "state": "new"          // new | watched | passed | applied | expired
}
```

### 3.2 Identity resolution — one JD, one record (hard requirement)

Applied at ingest, in order; first hit merges into the existing record:

1. **Canonical URL.** Strip tracking params; aggregator records that carry an
   "apply on company site" / origin link are resolved to it (Adzuna, Jooble,
   Indeed and LinkedIn snippets usually expose one). ATS URL wins.
2. **Redirect resolution.** Shortened/redirect apply links are followed
   (HEAD, capped hops) to their final host before comparison.
3. **Company + normTitle + location bucket** (country, or `remote`), with
   company matched through `aliases[]` (legal suffixes, brand names).
4. **Content fingerprint.** 64-bit simhash of normalized description text;
   Hamming distance ≤ 6 within the same company (or same title bucket when the
   aggregator hid the company) = same job. Catches the three-boards-one-JD
   case even when URLs and titles differ cosmetically.

**Merge policy:** canonical source priority = company ATS > company careers
page > major aggregator > other. Merged record keeps every sighting in
`sources[]`, aggregates `seenCount`, keeps the earliest `postedAt` /
`firstSeenAt`. The card may show a "seen on 3 boards" chip. A merge is logged;
`meta.lastIngest.deduped` counts them so dedupe health is visible.

Also deduped against: `passed.json` tombstones (a passed job is **never
re-offered** — same principle as Dropped), submitted history and in-flight
sessions via `dupe_check` matching (→ `state: applied`, hidden), and
`roles.json`.

Retention: prune `state=new` after 30 days or beyond a **3,000-job cap**
(aggregator APIs raise volume; oldest, lowest-score first). `watched`,
`applied`, `passed` are never pruned by the cap.

### 3.3 Saved search config — `~/.job-application/search-config.json`

As v1, with v2 deltas:

```json
{
  "version": 8,
  "keywords": ["product manager", "business analyst", "product owner"],
  "excludeKeywords": [],
  "searchScope": "title+description",
  "roleCategories": ["product_management", "business_analysis"],
  "locations": [
    {"country":"US"},{"country":"IN"},{"country":"CA"},{"country":"AU"},
    {"regionPack":"EU"},{"regionPack":"MIDDLE_EAST"},{"country":"JP"},
    {"country":"KR"},{"regionPack":"SEA"}
  ],
  "includeRemoteForSelection": true,
  "workplace": ["remote","hybrid","onsite"],
  "companiesMode": "all", "companies": [],
  "maxExperienceYears": 5,
  "degreeMax": "masters",
  "employmentTypes": ["full_time"], "jobTypes": [],
  "postedWithin": "7d",
  "includeUnknowns": { "experience": true, "degree": true, "workplace": true, "postedAt": true },
  "sponsorsVisaOnly": false,
  "minScoreToKeep": 40,
  "tier1IntervalMinutes": 60,
  "tier2SweepsPerDay": 6
}
```

`REGION_PACKS` is a constant: `EU` expands to all EU/EEA/UK countries;
`MIDDLE_EAST` = AE, SA, QA, KW, BH, OM, JO, EG; `SEA` = SG, MY, PH, ID, TH, VN.
`tier2SweepsPerDay` validates to **minimum 6**.

Transport unchanged from v1 (rides the single `jobby:queue` KV key; zero extra
reads; one write per save; last-write-wins noted as multi-user TODO).

### 3.4 Meta additions — `meta.json`

`nextTier1ScanAt`, `sweepSchedule` (next slot + pack rotation cursor),
`lastIngest { at, tier, found, kept, deduped, gatedOut, byChannel{}, channelsSkipped[], errors[] }` — rendered on the Found header so absence is visible.

---

## 4. Match % — explainable rubric (v1)

Unchanged from plan v1: deterministic, no LLM per job. Title/role 35 ·
experience 20 · location/workplace 15 · keyword/skill coverage 20 (reuse
`ats_match` stemming) · freshness 10 (null `postedAt` → `firstSeenAt` at half
weight). Hard gates (cap at 25, label, never silently drop):
citizenship/clearance language, failed liveness, duplicate-of-applied.
Unknown attribute → neutral score + explicit `unknown` chip. Every card
exposes **"why this %"** (component bars + gates). Non-English postings
(`lang != en`): score on what stems match, show a `lang: ja` chip; translation
enrichment is Phase 3. Weights live in one module for Phase-4 calibration.

---

## 5. Board — the **Found** lane

New first tab: `Found 214` (default when non-empty; header keeps "9 waiting on
you" visible). tsenta-style grid under Jobby's constraints (CSP
`default-src 'none'`, single self-contained page, every colour in all three
theme blocks).

**Card:** % ring · company · title · location + workplace chip + posted-ago
(falls back to "seen 2h ago") · tags · attribute chips incl. explicit
`unknown` · "seen on N boards" when merged · actions:

- **👁 Watch** — *the eye button.* One tap moves the job to **Watching**: a
  session is created (status `watch`) from the job record, the card leaves
  Found (`state: watched`), and in Watching it gets the full existing
  treatment — Apply right there, View posting, ×. Eye outline → filled on tap;
  optimistic with localStorage replay; idempotent per job id (new `watch`
  array in the same queue key). Fit-read prose is generated lazily on promote
  (small enrichment task) — until it lands, the card shows the rubric
  breakdown, honestly labeled.
- **Pass** — tombstone; never re-offered; provenance kept.
- **Details** — expand: excerpt, why-this-%, all sighting links.
- **Apply** — straight into the existing apply queue (unchanged consent
  model).

**Filter bar** (client-side, instant): keyword pills + "Add another…" ·
Exclude "hide jobs mentioning…" · scope Title / Title+description · Date
radios (6h · 24h · 7d · 14d · 30d · all) · Location searchable multi
(countries + region packs) + "include remote for this selection" · Workplace ·
Companies (Only these / Hide) · Max experience ("No experience" … "Up to 7
years" · "Open to all") · Degree level · Role category · Employment type · Job
type · per-attribute include-unknowns toggles · sort **Match % / Newest** ·
result count · × Clear. (Degree/experience/role/employment filters bite fully
once Phase 2 extraction fills them; until then they filter the `unknown`
bucket honestly.)

**Save search** → banner: `Next auto-scan uses this search · hourly + 6
sweeps/day · last ran 20:58 · Scan now`. **Add your own link** card retained.

**Page weight:** embed top **800** jobs (~0.6 MB compact JSON), lazy card
render, footer "Showing 800 of 2,340 — refine filters or save a narrower
search". Full pool stays on disk. Page target < 4 MB.

---

## 6. Scheduler & flow

- **Tier 1 (hourly):** `watch.py` tick → if due and scan lock free →
  `scripts/ingest.py --tier 1` (minutes, no LLM) → next = +60 min ± 5 jitter →
  publish.
- **Tier 2 (≥6×/day):** slots every ~4h (`24h / tier2SweepsPerDay`, jitter).
  Each slot spawns the existing scan-agent path with the config serialized in,
  sweeping a **rotating channel pack** — e.g. A: LinkedIn+Indeed global ·
  B: India (Naukri, Instahyre, Cutshort, Hirist, Foundit, IIMJobs, apna) ·
  C: Europe/UK (SEEK-free set: StepStone, Reed*, Totaljobs, WTTJ, EURES,
  gov boards) · D: APAC (SEEK AU, JobStreet/JobsDB, Wanted KR, Saramin,
  JapanDev/TokyoDev, MyCareersFuture) · E: Middle East (Bayt, GulfTalent,
  Naukrigulf, Wuzzuf, Qureos) + AU/NZ overflow · F: startup/curation (Wellfound,
  YC, HN, Built In, Otta/WTTJ, hiring.cafe, VC boards). Rotation guarantees
  every pack ≥1×/day and hot packs 2×, without hammering any single site 6×.
  (*Reed moves to Tier 1b once its key exists.)
- Manual **Scan now** = existing `scan` queue → Tier 1 immediately + the next
  due pack.
- Failures and zero-yield channels surface via `meta.lastIngest`: *a scan that
  found nothing must look different from a scan that didn't run.*

**Budget check** (KV free tier: 100k reads / 1k writes / 1k lists per day):
poll reads unchanged ~28.8k/day (config + watch/pass arrays ride the same
key) · board publishes ≈ 24 (Tier 1) + 6 (sweeps) + user actions ≪ 1k writes ·
zero new keys or lists · Tier-1 HTTP is free. **Tier-2 at 6×/day is real token
money** — per-sweep usage capture (`claude -p --output-format json`) is now a
Phase-3 acceptance requirement, not a Phase-4 nicety, so the cost is on the
board within days of sweeps scaling up.

---

## 7. Phases

### Phase 1 — the Found lane, live end-to-end (MVP) — **build first**

**Outcome:** hourly, Jobby pulls fresh postings from the registry's ATS boards
+ remote feeds + the free-key aggregator APIs across all target regions,
resolves identities so nothing enters twice, scores against the saved search,
and the new first tab shows them with working filters, 👁/Pass/Apply/Details,
config save-and-rescan, and add-your-own-link.

**Scope**

1. `companies.json` registry + `scripts/seed_companies.py` (seed from
   `roles.json` + curated per-region packs, §Appendix A note).
2. `sources.json` + `references/sources-registry.md` (the catalog, with
   status accounting from day one).
3. `scripts/sources/` package: `greenhouse.py`, `lever.py`, `ashby.py`,
   `feeds.py` (RemoteOK, WWR, Remotive, Himalayas), `adzuna.py`, `jooble.py`,
   `careerjet.py`, `reed.py`, `usajobs.py`, `themuse.py` → one normalized
   shape; throttle; ETag caching; loud failure accounting. Keys read from
   `config.env` (`JOBBY_ADZUNA_ID/KEY`, `JOBBY_JOOBLE_KEY`, …); a connector
   with no key reports *skipped (no key)*, never silently absent.
4. `scripts/jobs_store.py`: upsert + **identity resolution** (§3.2: canonical
   URL, redirect resolution, alias match, simhash) + tombstones + pruning.
5. `scripts/match_score.py`: rubric v1 + gates + breakdown.
6. `scripts/ingest.py --tier 1`: fetch → resolve → gate → score → upsert →
   prune → `meta.lastIngest` → publish.
7. `watch.py`: hourly scheduler + config pickup + `watch`/`pass` queue
   handling (session creation on 👁).
8. `deploy/api/board.js`: `/api/search-config`, `/api/watch`, `/api/pass`
   (all RMW on the single `jobby:queue` key, idempotent per id).
9. `dashboard.py`: Found tab (grid, % ring, chips, Details, 👁/Pass/Apply),
   filter bar, saved-config banner, add-your-own-link, last-ingest header
   incl. channels-skipped, embed cap, optimistic UI + localStorage replay.

**Tests** (extend the 171): `test_sources.py` (per-connector fixtures →
normalization; no-key = visible skip; throttle) · `test_identity.py`
(cross-source trio → one record with `sources[3]`; simhash thresholds; alias
matching; redirect capping; ATS-beats-aggregator canonical) ·
`test_jobs_store.py` (tombstones, prune caps, state transitions) ·
`test_match_score.py` (weights sum, gates, unknown neutrality) ·
`test_ingest.py` (config gating incl. excludeKeywords/companiesMode/region
packs; lastIngest written on success *and* failure) · `test_watch.py`
additions (scheduler, config pickup, 👁 creates session once) ·
`test_dashboard.py` additions (Found render, embed cap, three themes,
escaping) · `kvqueue.test.mjs` additions (`searchConfig`/`watch`/`pass` RMW
never clobber other arrays — the `claim` bug's cousin).

**Acceptance**
- [ ] Fresh posting on a registry company's board appears in Found ≤ 70 min,
      scored, single record.
- [ ] The same JD seeded through three fixture sources produces **one** job
      with three sightings; live spot-check on a real syndicated posting.
- [ ] Jobs from ≥ 8 target countries present after first full day (Adzuna +
      Jooble reach), given keys installed.
- [ ] 👁 → role in Watching with a session, Apply works there; Pass →
      tombstoned, never reappears; Apply from Found → normal flow.
- [ ] Config save changes the *next* automatic ingest; banner shows version +
      next run; Scan now works.
- [ ] A connector outage or missing key is visible on the board within one
      cycle.
- [ ] KV ops within free tier; page < 4 MB at 800 embedded; CSP clean; three
      themes.
- [ ] Full suite green.

**Effort:** L. **Out of scope:** attribute extraction, industry, Tier-2
sweeps, notifications, auto-apply.

### Phase 2 — deep filters via attribute extraction

Heuristic extractors (fixture-corpus-tested) for required-experience years,
degree, employment type, role category; salary when present ("salary listed
only" toggle). Company→industry enrichment cached per company. Score v2.
Connectors: SmartRecruiters, Workable, Recruitee, BambooHR, Teamtailor,
Personio (all public per-company JSON). **Effort:** M.

### Phase 3 — full sweep catalog & the self-growing registry

Tier-2 packs per §6 live at ≥6/day. Full Appendix-A catalog connected or
status-marked. Per-company enterprise-ATS connectors (Workday CXS JSON, iCIMS
search JSON, SuccessFactors career-site JSON, Oracle Recruiting Cloud REST —
all public per-tenant, fiddlier shapes). **ATS resolver** turns every
discovered company into registry coverage. Liveness + phantom guard at ingest.
Language handling: batch title translation for `lang != en` scoring (capped,
cached). **Per-sweep cost capture is an acceptance requirement here.**
**Effort:** L. **Honesty:** hourly LinkedIn automation stays off the table;
auth walls land in Needs you exactly as today.

### Phase 4 — learning loop & ops

Pass/👁/Apply signals calibrate rubric weights (transparently — the "why"
panel shows the adjustment). Post-ingest digest ("41 new, 5 ≥ 80%") via the
notification channel from the earlier backlog. Liveness re-sweep expires dead
Found jobs. Cost per scan and per application on the board. **Optional,
default off, explicit decision:** auto-queue Apply above a threshold with a
daily cap and review window. **Effort:** M.

---

## 8. Phase gate — hard code review after every phase (required)

No phase is "done", and the next phase does not start, until its gate passes:

1. **Suite green:** `python3 -m pytest tests/ -q` (which runs the JS tests) on
   a clean checkout.
2. **Invariant audit** against a written checklist committed as part of the
   review: single-key KV discipline + ops/day math re-verified · watcher-only
   claiming untouched · one-browser-per-slot untouched · atomic writes
   (unique temp names) on every new writer · no silent-success paths (every
   failure is visible somewhere a human looks) · CSP + three-theme colours ·
   locks/races on anything concurrent · **PII scan** of new artifacts, logs,
   fixtures, and snapshots (the Playwright leak must never repeat) · no
   Sahil-specific constants in code (config/profile only — protects the
   open-source path).
3. **Independent review pass:** a *fresh* Claude session in reviewer mode
   (not the implementing session) reads the diff against this plan and the
   checklist, and writes `docs/reviews/phase-N.md` with findings graded
   **Blocking / Major / Minor** and explicit sign-off lines.
4. **Human sign-off:** Sahil approves the review doc. Blocking and Major
   findings are fixed (and re-reviewed) before the phase closes.
5. **Docs current:** `system-reference.md` (§components, §data model, §board,
   §known gaps) and the project copy updated to match what actually runs;
   sources-registry statuses refreshed.

---

## 9. Decisions / actions for Sahil

1. **API keys (action, unblocks Phase 1's international reach):** register
   free keys and drop into `~/.job-application/config.env` — Adzuna
   (developer.adzuna.com), Jooble (jooble.org/api/about), Careerjet
   (affiliate key), Reed (reed.co.uk/developers), USAJobs
   (developer.usajobs.gov). ~20 minutes total. Connectors run without them but
   will show *skipped (no key)*.
2. **Auto-apply above threshold:** Phase-4 option or hard no?
3. **Volume knobs:** confirm `minScoreToKeep: 40`, stored cap 3,000, embedded
   cap 800.
4. **Review sign-off:** confirm review docs live in `docs/reviews/` and that
   you are the sign-off on every gate.

Resolved in v2: sweep cadence (**≥6/day**, rotating packs) · 👁 semantics
(Found → Watching; Watching keeps its name and its honest fit reads) · region
scope (US · IN · CA · AU · EU · ME · JP · KR · SEA) · dedupe bar (one JD, one
record, sightings merged).

---

## Appendix A — Source registry (initial catalog)

Class: **A** open API/JSON (Tier 1a) · **B** free-key API (Tier 1b) ·
**C** feed/semi-structured (Tier 1a) · **D** agent sweep (Tier 2) ·
**E** auth-walled → user's signed-in slots only · **X** excluded (reason).
Statuses live in `sources.json`; "verify at build" = endpoint shape confirmed
during implementation.

| Region | Channel | Class | Notes |
|---|---|---|---|
| Global | Greenhouse / Lever / Ashby boards | A | per-company, registry-driven |
| Global | SmartRecruiters / Workable / Recruitee / BambooHR / Teamtailor / Personio | A | Phase 2 |
| Global | Workday CXS · iCIMS · SuccessFactors · Oracle ORC (per-tenant JSON) | A | Phase 3, fiddly shapes |
| Global | Adzuna | B | ~20 countries incl. US CA GB AU DE FR NL PL ES IT IN SG NZ BR MX ZA |
| Global | Jooble | B | 60+ countries incl. Gulf, JP, KR |
| Global | Careerjet | B | 60+ countries |
| Global | The Muse | B | curated US/EU |
| Global | RemoteOK · WeWorkRemotely · Remotive · Himalayas · Working Nomads | C | remote |
| Global | LinkedIn · Indeed · Glassdoor · Google Jobs · Monster · ZipRecruiter · SimplyHired · Talent.com | D | aggregator sweeps; origin-link resolution for dedupe |
| Global | Wellfound · YC workatastartup · HN Who's Hiring · Built In · Otta/WTTJ · hiring.cafe · VC portfolio boards | D | startup/curation pack |
| US | USAJobs | B | official API |
| US | Dice · CareerBuilder | D | |
| India | Naukri · Instahyre · Cutshort · Hirist · Foundit · IIMJobs · Shine · TimesJobs · apna | D | per `references/sourcing.md` |
| India | Zoho Recruit · Darwinbox · Keka · RippleHire boards | A/D | custom subdomains hide many from `site:` sweeps — registry-driven where known |
| Canada | Job Bank (gov) | C | feeds; verify at build |
| Canada | Eluta · Jobboom | D | |
| Australia/NZ | SEEK · Jora · CareerOne · Trade Me Jobs | D | SEEK has no public API |
| UK | Reed | B | free key |
| UK | Totaljobs · CV-Library · CWJobs | D | |
| Europe | EURES (EU-wide, gov) | C | API; verify at build |
| Germany | StepStone · Xing Jobs · Arbeitsagentur (gov API) · Berlin Startup Jobs · Honeypot | C/D | Arbeitsagentur = open API |
| France | Welcome to the Jungle · France Travail (gov API) · APEC | C/D | FT API needs free registration |
| Nordics | Platsbanken (SE, gov API) · Finn.no (NO) · Jobindex (DK) · Duunitori (FI) | C/D | Platsbanken = open API |
| Iberia/Italy | InfoJobs (ES) · Landing.jobs (PT) · InfoJobs.it | D | |
| CEE | Pracuj.pl · JustJoin.IT · NoFluffJobs · Jobs.cz · Profesia | C/D | tech boards have feeds |
| Middle East | Bayt · GulfTalent · Naukrigulf · Wuzzuf (EG) · Qureos · Akhtaboot (JO) | D | UAE · SA · QA · KW · BH · OM · JO · EG |
| Japan | Japan Dev · TokyoDev · Daijob · GaijinPot · Wantedly · Green | C/D | EN-friendly boards first; `lang: ja` handling Phase 3 |
| Japan | BizReach · Rikunabi NEXT · Doda | E | auth-walled |
| Korea | Wanted · Saramin · JobKorea · Programmers · RocketPunch | D | `lang: ko` handling Phase 3 |
| SEA | JobStreet · JobsDB (SEEK APAC) · Tech in Asia · NodeFlair (SG) · Glints · Kalibrr (PH) · VietnamWorks/TopCV (VN) | D | |
| SEA | MyCareersFuture (SG, gov) | C | public API; verify at build |
| — | FlexJobs · Hired · Handshake | X/E | paywall / auth+edu — documented exclusions |

