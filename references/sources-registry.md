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
| Adzuna, Jooble, Reed, USAJobs | 2026-08-20 | Keys installed during the supervised run. All four returned live results against the real connectors and normalised into job records. Status moved from `needs-key` to `connected`. Their fixtures remain contract-derived and still carry `__fixture_note`, which is now a statement about the fixture rather than about the channel. |
| Careerjet | 2026-08-20 | **Two corrections.** The public endpoint refuses TLS — `https://` returns connection refused on 443 — and answers on plain HTTP, so the affiliate key travels in the clear. It is an affiliate identifier rather than an account credential, but it is a key on the wire and that is recorded rather than papered over. It also rejects an undeclared caller: `403 {"error":"Undeclared referrer. Please add a Referer header so we know who is calling this API and from which page."}`. The connector now sends a Referer naming the project, on the same honest-identification principle as the User-Agent — not a browser string. |
| Adzuna origin links | 2026-08-20 | **Correction to plan §3.2**, which assumes an aggregator record "usually" carries an apply-on-the-company-site link. Adzuna's `redirect_url` is `adzuna.com/details/<id>` or `/land/ad/<id>?se=…&v=…`, with no employer URL embedded anywhere in the response. Identity rule 1 therefore never fires for an Adzuna sighting: its copies merge on company + normalised title + country, or on the description fingerprint, or not at all. Found by re-recording the fixture from a live response — the contract-derived fixture written before the key existed had invented an embedded link, and the test built on it was passing against fiction. |
| Keyed fixtures re-recorded | 2026-08-20 | Adzuna, Jooble, Careerjet, Reed and USAJobs fixtures were originally written from each published contract because no key was installed, and carried `__fixture_note: "not a recorded response"`. All five are now recorded from live authenticated responses and the note is gone. Reed's `dd/mm/yyyy` dates and USAJobs' nested `MatchedObjectDescriptor` both matched the contract; Adzuna's origin link did not. |
| Query fan-out | 2026-08-20 | The plan's nine regions expand to ~50 countries. Adzuna, Jooble and Careerjet each querying every country against every search term came to roughly 400 requests and blew a ten-minute timeout on the first supervised dry run — and at 24 runs a day would exhaust these channels' free quotas within hours. Each keyed channel now asks a rotating window of at most 8 country/locale queries per run, deterministic in the hour so a retry is not a fresh burst, and reports the slice it used. A day covers everything an hour cannot. |
| Direct ATS registry repair | 2026-08-24 | A production-shaped smoke test exposed five Workable records whose token was the literal path segment `j` from `apply.workable.com/j/<job-id>`. That path contains no tenant evidence. Discovery now rejects it, seed repair removes invalid tokens visibly, and verified seeds cannot be overwritten by learned records. Verified tenants were added for Workday, Pinpoint, Teamtailor, JazzHR, Oracle Recruiting Cloud and Gem. A bounded live run made seven requests, found 19 postings and reported zero skips and zero connector errors. |
| Posted-date provenance audit | 2026-08-24 | Every connected normalizer was checked field by field. Greenhouse uses `first_published`, Ashby `publishedAt`, SmartRecruiters `releasedDate`, Workable `published_on`, Recruitee `published_at`, Personio `createdAt`, Breezy `published_date`, Teamtailor `date_published`, Oracle `PostedDate`, Gem `first_published_at`, Lever `createdAt`, and Workday exact `postedOn` ages. Feed and search connectors retain only their source date fields. Greenhouse and Recruitee no longer fall back to edit timestamps. Workable no longer treats a draft's `created_at` as publication. Jooble's `updated` value is not relabelled as publication. Pinpoint, JazzHR, Rippling, and Jooble remain honestly undated when no publication field exists. Found never substitutes `firstSeenAt`. |

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
| Adzuna | `adzuna` | B | 1b | 1 | `connected` | US, CA, GB, AU, DE, FR, NL, PL, ES, IT, AT, BE, CH, SE, SG, NZ, BR, MX, ZA, IN | Verified live 2026-08-20. Covers 19 countries; a selected country outside that set is reported rather than dropped silently. Country x term queries capped per run and rotating. CORRECTION to plan §3.2, which assumes aggregators usually expose an 'apply on the company site' link: Adzuna's redirect_url is adzuna.com/details/<id> or /land/ad/<id>?se=...&v=... with nothing embedded, so identity rule 1 can never fire for an Adzuna sighting. Its copies merge on company+title+country or on the description fingerprint, or not at all. |
| Ashby job boards | `ashby` | A | 1a | 1 | `connected` | * | One request returns descriptionPlain, workplaceType and isRemote. isListed=false postings are skipped. A wrong token returns a plain-text 404, not JSON. |
| Breezy HR | `breezy` | A | 1a | 1 | `connected` | * | Verified live 2026-08-20. A bare array; location is an object carrying is_remote. The list has no description field at all, so its records are thin. |
| Careerjet | `careerjet` | B | 1b | 1 | `connected` | * | Verified live 2026-08-20, with TWO CORRECTIONS. The endpoint is plain HTTP: it refuses TLS outright (connection refused on 443) and answers on port 80, so the affiliate key travels in plaintext. And it rejects an undeclared caller with 403 and the body 'Undeclared referrer. Please add a Referer header so we know who is calling this API and from which page.' - so the connector declares itself with a Referer naming the project, which is the same honest-identification rule as the User-Agent. Locale x term queries capped and rotating. |
| Greenhouse job boards | `greenhouse` | A | 1a | 1 | `connected` | * | List is 59 KB for 83 jobs; content=true is 845 KB, so bodies are fetched per job at /jobs/{id} only for postings that survive the metadata gate. No ETag offered when checked. A wrong token is 404. |
| Himalayas | `himalayas` | C | 1a | 1 | `connected` | REMOTE | jobs[]; pubDate in epoch seconds. |
| Jooble | `jooble` | B | 1b | 1 | `connected` | * | Verified live 2026-08-20. POST with a JSON body; the key is in the URL path and never reaches an error line. Location x term queries capped and rotating. |
| Lever job boards | `lever` | A | 1a | 1 | `connected` | * | One request returns descriptions, workplaceType and a two-letter country. The only platform of the three that returned an ETag and answered a conditional request with 304. |
| Personio | `personio` | A | 1a | 1 | `connected` | * | Verified live 2026-08-20. XML, not JSON. Descriptions nest two levels down under jobDescriptions/jobDescription/value. The only platform of the six that states years of experience as its own field. |
| Recruitee | `recruitee` | A | 1a | 1 | `connected` | * | Verified live 2026-08-20. {offers:[...]}, HTML descriptions, country_code, and its own remote/hybrid/on_site flags. |
| Reed | `reed` | B | 1b | 1 | `connected` | GB | Verified live 2026-08-20. HTTP Basic with the key as username; dd/mm/yyyy dates. Only asked when the saved search includes the UK. |
| RemoteOK | `remoteok` | C | 1a | 1 | `connected` | REMOTE | Bare array whose FIRST element is a legal notice rather than a job. |
| Remotive | `remotive` | C | 1a | 1 | `connected` | REMOTE | jobs[] alongside two legal-notice keys. candidate_required_location is a restriction, not a location. |
| Rippling ATS | `rippling` | A | 1a | 1 | `connected` | * | Verified live 2026-08-20. A bare array with five keys, no description and no date, so its records are thin by construction. Lists one requisition once per location under a single URL, which canonical-URL matching folds correctly. |
| SmartRecruiters | `smartrecruiters` | A | 1a | 1 | `connected` | * | Verified live 2026-08-20. {content:[...]}. The postings list carries NO description, so these records score on their title and report experience as not stated. A detail fetch per posting would fix that and is a Phase 2 refinement. |
| SAP SuccessFactors | `successfactors` | A | 1a | 1 | `connected` | * | Career Site Builder serves RSS at /services/rss/job/ on the employer's own career hostname, which is also the token. Not JSON. NOT a complete board listing: it carries the most recent items, so the connector reports complete=False and its postings never feed the staleness sweep. 48 other candidate career hostnames were probed and run something else at the same address. |
| The Muse | `themuse` | B | 1b | 1 | `connected` | US, GB, DE | CORRECTION to Appendix A, which lists this as free-key: the public endpoint answers with no key at all. A key raises the rate limit rather than unlocking access. |
| USAJobs | `usajobs` | B | 1b | 1 | `connected` | US | Verified live 2026-08-20. Needs the key AND the registered email, which goes in the User-Agent header. |
| We Work Remotely | `weworkremotely` | C | 1a | 1 | `connected` | REMOTE | RSS, not JSON. Title is "Company: Role" and is the only place the company name appears. |
| Workable | `workable` | A | 1a | 1 | `connected` | * | Verified live 2026-08-20. {jobs:[...]}, country and city as separate fields, full descriptions. The richest of the six: experience extracts cleanly from its bodies. |
| Working Nomads | `workingnomads` | C | 1a | 1 | `connected` | REMOTE | Bare array; tags is a comma-separated string rather than a list. |
| BambooHR | `bamboohr` | A | 1a | 2 | `planned` | * | Official ATS jobs API is per-company but requires an authenticated caller with ATS access; it is not an anonymous discovery endpoint. Verified from BambooHR's Get Job Summaries reference 2026-08-23. |
| Factorial | `factorial` | A | 1a | 2 | `planned` | * | Per-tenant, European tech; shape unverified. **Verify at build.** |
| Freshteam | `freshteam` | A | 1a | 2 | `planned` | * | Per-tenant, Freshworks; shape unverified. **Verify at build.** |
| Gem | `gem` | A | 1a | 2 | `connected` | * | Verified live 2026-08-24 using Gem's documented anonymous Job Board API. Includes full plain description, offices, departments and first-published timestamp. |
| JazzHR | `jazzhr` | A | 1a | 2 | `connected` | * | Verified live 2026-08-24 against FocusKPI. Public server-rendered listing table supplies direct detail URLs and locations; list records are deliberately thin rather than issuing one request per job. |
| Pinpoint | `pinpoint` | A | 1a | 2 | `connected` | * | Verified live 2026-08-24 using Pinpoint's documented anonymous postings.json feed. Includes description, responsibilities, skills, workplace and employment type. |
| Teamtailor | `teamtailor` | A | 1a | 2 | `connected` | * | Verified live 2026-08-24. Anonymous JSON Feed 1.1 with full HTML description, exact publication timestamp and schema.org location data. |
| ADP Workforce Now | `adp` | B | 1a | 3 | `planned` | * | Per-tenant portal, North America. **Verify at build.** |
| Akhtaboot | `akhtaboot` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| APEC | `apec` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| apna | `apna` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Applied | `applied` | A | 1a | 3 | `planned` | * | Per-tenant, UK/Europe blind hiring; shape unverified. **Verify at build.** |
| Apploi | `apploi` | E | 1a | 3 | `blocked` | * | No public JSON board. **Verify at build.** |
| Arbeitsagentur (Germany) | `arbeitsagentur` | C | 1a | 3 | `planned` | DE | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Avature | `avature` | E | 1a | 3 | `blocked` | * | No public JSON board; JavaScript-rendered per-tenant portals. **Verify at build.** |
| Bayt | `bayt` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| Berlin Startup Jobs | `berlin_startup_jobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| BizReach | `bizreach` | E | 2 | 3 | `blocked` | JP | auth wall: account required to see listings |
| Built In | `builtin` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| CareerBuilder | `careerbuilder` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| CareerOne | `careerone` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| CEIPAL | `ceipal` | B | 1a | 3 | `planned` | * | Per-tenant; shape unverified. **Verify at build.** |
| ClearCompany | `clearcompany` | B | 1a | 3 | `planned` | * | Per-tenant. **Verify at build.** |
| Cornerstone OnDemand | `cornerstone` | B | 1a | 3 | `planned` | * | Per-tenant. Regulated sectors. **Verify at build.** |
| Cutshort | `cutshort` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| CV-Library | `cvlibrary` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| CWJobs | `cwjobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Daijob | `daijob` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Darwinbox boards | `darwinbox` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Ceridian Dayforce | `dayforce` | B | 1a | 3 | `planned` | * | Per-tenant, US/CA/AU. **Verify at build.** |
| Dice | `dice` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Doda | `doda` | E | 2 | 3 | `blocked` | JP | auth wall |
| Duunitori | `duunitori_fi` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Eluta | `eluta` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| EURES (EU-wide) | `eures` | C | 1a | 3 | `planned` | EU | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Finn.no | `finn_no` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Flatchr | `flatchr` | A | 1a | 3 | `planned` | * | Per-tenant, continental Europe; shape unverified. **Verify at build.** |
| Foundit | `foundit` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Fountain | `fountain` | E | 1a | 3 | `blocked` | * | No public JSON board. **Verify at build.** |
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
| iCIMS | `icims` | A | 1a | 3 | `planned` | * | The official Job Portal API requires Basic authentication. Public career pages remain a later tenant-specific parser rather than a falsely anonymous API connector. |
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
| Jobvite | `jobvite` | B | 1a | 3 | `planned` | * | Per-tenant. **Verify at build.** |
| Jora | `jora` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| JustJoin.IT | `justjoin` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Kalibrr | `kalibrr` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Keka Hire boards | `keka` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Landing.jobs | `landing_jobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| LinkedIn | `linkedin` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Loxo | `loxo` | E | 1a | 3 | `blocked` | * | Sourcing tool; no public job board. **Verify at build.** |
| Manatal | `manatal` | A | 1a | 3 | `planned` | * | Per-tenant, SEA/Europe/UAE; shape unverified. **Verify at build.** |
| Monster | `monster` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| MyCareersFuture (Singapore) | `mycareersfuture` | C | 1a | 3 | `planned` | SG | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Naukri | `naukri` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| Naukrigulf | `naukrigulf` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| NodeFlair | `nodeflair` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| NoFluffJobs | `nofluffjobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Oleeo | `oleeo` | E | 1a | 3 | `blocked` | * | No public JSON board. **Verify at build.** |
| Oracle Recruiting Cloud | `oracle_orc` | A | 1a | 3 | `connected` | * | Verified live 2026-08-24 against Oracle's CX_1 candidate site. Anonymous requisition list requires expand=requisitionList and finder-scoped site, limit and offset. |
| Otta | `otta` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| PageUp | `pageup` | B | 1a | 3 | `planned` | * | Per-tenant, dominant in Australia and APAC. **Verify at build.** |
| Paycom | `paycom` | B | 1a | 3 | `planned` | * | Per-tenant. **Verify at build.** |
| Paylocity | `paylocity` | B | 1a | 3 | `planned` | * | Per-tenant, US mid-market. **Verify at build.** |
| PeopleFluent | `peoplefluent` | E | 1a | 3 | `blocked` | * | No public JSON board. **Verify at build.** |
| PeopleStrong | `peoplestrong` | B | 1a | 3 | `planned` | * | Per-tenant, India and APAC; shape unverified. **Verify at build.** |
| Phenom People | `phenom` | E | 1a | 3 | `blocked` | * | No public JSON board; JavaScript-rendered career portals. **Verify at build.** |
| Platsbanken (Sweden) | `platsbanken` | C | 1a | 3 | `planned` | SE | Endpoint shape not confirmed; verify before connecting. **Verify at build.** |
| Pracuj.pl | `pracuj` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Profesia | `profesia` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Programmers | `programmers_kr` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| In-house corporate portals | `proprietary` | E | 1a | 3 | `blocked` | * | Google, Apple, Netflix, Meta and similar build their own. One bespoke connector per company; no leverage across them. **Verify at build.** |
| Qureos | `qureos` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| Rikunabi NEXT | `rikunabi` | E | 2 | 3 | `blocked` | JP | auth wall |
| RippleHire boards | `ripplehire` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| RocketPunch | `rocketpunch` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Sage People | `sagehr` | B | 1a | 3 | `planned` | * | Per-tenant; shape unverified. **Verify at build.** |
| Saramin | `saramin` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| SEEK | `seek` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Shine | `shine` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| SimplyHired | `simplyhired` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| StepStone | `stepstone` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Talent.com | `talent_com` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| TalentReef | `talentreef` | E | 1a | 3 | `blocked` | * | No public JSON board. **Verify at build.** |
| Tech in Asia | `techinasia` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| TimesJobs | `timesjobs` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| TokyoDev | `tokyodev` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| TopCV | `topcv` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Totaljobs | `totaljobs` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Trade Me Jobs | `trademe` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Trakstar Hire | `trakstar` | A | 1a | 3 | `planned` | * | Per-tenant; shape unverified. **Verify at build.** |
| Tribepad | `tribepad` | E | 1a | 3 | `blocked` | * | No public JSON board. **Verify at build.** |
| UKG Pro | `ukg` | B | 1a | 3 | `planned` | * | Per-tenant, high-volume healthcare. **Verify at build.** |
| VC portfolio boards | `vc_boards` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| VietnamWorks | `vietnamworks` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Wanted | `wanted_kr` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Wantedly | `wantedly` | D | 2 | 3 | `rotation` | AU, NZ, JP, KR, SG, MY, PH, ID, TH, VN | Tier-2 sweep, rotation pack D. |
| Wellfound | `wellfound` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| Workday CXS | `workday` | A | 1a | 3 | `connected` | * | Verified live 2026-08-24 against GEICO. Public candidate CXS JSON POST; exact host, tenant and career-site path come from a real posting URL. |
| Welcome to the Jungle | `wttj` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Wuzzuf | `wuzzuf` | D | 2 | 3 | `rotation` | AE, SA, QA, KW, BH, OM, JO, EG | Tier-2 sweep, rotation pack E. |
| XING Jobs | `xing` | D | 2 | 3 | `rotation` | EU | Tier-2 sweep, rotation pack C. |
| Y Combinator (workatastartup) | `ycombinator` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack F. |
| Zimyo | `zimyo` | B | 1a | 3 | `planned` | * | Per-tenant, India and UAE; shape unverified. **Verify at build.** |
| ZipRecruiter | `ziprecruiter` | D | 2 | 3 | `rotation` | * | Tier-2 sweep, rotation pack A. |
| Zoho Recruit boards | `zoho_recruit` | D | 2 | 3 | `rotation` | IN | Tier-2 sweep, rotation pack B. |
| FlexJobs | `flexjobs` | X | - | 0 | `excluded` | * | paywall: listings are behind a paid subscription |
| Handshake | `handshake` | X | - | 0 | `excluded` | * | auth wall: verified .edu enrolment required |
| Hired | `hired` | X | - | 0 | `excluded` | * | auth wall: candidate marketplace, account required |

<!-- generated:sources end -->
