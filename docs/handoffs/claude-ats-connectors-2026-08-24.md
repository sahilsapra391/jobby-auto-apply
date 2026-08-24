# Claude handoff: ATS connector expansion, 2026-08-24

Work happened on branch `codex/ats-connectors` in the isolated worktree
`/Users/sahilsapra/Documents/Development.nosync/jobby-ats-connectors`. Do not
rebuild these connectors from the dirty live checkout.

## Added and live-verified

`scripts/sources/workday.py` connects Workday CXS using the public candidate
JSON POST. `scripts/sources/enterprise_boards.py` connects Pinpoint
`postings.json`, Teamtailor JSON Feed, JazzHR's server-rendered public listing,
Oracle Recruiting Cloud's candidate-experience requisition JSON, and Gem's
documented Job Board API.

The bounded live smoke test on 2026-08-24 returned normalized postings from all
six: Pinpoint 1, Teamtailor 3, JazzHR 3, Oracle 3, Gem 3 and Workday 3. Each used
one request and returned no connector error. The test used a temporary store
and did not read or write production KV.

A second live pagination check fetched all 40 jobs currently reported by
GEICO's Workday board in two requests and 250 Oracle jobs in two requests, with
zero errors. Unit tests also force Workday past its former 100-job cutoff and
force Oracle onto a second page. Both connectors retain a high safety ceiling
and report an error if a board ever exceeds it instead of truncating quietly.

## Failure contract

`sources/base.py` now opens a host circuit for the remainder of one sweep after
a network, 429 or 5xx response exhausts three attempts. A new sweep starts with
a closed circuit. A tenant 404 does not poison other tenants on the same host.
Every connector still returns `ok`, `skipped` or `error`; no exception becomes
an empty success. Existing stored sightings remain available during an outage.

This is resilience, not a promise that third-party servers cannot fail.

## Tenant discovery

`seed_companies.py` recognizes Pinpoint, Teamtailor, JazzHR, Gem and Oracle URLs
and preserves the exact Workday or Oracle host information their endpoints
need. Connector count alone does not add coverage. Each platform still needs
verified tenant URLs in `companies.json`.

## Tests and fixtures

Real-response-derived fixtures live in `tests/fixtures/{pinpoint,teamtailor,
jazzhr,oracle_orc,gem}.*`. `NewDirectBoardTests` pins normalization, skips,
outages, Workday POST routing, circuit opening and the 404 counter-case. The
final focused source, ingest and store run passed 275 tests. The full repository
suite passed 1,112 tests with one pre-existing environment skip.

Do not convert iCIMS to connected without credentials or a separately verified
public career-page contract. Its official Job Portal API requires Basic auth.

## Production registry repair

The first production scan after the connector merge skipped Workday, Pinpoint,
Teamtailor, JazzHR, Oracle Recruiting Cloud and Gem because connector code alone
does not create tenant records. Workable failed all five records because tenant
discovery had mistaken the literal `j` in `apply.workable.com/j/<job-id>` for a
company board slug.

Branch `codex/connector-registry-repair` fixes the producer contract in
`scripts/seed_companies.py`. Workable `/j/` URLs no longer yield a tenant,
existing invalid tokens are removed with an explicit unresolved status, and
live-verified pack records outrank learned records. The pack now includes one
exercised tenant for each formerly skipped connector. GEICO Workday and Oracle
CX_1 include the exact public origins required by those APIs.

The production-shaped live smoke used an isolated store copy and a cap of three
postings per connector. Workable, Workday, Pinpoint, Teamtailor, JazzHR, Oracle
and Gem each returned `ok`: 19 postings, seven requests, no skips, no errors.
Three matched search version 17; sixteen were visibly gated by age, keywords or
country. Regression coverage is in `CompanyRegistryTests`, including the exact
Workable poisoning path and seed repair. Do not replace this with a broader
guessing rule. A job URL that lacks a tenant remains unresolved.
