# ATS coverage — what is actually reachable, and what moves the number

Input for Phase 2 and Phase 3 planning, written 2026-08-20 in response to a
request to "connect to all of these ATS systems and get jobs from every single
one of them" (a list of 50).

## The thing to understand first

**There is no such thing as crawling an ATS.** Every platform on the list is
*per-tenant*. Greenhouse's public API is:

    https://boards-api.greenhouse.io/v1/boards/{token}/jobs

There is no endpoint that returns all Greenhouse jobs, and the same is true of
Lever, Ashby, Workday, iCIMS, SuccessFactors, Darwinbox and the rest. The unit
of coverage is **the company**, not the platform.

Measured on a real sweep, 2026-08-20:

| | |
|---|---|
| Companies in the registry with a Greenhouse/Lever/Ashby token | 140 |
| Postings returned by one sweep of those | 17,632 |
| Of which from company ATS boards | 10,136 |
| Requests to get them | 323 |
| Time | 6.3 minutes |

Adding a connector for a platform Jobby has ten tenants for adds ten companies
of coverage. Adding a thousand company tokens to platforms Jobby already speaks
adds a thousand. **Registry size is the multiplier; connector count is not.**

That is why the plan calls `companies.json` the flywheel and puts an ATS
resolver in Phase 3. The resolver is the high-value item on this page.

## Triage of the 50

### Group A — public per-company JSON, no key. Phase 2.

Each is a small connector, the same shape as the three that exist. Worth doing,
and cheap, but only pays out to the extent the registry knows tenants for them.

SmartRecruiters · Workable · Recruitee · Personio · Teamtailor · BambooHR ·
JazzHR · Breezy HR · Zoho Recruit · Freshteam · Rippling ATS · Pinpoint · Gem

The plan already lists the first six as Phase 2. `roles.json` alone already
carries tokens for SmartRecruiters (3), Workable (2), Recruitee (1) and Zoho
(1), so the first run would add seven companies. That is the point about
registry size, in miniature.

Pinpoint, Teamtailor, JazzHR and Gem were connected and live-verified on
2026-08-24. Workday CXS moved from its prior live prototype into the same
isolated branch. Their tenant tokens still have to come from real career URLs.

### Group B — per-tenant enterprise JSON, fiddlier shapes. Phase 3.

Public but awkward: POST bodies, session cookies, per-tenant site paths,
pagination that differs per customer.

Workday CXS · Oracle Recruiting Cloud / Taleo · SAP SuccessFactors ·
iCIMS · Cornerstone · ADP · UKG Pro · Ceridian Dayforce · Paycom · Paylocity ·
PageUp · Darwinbox · Keka · RippleHire · PeopleStrong · Zimyo · Manatal ·
Factorial · Sage People · CEIPAL · Trakstar · ClearCompany · Jobvite

Darwinbox, Keka, RippleHire, PeopleStrong and Zimyo are the India and UAE
answer and matter disproportionately for those regions. `references/sourcing.md`
already records that their tenant subdomains are company-specific and **not
guessable**, so discovery is search-first — which is the resolver again.

Workday is the single biggest name here: 39% of the Fortune 500. It is also the
one whose tenants are most discoverable, because the hostname contains the
company (`geico.wd1.myworkdayjobs.com`). `roles.json` already yields 10 Workday
tenants for free.

### Group C — no public JSON, or auth-walled. Sweep or exclude.

Avature · Phenom People · Oleeo · Fountain · TalentReef · Apploi ·
PeopleFluent · Loxo · Applied · Tribepad · Flatchr

These render through JavaScript or sit behind an account. They belong to Tier-2
agent sweeps through the user's own signed-in browser, or to a documented
exclusion. Building HTTP connectors for them would produce empty results that
look like working connectors, which is the failure mode this system is built to
avoid.

### Group D — proprietary, one connector each.

Google, Apple, Netflix, Meta and similar build their own. Each is a bespoke
connector for one company. High value per company, no leverage across them.

## What this means for sequencing

1. **Phase 1 lands first.** It is what makes any of the above measurable: the
   registry, identity resolution, the score, the accounting, the lane.
2. **Grow the registry.** This is the largest single multiplier and needs no new
   connector at all. Every verified Greenhouse, Lever or Ashby token is a
   company covered for free, hourly, forever. Going from 140 to 1,000 companies
   is worth more than all of Group A combined.
3. **Phase 2 = Group A**, plus the attribute extraction already begun.
4. **Phase 3 = the ATS resolver, then Group B.** The resolver first, because
   Group B connectors without tenants are connectors with nothing to ask.
5. **Group C stays honest** — swept or excluded, with the reason recorded in
   `references/sources-registry.md`.

## What was not done, and why

The 2026-08-24 connector batch added Workday CXS, Pinpoint, Teamtailor, JazzHR,
Oracle Recruiting Cloud and Gem. Every connector passed fixture, missing-
registry and outage tests, then returned normalized postings from a live public
tenant using a temporary store. iCIMS stayed planned because its official Job
Portal API requires Basic authentication.
