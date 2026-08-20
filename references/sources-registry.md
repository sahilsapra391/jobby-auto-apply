# Source registry

Every channel Jobby can reach, and its live status. The catalog itself is
`sources.json`; the table below is generated from it, so the two cannot drift.

## Why this file exists

"We cover every source" is not something anyone can check by reading code. It
is only meaningful as **accounting**: every channel here is in exactly one of
six states, and a channel in none of them is a gap you can count rather than a
gap nobody noticed.

| Status | Meaning |
|---|---|
| `connected` | a Phase-1 connector fetches this on every ingest |
| `needs-key` | the connector exists, the API key is not installed; every ingest reports *skipped (no key)* rather than staying quiet |
| `planned` | catalogued, connector scheduled for the phase named |
| `rotation` | reached by Tier-2 agent sweeps on a rotating channel pack |
| `blocked` | reachable only through something Jobby refuses to do, with the reason recorded |
| `excluded` | structurally out of reach, with the reason recorded |

`blocked` and `excluded` must carry a reason. An unexplained exclusion is how
coverage gets quietly lost, so the loader refuses a catalog containing one.

## What "blocked" actually means here

Jobby does not solve CAPTCHAs, create accounts, type passwords or work around
paywalls, and that does not change because a channel is valuable. BizReach,
Rikunabi NEXT and Doda hold real Japanese roles behind an account wall;
FlexJobs is a paid subscription; Handshake requires verified university
enrolment. These are documented exclusions, not oversights. Where the user has
their own signed-in session, an auth-walled channel can be swept through their
own browser slot in Tier 2 — that is the user acting, not Jobby impersonating
them.

## Verification evidence

Endpoint shapes marked **Verify at build** were catalogued from the plan and
have not been confirmed against a live response. They carry no endpoint claim
in `sources.json` until they are.

| What | When | Evidence |
|---|---|---|
| Greenhouse, Lever and Ashby board tokens for the 96-company seed pack | 2026-08-19 | Each token fetched once against the platform's public board API; 96 of 104 candidates returned a parseable board and were kept, the other 8 were dropped rather than shipped unverified. |
| Greenhouse payload cost | 2026-08-19 | `boards-api.greenhouse.io/v1/boards/vercel/jobs` returned 58,735 bytes for 83 jobs; the same URL with `content=true` returned 845,251 bytes. Fourteen times the traffic every hour, so bodies are fetched per job at `/jobs/{id}` and only for postings that survive the metadata gate. `internal_job_id` 404s on that route; `id` is the one that works. |
| Conditional requests | 2026-08-19 | Lever returned `ETag: W/"25986-..."` and answered `If-None-Match` with a 304 and no body. Greenhouse, Ashby and The Muse returned no validator at all from this edge, having returned one from another edge minutes earlier. Conditional headers are therefore sent only when a validator was actually stored; sending `If-None-Match: None` raises inside `http.client` with a message naming neither the header nor the URL. |
| Dead board tokens | 2026-08-19 | A nonexistent token returns 404 on all three ATS platforms: Greenhouse `{"status":404,"error":"Job not found"}`, Lever `{"ok":false,"error":"Document not found"}`, Ashby plain-text `Not Found`. A 404 is a wrong token rather than an outage and is counted separately. |
| The Muse authentication | 2026-08-19 | `https://www.themuse.com/api/public/jobs?page=0` returned 200 with 20 results and no key supplied. Appendix A lists The Muse as class B free-key; **this is a correction** — the key raises the rate limit rather than unlocking access, so the connector runs either way and reports which. |
| Adzuna, Jooble, Careerjet, Reed, USAJobs | not verified | No key for any of these is installed in `~/.job-application/config.env`, so none has ever had a live response here. Their connectors are written to each published contract and their fixtures are labelled `__fixture_note: "not a recorded response"`. Status is `needs-key`; the first run with a key installed is what confirms the shapes. |

## The alias list is load-bearing

Cross-source dedupe matches a company by name before it ever looks at a
description: rule 4 of identity resolution only compares fingerprints *within a
company bucket*, because comparing every posting against every other one would
merge unrelated companies that happen to share boilerplate.

That makes `companies.json`'s `aliases[]` load-bearing. `norm_company` strips
legal furniture — "Datadog, Inc." meets "Datadog" — but it cannot bridge two
genuinely different strings. If an ATS says "Northwind" and an aggregator says
"Northwind Analytics Inc.", and no alias connects them, they stay two records.

**A missing alias shows up as a duplicate, never as an error.** Nothing warns.
This is deliberate: widening the company match risks merging two real companies
into one, which is the worse failure and much harder to notice.

Seeding aliases from company-name variants observed across sources is a
sensible **Phase 2** candidate. It is not built, and should not be built as
part of Phase 1.

## The catalog

<!-- generated:sources begin -->

| Channel | id | Class | Tier | Phase | Status | Regions | Notes |
|---|---|---|---|---|---|---|---|
| Adzuna | `adzuna` | B | 1b | 1 | `needs-key` | US, CA, GB, AU, DE, FR, NL, PL, ES, IT, AT, BE, CH, SE, SG, NZ, BR, MX, ZA, IN | Endpoint shape NOT verified live: no key installed. Written to the published contract; the first real run confirms it. Covers 19 countries. **Verify at build.** |
| Ashby job boards | `ashby` | A | 1a | 1 | `connected` | * | One request returns descriptionPlain, workplaceType and isRemote. isListed=false postings are skipped. A wrong token returns a plain-text 404, not JSON. |
| Careerjet | `careerjet` | B | 1b | 1 | `needs-key` | * | Endpoint shape NOT verified live: no key installed. Addressed by locale rather than country code. **Verify at build.** |
| Greenhouse job boards | `greenhouse` | A | 1a | 1 | `connected` | * | List is 59 KB for 83 jobs; content=true is 845 KB, so bodies are fetched per job at /jobs/{id} only for postings that survive the metadata gate. No ETag offered when checked. A wrong token is 404. |
| Himalayas | `himalayas` | C | 1a | 1 | `connected` | REMOTE | jobs[]; pubDate in epoch seconds. |
| Jooble | `jooble` | B | 1b | 1 | `needs-key` | * | Endpoint shape NOT verified live: no key installed. POST with a JSON body; the key is in the URL path, so it must never reach an error line. **Verify at build.** |
| Lever job boards | `lever` | A | 1a | 1 | `connected` | * | One request returns descriptions, workplaceType and a two-letter country. The only platform of the three that returned an ETag and answered a conditional request with 304. |
| Reed | `reed` | B | 1b | 1 | `needs-key` | GB | Endpoint shape NOT verified live: no key installed. HTTP Basic with the key as username; dd/mm/yyyy dates. Only asked when the search selects the UK. **Verify at build.** |
| RemoteOK | `remoteok` | C | 1a | 1 | `connected` | REMOTE | Bare array whose FIRST element is a legal notice rather than a job. |
| Remotive | `remotive` | C | 1a | 1 | `connected` | REMOTE | jobs[] alongside two legal-notice keys. candidate_required_location is a restriction, not a location. |
| The Muse | `themuse` | B | 1b | 1 | `connected` | US, GB, DE | CORRECTION to Appendix A, which lists this as free-key: the public endpoint answers with no key at all. A key raises the rate limit rather than unlocking access. |
| USAJobs | `usajobs` | B | 1b | 1 | `needs-key` | US | Endpoint shape NOT verified live: no key installed. Needs the key AND the registered email, which goes in the User-Agent header. **Verify at build.** |
| We Work Remotely | `weworkremotely` | C | 1a | 1 | `connected` | REMOTE | RSS, not JSON. Title is "Company: Role" and is the only place the company name appears. |
| Working Nomads | `workingnomads` | C | 1a | 1 | `connected` | REMOTE | Bare array; tags is a comma-separated string rather than a list. |
| BambooHR | `bamboohr` | A | 1a | 2 | `planned` | * | Public per-company JSON. |
| Personio | `personio` | A | 1a | 2 | `planned` | * | Public per-company JSON. |
| Recruitee | `recruitee` | A | 1a | 2 | `planned` | * | Public per-company JSON. |
| SmartRecruiters | `smartrecruiters` | A | 1a | 2 | `planned` | * | Public per-company JSON. |
| Teamtailor | `teamtailor` | A | 1a | 2 | `planned` | * | Public per-company JSON. |
| Workable | `workable` | A | 1a | 2 | `planned` | * | Public per-company JSON. |
| Akhtaboot | `akhtaboot` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| APEC | `apec` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| apna | `apna` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Arbeitsagentur (Germany) | `arbeitsagentur` | C | 1a | 3 | `planned` | DE | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Bayt | `bayt` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| Berlin Startup Jobs | `berlin_startup_jobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| BizReach | `bizreach` | E | 2 | 3 | `blocked` | JP | auth wall: account required to see listings |
| Built In | `builtin` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| CareerBuilder | `careerbuilder` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| CareerOne | `careerone` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Cutshort | `cutshort` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| CV-Library | `cvlibrary` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| CWJobs | `cwjobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Daijob | `daijob` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Darwinbox boards | `darwinbox` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Dice | `dice` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Doda | `doda` | E | 2 | 3 | `blocked` | JP | auth wall |
| Duunitori | `duunitori_fi` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Eluta | `eluta` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| EURES (EU-wide) | `eures` | C | 1a | 3 | `planned` | EU | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Finn.no | `finn_no` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Foundit | `foundit` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| France Travail | `france_travail` | C | 1a | 3 | `planned` | FR | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| GaijinPot | `gaijinpot` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Glassdoor | `glassdoor` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Glints | `glints` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Google Jobs | `googlejobs` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Green | `green_jp` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| GulfTalent | `gulftalent` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| hiring.cafe | `hiring_cafe` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| Hirist | `hirist` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| HN Who's Hiring | `hn_whoishiring` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| Honeypot | `honeypot` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| iCIMS | `icims` | A | 1a | 3 | `planned` | * | Public per-tenant JSON; fiddlier shapes. |
| IIMJobs | `iimjobs` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Indeed | `indeed` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| InfoJobs (Spain) | `infojobs_es` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| InfoJobs (Italy) | `infojobs_it` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Instahyre | `instahyre` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Japan Dev | `japandev` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Job Bank (Canada) | `jobbank_ca` | C | 1a | 3 | `planned` | CA | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Jobboom | `jobboom` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| Jobindex | `jobindex_dk` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| JobKorea | `jobkorea` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Jobs.cz | `jobs_cz` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| JobsDB | `jobsdb` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| JobStreet | `jobstreet` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Jora | `jora` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| JustJoin.IT | `justjoin` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Kalibrr | `kalibrr` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Keka Hire boards | `keka` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Landing.jobs | `landing_jobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| LinkedIn | `linkedin` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Monster | `monster` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| MyCareersFuture (Singapore) | `mycareersfuture` | C | 1a | 3 | `planned` | SG | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Naukri | `naukri` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Naukrigulf | `naukrigulf` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| NodeFlair | `nodeflair` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| NoFluffJobs | `nofluffjobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Oracle Recruiting Cloud | `oracle_orc` | A | 1a | 3 | `planned` | * | Public per-tenant JSON; fiddlier shapes. |
| Otta | `otta` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| Platsbanken (Sweden) | `platsbanken` | C | 1a | 3 | `planned` | SE | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Pracuj.pl | `pracuj` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Profesia | `profesia` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Programmers | `programmers_kr` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Qureos | `qureos` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| Rikunabi NEXT | `rikunabi` | E | 2 | 3 | `blocked` | JP | auth wall |
| RippleHire boards | `ripplehire` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| RocketPunch | `rocketpunch` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Saramin | `saramin` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| SEEK | `seek` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Shine | `shine` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| SimplyHired | `simplyhired` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| StepStone | `stepstone` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| SAP SuccessFactors | `successfactors` | A | 1a | 3 | `planned` | * | Public per-tenant JSON; fiddlier shapes. |
| Talent.com | `talent_com` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Tech in Asia | `techinasia` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| TimesJobs | `timesjobs` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| TokyoDev | `tokyodev` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| TopCV | `topcv` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Totaljobs | `totaljobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Trade Me Jobs | `trademe` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| VC portfolio boards | `vc_boards` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| VietnamWorks | `vietnamworks` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Wanted | `wanted_kr` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Wantedly | `wantedly` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Wellfound | `wellfound` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| Workday CXS | `workday` | A | 1a | 3 | `planned` | * | Public per-tenant JSON; fiddlier shapes. |
| Welcome to the Jungle | `wttj` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Wuzzuf | `wuzzuf` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| XING Jobs | `xing` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Y Combinator (workatastartup) | `ycombinator` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| ZipRecruiter | `ziprecruiter` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Zoho Recruit boards | `zoho_recruit` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| FlexJobs | `flexjobs` | X | - | 0 | `excluded` | * | paywall: listings are behind a paid subscription |
| Handshake | `handshake` | X | - | 0 | `excluded` | * | auth wall: verified .edu enrolment required |
| Hired | `hired` | X | - | 0 | `excluded` | * | auth wall: candidate marketplace, account required |

<!-- generated:sources end -->
