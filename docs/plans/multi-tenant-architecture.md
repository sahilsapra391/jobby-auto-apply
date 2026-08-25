# Multi-tenant Jobby: architecture plan

Status: **plan only.** Nothing here is implemented. Written 2026-08-25 against
`origin/main` at 76b9e2a, by the Claude session that held the design brief,
with findings contributed by two peer sessions working the same checkout.

This document exists to be argued with. Several of its load-bearing pieces are
decisions for Sahil rather than conclusions from the code, and those are marked
as open rather than folded quietly into the design.

## What this assumes, and what it does not

The plan assumes Jobby becomes usable by people other than its author. It does
not assume that means a hosted service. That distinction runs through the whole
document, so §2 states which parts survive either answer.

Three things are already decided and are treated as fixed:

- All storage is Cloudflare. D1, R2, Workers, Queues.
- Auth is self-rolled on the Skeptic pattern rather than a third-party vendor.
- Agent orchestration stays on the user's own machine. This predates the plan
  and is in `CLAUDE.md`; nothing here moves it.

## 1. The provider terms finding, which comes first because it constrains everything

Jobby's product direction now rests on each user bringing their own AI
subscription. That assumption is the most legally fragile part of the design and
two of three providers have moved against it within the last six months. This
section is the verified position as of 2026-08-25. **Re-check it before it
becomes a pricing page.**

### Anthropic: permitted, narrowly, with conditions Jobby does not yet meet

Anthropic's [Claude Code legal and compliance
page](https://code.claude.com/docs/en/legal-and-compliance) addresses this exact
architecture. It prohibits third-party developers from offering Claude.ai login
inside their own applications, from routing requests through Free, Pro or Max
plan credentials on their users' behalf, and from collecting, storing or
intermediating Claude.ai credentials or session tokens. Sign-in must complete
through Anthropic's own flow.

It then carves out the case Jobby is in: it does not prevent an end user signing
in to the unmodified Claude Code binary with their own subscription, including
where a platform hosts Claude Code. That carve-out carries conditions. The
binary must not be modified. The platform may not pay for, resell or
intermediate Claude usage on end users' behalf, and each end user must
authenticate with their own credentials, billed to them. Offering Claude Code
in your product requires agreeing to Anthropic's Commercial Terms of Service.

Jobby appears to sit inside the carve-out, and the CLI-login change committed as
`947ee51` is what puts it there: sign-in happens through Anthropic's own flow
inside the CLI, nothing is prompted for, nothing is stored. That was already the
right architecture for security reasons. It turns out to also be the only
permitted one.

Three risks that are not resolved:

1. **The Commercial Terms have not been agreed.** That is a signature, not a
   code change, and it is a precondition rather than a nicety.
2. **Usage character.** The same page says advertised Pro and Max limits assume
   "ordinary, individual usage of Claude Code and the Agent SDK". An agent
   firing job applications all day is arguably not ordinary individual usage,
   and that clause is where enforcement would land if it came.
3. **Claude Code as a generic runner.** The carve-out is written about Claude
   Code being used as Claude Code. Jobby invokes it as a provider runner for a
   non-coding task. Defensible, not obvious.

There is also tension with the [Consumer
Terms](https://www.anthropic.com/legal/consumer-terms) themselves, whose §3
permits automated access only via an Anthropic API key. The Claude Code page is
more specific and more recent so it governs, but the tension is real enough to
warrant a lawyer rather than a confident engineer. Anthropic reserves the right
to enforce without notice, and did so on 2026-04-04 when it blocked third-party
tools from Pro and Max.

### Google: closed for individuals

Verified. Google stopped serving Gemini CLI requests for free, AI Pro and AI
Ultra accounts on 2026-06-18. Only Gemini Code Assist Standard or Enterprise,
Code Assist for GitHub via Google Cloud, or a paid API key still work. See the
[consumer deprecation
notice](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)
and [gemini-cli discussion
#28017](https://github.com/google-gemini/gemini-cli/discussions/28017).

The migration path is Antigravity CLI (`agy`), which a peer session installed
with explicit authorisation and confirmed authenticates a personal account and
serves a real request with no API key. The binary, the model id (which now needs
an effort suffix) and the probe format all change. The fail-closed model
telemetry contract does not.

### OpenAI: unverified, and must not be assumed

Both `openai.com/policies/usage-policies` and the terms of use returned HTTP 403
to automated fetching. Secondary sources describe subscription access as
intended for individual productivity rather than powering commercial services,
with the API as the production track, but secondhand policy has no place in this
decision. **Someone must read both pages in a browser before OpenAI-via-Codex
appears in any user-facing claim.**

### What this implies for the design

The provider layer must treat bring-your-own-API-key and
bring-your-own-subscription as equally first-class, so the product survives
either being closed off. Removing Jobby-held key storage was correct and should
not be undone; "the user configures their own key in their own environment" is a
different thing from Jobby holding it, and is explicitly permitted by the same
Anthropic page.

## 2. The open decision: hosted or self-hosted

The rest of this plan describes a hosted multi-tenant service. That has not been
chosen, and the alternative is stronger than it first looks.

**Hosted** reaches everyone, including people who will never open a terminal. It
is a real company. It also makes Sahil a data processor holding several hundred
people's resumes, work-authorisation status and compensation expectations, with
deletion obligations, export obligations, retention limits and breach exposure.
And per §1, it is the shape that attracts the most attention from provider terms,
because a platform orchestrating many users' consumer subscriptions is the
specific thing those clauses were written about.

**Self-hosted open source** has none of the data-processor burden, reaches
technical users immediately, and sits far closer to "ordinary, individual usage"
because each person is running their own copy against their own subscription for
their own job search. It reaches a much smaller audience and is not a business
without something built on top.

The honest read is that §1 pushes toward self-hosted harder than product
ambition pushes toward hosted. This should be decided deliberately, before
anything in §5 onward is built, because the two paths diverge at the data model:
self-hosted needs no `users` table at all.

### What is worth doing under either answer

Everything in §3 (the corpus/overlay split), §4 (identity versus history) and
§7's retention policy improves the single-tenant system on its own merits. None
of it requires a decision on hosting. That is deliberate: it is the low-risk
work, it retires most of the plan's risk, and it can start immediately.

Everything from §5 (data model) onward assumes hosted multi-tenancy and should
not start until §2 is answered.

## 3. The corpus split: shared discovery, per-user overlay

### Where the line is today

`ingest.py` fetches postings and gates them against one user's search config in
the same loop. The order in `run()` is: gate → tombstone check → already_decided
→ score → `identity.resolve` → `jobs_store.upsert`. `add_postings()` does the
same, gating at the top before anything is stored. `match_score.score()` and
`configVersion` are written *into the shared job record* before upsert.

So the stored corpus is already one person's, filtered and scored to their
taste. This is not a seam that exists; it is a loop that has to be reordered.

### Where the line belongs

**Stage A, shared.** Fetch, normalise, `parse_location`, company discovery via
`seed_companies.from_postings`, posted-date refresh, `identity.resolve`, upsert
into a global `jobs` table. No user context anywhere. Connectors run against a
union scope built from coarse dimensions only: the set of countries anyone
searches, the union of tracked ATS tenants, a posted-within ceiling.

**Stage B, per-user.** `gate()` and `match_score.score()` over jobs touched since
that account was last matched, writing a `user_jobs` overlay row. This stays
cheap because `match_score` is a deterministic rubric that runs no LLM per job,
by design and by its own docstring. Fanning out to a thousand users is CPU, not
inference spend.

Two pieces already sit on the correct side of the line and come for free:
`seed_companies.from_postings` and `jobs_store.refresh_posted_date` both run
before the gate today.

### The eye-only rule, enforced structurally

Sahil's rule is that a scan may only add to Found, and the eye is the only route
into Watching. In the split model, Stage B may only INSERT `user_jobs` rows in
state `new`, and has no write permission on `watched` at all. Enforce that as a
database constraint, not a convention. This is the same pattern that has proved
right elsewhere in Jobby: make it structural and it stops depending on anyone
remembering.

### Multi-region correctness

`parse_stamp` in `jobs_store.py` was truncating stamps to twenty characters and
reading an offset stamp as midnight UTC, silently up to a day out, in the
function that the gate, the prune and the sort all depend on. Fixed 2026-08-25.
A multi-tenant corpus draws from connectors in more than one region by
definition, so that function has to stay right. Any change to it should be
treated as touching the date contract.

## 4. Identity is shared, history is per-user

`seen.py` matches on application id, URL with tracking stripped, ATS job id, and
company plus role. Share the corpus without touching this and Jobby tells user B
a posting is already applied to because user A applied.

Three of those four strategies are identity questions. The fourth, application
id, is history wearing an identity costume. `known_records()` currently flattens
`roles.json`, the sessions directory and `applications.jsonl` into one set of
match keys, so it structurally cannot distinguish "this posting exists" from
"this account acted on it".

The split: `known_records()` becomes two functions rather than one with a
filter. Identity resolution stays shared and every account benefits from every
other account's resolution. History becomes a lookup on that user's overlay row.
The store guard in `store.py cmd_session_save` has to take a user before it can
refuse anything.

`passed.json` stops being a file to port. Tombstones become a state on the
user's overlay row.

## 5. Corpus retention, which does not exist today

This is the consequence of §3 that is easiest to miss and most likely to be
found in production.

The gate currently does double duty. It is a relevance filter, and it is the
volume control on the stored pool. `jobs_store.STORED_CAP = 3000` and
`NEW_MAX_AGE_DAYS = 30` are sized for a corpus already narrowed to one search
config. Move `identity.resolve`/`upsert` above the gate and the stored pool
becomes everything fetched across the union scope, which is a different order of
magnitude, and the cap silently begins evicting jobs no user has been offered
yet.

`jobs_store`'s rule that only `new` jobs are pruned also has to be restated,
because `new` becomes a per-user state. The shared version is: **a corpus job is
prunable only when no user has acted on it.** That is a join, not a field read.

So the split needs two retention policies where there is currently one:

- **Corpus retention**, on the shared `jobs` table, driven by posting liveness
  and age, with a cap sized for the union scope rather than one search.
- **Overlay retention**, on `user_jobs`, which is the existing rule and can stay
  as it is.

Neither exists yet. Sizing the corpus cap requires measuring the union scope's
real volume, which nobody has done.

## 6. Trust boundary: three data classes

The single most important decision in the storage design is that not all of a
user's data deserves the same treatment.

**Class 0, never leaves the device.** Provider credentials, CLI session tokens,
Chrome profile cookies, any ATS login. These stay in owner-only local files, as
`provider_secrets.py` already does it. The cloud stores one row per connection:
provider, model, status, last-checked timestamp. Nothing else, ever. Per §1 this
is now a terms requirement as well as a security one.

**Class 1, R2, encrypted at rest by the platform, plaintext to the API.**
Uploaded resumes, generated resume and cover-letter HTML and PDF, cached JD
snapshots, confirmation screenshots. Private bucket, no public access, served
only through short-lived presigned GETs minted after an authz check.

**Class 2, field-level envelope encryption.** The answers `store.py` already
tags sensitive: compensation expectations, work authorisation, EEO and
demographics, date of birth. Per-user data key wrapped by a platform key,
unwrapped only when a device runner leases an application that needs the field.
Never rendered into board HTML.

Client-side encrypting Class 1 is rejected deliberately. It breaks web preview,
kills mobile access, and makes "I forgot my password" mean "your resumes are
gone forever". That is buying an exotic threat model with the product's
usability. A resume is a document people deliberately send to strangers; an API
key is a bearer credential that can spend money. Treating them identically makes
the product worse without making anyone safer.

## 7. Three planes

- **Corpus plane**, shared, one instance. Job discovery. Knows nothing about any
  user.
- **Account plane**, cloud, per user. Identity, profile, questionnaire, per-job
  state, application records, documents.
- **Execution plane**, the user's own machine, per user. Provider credentials,
  every LLM call, PDF rendering, Chrome form filling, submission.

The boundary between the second and third already exists in prototype. The
encrypted document-job transport in `document_job.py` puts ciphertext and fixed
status metadata in KV while every piece of real work happens locally. The
multi-tenant version is the same lease, the same crash recovery, the same
bounded payload, the same result validation, with a `user_id` on the envelope
and server-side authz on the lease.

## 8. Data model

Assumes hosted (see §2).

```sql
users(id, email, created_at, status, deleted_at)
devices(id, user_id, name, pubkey, paired_at, last_seen_at, revoked_at)
profiles(user_id, doc_json, updated_at)
answers(user_id, key, value, state, sensitive, updated_at)
search_configs(user_id, doc_json, version)
provider_status(user_id, provider, model, status, checked_at)

companies(id, name, domain, logo_url, ...)              -- SHARED
jobs(id, fingerprint, company_id, title, url, posted_at,
     body_ref, source, first_seen_at, updated_at)        -- SHARED

user_jobs(user_id, job_id, state, score_total, score_json,
          gated_reason, decided_at)                      -- OVERLAY
  UNIQUE(user_id, job_id)

applications(id, user_id, job_id, status, created_at, submitted_at)
app_events(application_id, event, at, nonce_hash)
artifacts(id, application_id, kind, r2_key, sha256, bytes)
work_items(id, user_id, kind, payload_ct, state, lease_owner,
           lease_expires_at, attempts)
```

R2 layout: `u/{userId}/src/{sha}.pdf` for uploads,
`u/{userId}/app/{applicationId}/{resume|cover}.{html|pdf}` for generated work.
User id in the prefix so a deletion request is one prefix sweep.

D1 handles this scale. If it stops being enough the same schema lifts to
Postgres, so it is not a one-way door. Stage B fan-out should run through Queues
rather than one long Worker invocation, to stay inside CPU limits.

The KV whole-board snapshot does not survive. It is single-tenant by
construction and it is a whole-board blob, so it does not degrade gracefully:
one account's growth becomes every account's payload. Serve per-account from D1.

### No single global version stamp

Learned the expensive way on 2026-08-25, and it generalises directly to
multi-tenant. PR #17 added a fingerprint gate that refused to render on any hash
mismatch between the state and the renderer that built it. There is one
`jobby:board-state` key and many live deployments: main at `cb31a2b1`,
`codex/soft-premium-ui` at `6b40f653`, `codex/hierarchical-location-picker` at
`bb6975fe`, `codex/ats-connectors` at `93d37aa8`. A laptop can only stamp the
branch it is standing on, so the gate 503'd every preview of every
renderer-touching PR, permanently. PR #19 moved it: drift is measured before the
render and acted on only after one actually failed, and both hashes ride out as
response headers.

**Do not design multi-tenant state around a single global fingerprint agreeing
everywhere.** Multi-tenant makes this strictly worse, because there will be many
renderer versions and many accounts' state in flight at once. Version state
per record, degrade rather than refuse, and report the mismatch instead of
failing on it.

## 9. Auth: the Skeptic pattern

Ported from `skeptic/backend/app/auth/`, which is the reference implementation
Sahil asked for.

What carries over: argon2id hashing with plaintext never persisted anywhere;
opaque random session tokens where only the SHA-256 digest is stored, so a
leaked table replays nothing; 30-day session TTL on an httpOnly, secure,
samesite=lax cookie; uniform login failures with no account-existence oracle;
email verification on a shorter TTL; Turnstile gating signup; and layered rate
limits (per-IP sliding window, a second per-account window on login so a
distributed guesser cannot spread across IPs, and a global daily signup cap as a
backstop, because `x-forwarded-for` is caller-controlled).

Worth stealing specifically: Skeptic's `claim_run_ids`, which re-parents a
device's pre-account runs onto the account at signup. For Jobby that means
someone can run a scan and see real matching jobs before making an account, then
claim that scan when they sign up. That is a funnel rather than a cliff.

One friction point. Skeptic runs argon2id via passlib in Python at m=64MiB.
Workers cap at 128MB memory, so 64MiB argon2 in a Worker is tight. Options:
argon2id via WASM at a reduced memory parameter (recommended, with the rate
limits carrying the load), or running the auth path in a Cloudflare Container
where Skeptic's module could be reused nearly verbatim.

Device pairing is separate from user login and must not reuse it. The user
clicks "Add this Mac", the web app shows a short code, the runner generates a
keypair and posts its public key with the code, the cloud binds device to
account and issues a rotating scoped credential good for exactly one `user_id`.
Revocable from Settings.

## 10. Onboarding

The obvious flow breaks at step two: parsing an uploaded resume wants an LLM,
but the user has not connected a provider, and will not install a runner before
seeing the product do anything.

Resolution: deterministic parse in the cloud first. Text extraction plus
existing heuristics gets name, email, links, employers and dates at roughly
eighty percent. The user corrects the rest in the questionnaire they were going
to fill in anyway. LLM enrichment happens later, on device, once their provider
is live.

So: sign up, upload resume, confirm parsed fields, answer the questionnaire
(reusing the existing confirmed/inferred/missing/sensitive confidence model,
with per-field consent for sensitive ones), set search config, land on a Found
board that already has matches because Stage B backfilled on config save.
Installing the runner is step six, at the point where they want to apply to
something.

Work authorisation is an any-country topic rather than per-country answers.
That shape is already correct for multi-tenant and should not be re-specialised.

## 11. The device runner

The product is the web app. The device piece is one command.

Today the runner is a git checkout, three launchd plists and a hardcoded
interpreter path. The shipped form is `npx jobby connect`, which takes a pairing
code and installs a single launchd agent. The three internal lanes stay as they
are; the user sees one service.

Its loop: authenticate with the device credential, long-poll for a lease,
decrypt the envelope, do the work with the user's own subscription, push
artifacts to R2 by presigned PUT, report completion. Same nonce discipline on
submission, same `jobby_` prefix, same visible-confirmation requirement.

`preferredProvider` becomes a per-account setting rather than a registry-root
global, because the fallback rule only makes sense against that user's own
connected set.

Honest cost: when the machine is asleep, the board is fully readable but nothing
new is generated. That needs to be a first-class "waiting for your device" state
rather than a spinner that never resolves, and it belongs on the pricing page
rather than being discovered.

## 12. PII discipline

`tests/test_no_pii.py` currently scans tracked text against markers read from
the live profile. That does not survive multi-tenancy, and per-account fixtures
are worse than the problem: the test exists to keep one real phone number out of
the repository, and fixtures would put N real phone numbers in.

Replace the profile-derived half with shape rules that need no profile: no
E.164 or NANP phone patterns, no street-address patterns, no email outside an
allowlist of `example.com` and similar, in any tracked text file. The rule half
ports unchanged and is the stronger half anyway: no tracked images, ever, with
no content inspection, because a screenshot of a filled form cannot be shown to
be safe.

Freeze this before the squashed-root open-source publish. That is the moment it
stops being cheap to change.

## 13. Sequencing

**Now, independent of §2.** The corpus/overlay split (§3), identity versus
history (§4), and corpus retention (§5), all inside the single-tenant app,
serving one user. No product change, no new surface, and it retires most of the
plan's risk.

**Also now, unrelated to this plan.** Sahil is job hunting on this system today.
Per-host parallelism, the circuit breaker and stale-daemon detection cost him
daily and conflict with nothing here.

**Blocked on §2.** Everything from §8 onward.

**Blocked on §1.** Any user-facing claim about which subscriptions work.

Nothing past the first group should start until the single-tenant system is
boring again.

## 14. Risks

Sourcing volume scales badly if Stage A is got wrong. Shared fetch is not an
optimisation; it is the only version that survives a hundred users without
getting the IP banned.

Bring-your-own-subscription is a support surface unlike anything Jobby has now.
Expired logins, rate limits, wrong plan tiers, and every one of them looks to
the user like Jobby being broken. Runner health telemetry has to be a product
feature, not a Healthchecks ping for one person.

The `applications/` folder does not survive. It is gitignored personal data in a
code checkout, and in multi-tenant it lives in R2 or nowhere.

A branch checked out in the executed working tree is a deployment. launchd runs
`scripts/watch.py` from an absolute path every three seconds, and the daemon is
one long-lived process, so merging is not deploying and a restart is the arming
event. Implementation work belongs in a separate worktree. This has already cost
this project once.

The deployed bundle is not the laptop. The board outage this week was
`brand.favicon_links()` reading `favicon.ico` and `apple-touch-icon.png` out of
`deploy/public/`, which is Vercel's static output: served at the edge and
excluded from the serverless bundle. It resolved on every laptop and raised
`FileNotFoundError` on every deployed request, so every deployment since the
Soft Premium renderer 502'd while local renders and the full suite stayed green.
Fixed in PR #18. Multi-tenant serving multiplies this class of bug, because
there is more code reading more paths on behalf of more accounts. Any file a
request path reads has to be proven present in the deployed artefact, not in the
repository. The directories the bundle may read are now written down; keep that
current.

Submitting on someone's behalf stays honest only because of the one-use nonce
and the visible-confirmation requirement. Both must remain per user.

## 15. Open decisions

1. **Hosted or self-hosted.** §2. Blocks everything from §8 onward.
2. **Agree Anthropic's Commercial Terms, or not.** §1. Blocks any hosted
   offering that runs Claude Code on users' behalf.
3. **Read OpenAI's terms in a browser.** §1. Blocks any claim about Codex.
4. **Size the corpus cap.** §5. Requires measuring union-scope volume, which
   nobody has done.
5. **argon2 in a Worker, or auth in a Container.** §9.
