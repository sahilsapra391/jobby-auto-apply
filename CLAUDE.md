# Claude Code handoff

Read this file before changing Jobby. It records the active architecture after
the 2026-08-23 provider and local-execution work. Extend these seams. Do not
rebuild them under a second abstraction.

## Current product decisions

Jobby runs all agent work on the user's Mac. Resume tailoring, cover-letter
creation, sourcing scans, form filling, submission, and confirmation checking
are local processes. The Oracle deployment is retired and outside the active
pipeline. Do not deploy to, connect to, edit, or delete the user's Oracle VM
unless the user explicitly restores remote execution.

The top-level UI has two centred pills in the top app bar, between the Jobby
logo and the theme control: Home and Settings. Home contains the Found,
Watching, In flight, Needs you, and Submitted workflow. Dropped is a separate
lower-left archive control and dialog, not a workflow tab. Settings contains
provider connections. There is no left rail and no Back to applications
button. A future Profile pill can join the centred control later.

The active visual system is Soft Premium, with restrained Option 1 hierarchy.
Its isolated tokens and responsive overrides live in `scripts/premium_ui.py`.
System, Light, and Dark are three icon-only controls. System removes the saved
theme override so the page continues to follow the operating system. The
reduced-motion media query disables decorative motion.

One mechanism owns each screen change, and mixing them is what made the board
feel rough. Home and Settings are the same document (`vercel.json` rewrites both
paths to `api/page`), so the nav links are intercepted, the body class flips
inside `document.startViewTransition`, and `history.pushState` moves the URL. A
tab switch changes one block on an otherwise identical page and gets no view
transition at all, only the incoming panel's own keyframes. Do not put
`view-transition-name` back on `.panel` or `.settings-page`: two panels differ in
height by tens of thousands of pixels, so the group animated that size and
stretched both snapshots to fit it. `.appbar` keeps `jobby-header` so it holds
still. And no keyframe on a panel may use a fill mode, because the Found panel
runs past 50,000px and a transform held on a layer that tall exceeds the max
texture and rasterizes blank.

The hero mark sits on the right where the old large scan button used to be, and
the mascot drawing is its resting state rather than a hover reveal over the
circular badge. The linework is a single colour, so dark themes set it on a
cream oval and ink it dark: `--mark-plate` and `--mark-ink` carry that pair, and
in light themes the plate is transparent because the page is already cream. The
plate has to circumscribe the drawing, which fills its viewBox corner to corner;
an ear or a foot crossing the edge lands as dark ink on a dark page. The
contour-draw and ink-wipe beats survive as a first-paint flourish gated on
`html.jb-intro`, cleared when the wipe ends so returning from Settings does not
replay the reveal. Clicking the mark replays it, which needs a reflow between
removing the class and re-adding it or the browser coalesces the two.

Neither beat may animate a compositor-driven property. CSS `clip-path` and
`opacity` both promote the pack to its own layer, which Chrome rasterizes coarse
for the length of the animation and re-rasterizes sharp only at the end; on
linework this fine that is a visible half second of blur. The wipe is therefore
an SVG `clipPath` whose rect grows in the drawing's own coordinate space, and
that clip wraps the ink group rather than sitting on it, because a clip on an
element with its own transform resolves in the space that transform establishes
and the ink group carries a flip.

Nothing on the board answers hover by moving except a control you press. Found,
In flight, Needs you and the Settings provider cards rest on `--hair` and hover
to `--hair-strong` with no transform. The nav pills and the three theme controls
wash to `--hover-wash` rather than lifting. An unselected tab washes toward the
selected tab's own treatment at about half strength, so the hover previews what
the click will give. Buttons still lift; the surfaces under them do not.

The logo is the way back to the board from anywhere, and the board means Found,
not whichever tab was last open. It calls `window.jobbyGoHome`, which
`settings_panel.py` publishes so the switch is shared rather than duplicated.
The only scan action in the UI is Scan now inside the Found status banner.

Native select popups and checkboxes are drawn by the operating system, so they
were the only controls on the board that could not follow the design system.
`premium_ui.SCRIPT` wraps every `<select>` in a styled face and leaves the native
element in the DOM owning value and events, which is what keeps the found lane's
existing wiring working untouched. Two things that face has to keep doing: saved
searches and Clear assign to `.value` and dispatch nothing, so the wrapper
redefines that property to stay in step; and an implicit `<label>` forwards its
clicks to the control it wraps, which is now invisible, so those are redirected.
The location picker is a `<details>`, which closes only on its own summary, so
outside clicks and Escape close it explicitly. Every listener in
`found_lane.SCRIPT` is guarded, because `tests/test_found_script.py` runs that
file in Node against a minimal `document` stub with no `addEventListener`.

Every lane is one width. `--shell` in `scripts/premium_ui.py` sets it, and the
app bar and the wrap both read it. The card lanes used to stop at 1360 while the
table lanes broke out to 1800 with negative margins, which is why Found, In
flight and Needs you looked like they were using half the screen next to
Watching and Submitted. That break-out is now a no-op and stays only so the
rule does not have to be hunted for if the wrap narrows again.

The Found row is four columns above 1200px: identity, summary and chips, the
match breakdown, and the controls. The breakdown is the rubric that already
rides on every record and used to appear only behind Details, which is worth
more card space than anything else available. Do not reach for `extracted`
degree, employmentType or roleCategory to fill space: all three measured 0 of
10,990 records populated on 2026-08-25, so they render as "not stated" and
nothing else.

The five-tab strip keeps its own narrower width. The lanes wanted the page; a
control stretched to 1800px reads as a toolbar.

`scripts/posting_sections.py` splits a posting into what the job is and what the
candidate needs, by matching heading lines against the vocabulary employers draw
them from. Both sides of that comparison go through `_words`, because
"Skills & Qualifications-" and "skills and qualifications" are the same heading
and only normalising one side matches neither. A posting with no recognisable
heading returns empty buckets rather than a guess.

`scripts/answer_record.py` writes what the employer's form asked and what went
into it, one JSON record per application, filed in `applications/<Company>/` next
to that application's PDFs. SKILL.md had told the agent to build that manifest
and then to throw it away, so 102 submitted applications could show the resume
and cover letter an employer received and nothing about the twenty questions
underneath them.

**No answers survive from before capture existed, and none are invented.** The
whole store was swept on 2026-08-27: `answers.json` is the only file in it
holding answers, 156 entries keyed by question slug with no application in them.
The 41 `questions*.json` files scattered through `applications/` hold question
text and nothing else. So a record has a `capture` level. `full` is a real run.
`questions-only` is a salvage: `answer_record.py salvage-questions` turns those
lists into records whose rows carry the question, `unrecorded: true`, and no
answer, no source and no read-back state, because all three would be invented.
It recovered 16 of 102 submitted applications, 224 questions.

The one thing that must never be built here is a join against `answers.json` to
fill those rows in. It is keyed by question slug with no application, and its
entries keep being edited — many were last touched months after the applications
they would be attributed to. Resolving a salvaged question against it produces
what the store would say today, presented as what the employer received then.
That is a fabricated audit trail, and a fabricated audit trail is worse than
none. A test asserts no stored value reaches a salvaged record.

There is a second tempting source and it is worse than the join. Sessions
write blocker text into a session's `pendingFields`, and #73 now writes the same
strings into `answers.json` as unanswered entries carrying `askedBy`. Both live
in a field called `question`, so backfilling salvaged records from them looks
like recovering more of the same thing. It is not. Measured across the 13
applications parked in `review` on 2026-08-27, 29 blocker strings, **0 of 29 are
question-shaped**; every one is agent prose. Two verbatim:

    "Email verification code (8 characters) sent to sahil...@gmail.com"
    "Browser tools unavailable in this session"

A fault report and an instruction, sitting in a field named `question`. The
salvage reads `questions*.json` only and must keep doing so. `askedBy` was 0
across all 156 entries when checked the same day, because that loop writes on a
park and none has happened since it merged, so there is nothing there to take
even if it were the right shape.

That is five distinct claims now catalogued, and none of them may share a field:
what the form asked and the agent entered (`full`), what the form asked with the
answer lost (`questions-only`), what the agent reported being stuck on, what the
agent saw on the confirmation page (`confirmation` on the completed event, 78 of
102, page banners with no field values), and what an employer's email says it
received, which does not exist yet. The `capture` level and the per-field
`source` enum are where a new one goes. Adding a value costs one enum entry;
blending two costs the ability to ever tell which you are looking at.

Attribution follows `r2_artifacts`' rule rather than a new one: the id in the
filename when the store knows it, otherwise the company folder and only when
that company has exactly one application. 25 files were skipped on those
grounds and the reasons are printed, because a company with four open roles
gets four different forms and filing one role's questions under another's is a
stranger's logo on someone's card.

Three things that file owns rather than trusts the caller for. It withholds
one-time codes, passwords and national or bank identifiers, keeping the question
and dropping the value, so a secret never reaches the disk that record is about
to migrate off. It flags a row sensitive from the question text *and* from the
stored answer's own `sensitive` state, which is the half question text cannot
see. And a `stored` source has to cite a key the answer store actually holds,
because a source badge that cannot be checked is decoration. Each of those is
covered by a test that fails when the guard is disabled, not only by one that
passes while it works.

Withholding and flagging are two different things and only one of them is about
a reader. **A withheld value was never written**; there is nothing on disk, so
the row says which class of thing it was and offers no affordance, because there
is nothing behind it. **A flagged value is written and, today, shown plainly.**
The board has one reader, he knows his own salary and his own demographics, and
covering them behind a tap was friction protecting nobody. That is a decision
about this account's display and it reverses the moment there is a second one: a
stranger's compensation is not drawn plainly on a shared surface. Which is why
the flag stays on the record rather than being deleted along with the mask. It
is what a per-user board will mask on and what the multi-tenant plan's Class 2
field encryption will key on, and re-deriving it later from question text alone
would silently lose every row the answer store's own state caught. Do not
"simplify" it away because nothing currently draws it.

A record is a Class 1 artifact and goes where the PDFs go. `r2_artifacts`
already matched `answers-<id>.json` by name before this existed, so it keys one
`u/{userId}/app/{applicationId}/answers.json` with `application/json` and
`r2_sync` uploads and indexes it; nothing in the publish path needed adding.
The modal reads it through `/doc/`, the same authenticated proxy, the same
password gate and the same R2-then-KV dual read the documents use. It has no
route of its own on purpose: a second serving path is a second auth check and a
second dual read to keep in step. What that did need was one line inside the KV
fallback, where the content type was hardcoded to `application/pdf` because
every document KV held was one. It is now taken from the name, so a record
served from the pre-migration copy is still a page rather than a file the
browser refuses to open.

`answer_record.applications_dir` must stay character-identical to
`r2_artifacts.applications_dir`, and `ArtifactContractTests` compares the two
rather than restating the rule. A writer and a collector that resolve
`applications/` differently under a redirected store produce a record nothing
ever uploads, and the board then says "not captured" for an application whose
record is sitting on the disk.

Records are fetched, not embedded, and that is measured. Against `main` at 101
submitted applications on 2026-08-27: the column, the modal and its script cost
the page 16,260 bytes, a captured row adds 198, and embedding a realistic
15-field record for every application would have cost 263,509 against roughly
370 KB of headroom under the embed cap. So the only thing this feature puts in
the published state is the list of captured ids, 4,137 bytes at 101, which is
0.02 of a percentage point of the reader ceiling.

That decision was taken while the state was 21.0 MB against a 20 MB ceiling and
`guard_state_size` was refusing every publish. `state_slim` fixed that, and the
same store measured 10.08 MB, 48.0%, an hour later. The decision does not change
with the headroom: a record is a per-application artifact, it belongs with the
artifacts, and carrying a hundred of them in a payload every board load pays for
would be wrong at any occupancy.


Those sections are what an expanded card shows, and they are fetched from
`/api/posting` rather than embedded. Sections average about 265 bytes a record,
the page is capped at 4MB and has about 21 bytes a job spare, and the published
state is capped at 20MB and sits at 9.4. So the state carries them and only an
opened card pays. A saved copy of the board has no route to ask and simply
shows the rest of the panel.

`extract.years_required` reads the requirements section first, where a bullet
has no responsibilities or company blurb around it to be confused by, and inside
that section the heading does the qualifying: "7+ years in growth" is a
requirement even though it never says "experience". Two bounds keep that from
running loose. A disqualifier only counts within the figure's own line and
sentence, both clamped to the existing window, so neither bound can ever take
away a figure the window already accepted. Measured store-wide: 20 gained, 0
lost. Over 70 full bodies with no stored figure, 11% to 24%.

The premise that every posting states a number does not hold. Of 58 sampled
postings still without one, 36 never use the word "year" at all.

`scripts/backfill_sections.py` fills both for records written before this
existed. It reads the 1,500-character excerpt, so it recovers less than ingest
does from the full body, and it never overwrites a figure ingest already found.
It defaults to `--dry-run` because a daemon is using that store.

Company identity is centralized in `scripts/company_logos.py`. Job feeds may
store a trusted `logoUrl`, and promoted roles carry that value into Watching.
Source ingestion also records an employer-owned website when a job description
links to a domain matching the company name. The board otherwise derives a
real employer domain from direct employer posting URLs. Never use an ATS or job
aggregator favicon as an employer mark.
When no employer domain is recorded, one is derived from the applicant tracking
slug or the company name. `logo_plan` decides which and returns a single digit,
and `found_lane.SCRIPT` performs the derivation over `co` and `url`, which the
payload already carries. That split exists for page weight: `main` sits at
3.63MB against a 3.7MB budget at the embed cap, which leaves about 21 bytes a
job, and a domain string does not fit. `LogoContractTests` compares the two
halves on every plan and every legal suffix, because they are one contract in
two languages; a first version covered only the plans and passed with a suffix
deleted from the script's copy.

Both derivations require the slug or name to agree with the recorded company,
and the guard is the point. Measured over 200 randomly sampled companies on
2026-08-25, a known domain or a candidate returned an image for 168 of 200, up
from about half. Adding the first word of the name on its own reached 178 and
was discarded: what it bought was Federal Deposit Insurance Corporation
resolving to federal.ai and Bureau of Prisons to bureau.ai. A stranger's logo is
worse than no logo, and a favicon endpoint answers 404 for a domain it does not
know, so a wrong guess costs a monogram rather than a wrong mark.

The authenticated `/api/company-logo` route in `deploy/api/board.js` validates
the request, fetches candidates server-side in order, limits response size and
type, and returns a private cached response. `alt=1` means "try the other TLDs
of this label"; that list lives in the route because it is a constant, and
spelling it out per job is what put the payload over budget. The browser must
only load same-origin logo URLs. Monograms are the required fallback, and about
one company in six still lands on one.

Found locations use the hierarchical picker in `scripts/found_lane.py`.
`COUNTRY_NAMES` supplies full visible names and `CONTINENT_COUNTRIES` assigns
each of the 199 selectable locations to exactly one display group. Continents
select or clear all children, country rows support independent multi-select and
`Only`, and Worldwide selects the complete set. Keep the storage contract:
empty saved `locations` means Worldwide, while a partial selection stores
explicit country codes. Legacy region packs expand when the page renders.

OpenAI, Anthropic, and Gemini share one local provider registry in
`~/.job-application/providers.json`. New work probes connected accounts in
saved order and uses the first one that can invoke its pinned model. Failed
login, usage, or model checks move new work to the next healthy connection. A
running document process keeps its saved provider, but the form stage and a
retry may hand off. Do not choose or connect a provider for the user.

Every agent task pins an exact model:

- OpenAI: `gpt-5.6-luna`
- Anthropic: `claude-sonnet-5`
- Gemini: `gemini-3.7-flash-medium`

Gemini must report the requested served model through its bounded telemetry
record. A remap or missing record fails closed.

## Runtime shape

`python3 scripts/watch.py --install` owns three launchd services:

- `ai.jobby.watch` runs the queue watcher and form orchestrator.
- `ai.jobby.documents` runs serial local document generation.
- `ai.jobby.providers` polls only the Settings control lane every two seconds.
  It exists so a slow provider or application probe cannot delay Connect or
  Disconnect. Do not merge this lane back into the main watcher.

All three services use the same provider registry. OpenAI credentials remain in
Codex. Anthropic and Gemini keys live in owner-only files under
`~/.job-application/provider-secrets/` and enter only the matching provider
child environment. Disconnect deletes the matching key.
The settings API exposes only the local target, labelled Your Device. Legacy
OCI queue and deployment files remain only for compatibility and recovery.
The launchd installer retains its configured Python interpreter. This matters
for repositories under macOS Documents, where Privacy controls can deny a newly
selected interpreter access even when the plist itself loads.

The document stage still uses the authenticated, AES-256-GCM Cloudflare KV job
transport. This keeps the existing serial lease, crash recovery, bounded
payload, and result-validation contract. Encryption, decryption, provider
execution, HTML/PDF rendering, geometric checks, and file installation all run
on the Mac. KV receives ciphertext and fixed status metadata.

## The Profile page's wording travels, it is not read off the disk

**Everything the document worker needs is in the bundle. The one input that was
not is the one that never worked.**

`_base_resume_content` read `resume-content.json` out of the task-private store
and fell back to `templates/resume-content.json`. `materialize` wrote
`profile.json` and `story-facts.txt` into that store and nothing else, so the
file was never there, the fallback was the only path, and every resume and cover
letter Jobby has generated started from the repository template. The Profile
page saves an edit and answers "Saved. Your next tailored resume starts from
this." No resume ever did.

Measured on 2026-08-28, because a docstring asserting the store copy "is
preferred here" is exactly the kind of claim that is not evidence for itself.
`build_bundle` for the real application `7923ca442c573811` then `materialize`:
the task store held those two files, and `_base_resume_content` on that runtime
returned a dict equal to the shipped template and unequal to the store copy
sitting on the same machine.

So the wording goes through the bundle now. `build_bundle` resolves it through
`store.resume_content`, which already answers with the device copy when the
Profile page has been used and the template otherwise; `validate_bundle` checks
its shape; `materialize` writes it beside `profile.json` at 0600. The bundle
schema is 2, so a job encrypted before this and claimed after fails by name
rather than quietly reverting.

**An empty `document-tasks/` does not cover that window, and believing it does
is the trap.** That directory holds *claimed* work. A bundle encrypted under the
old schema and sitting unclaimed in the KV queue is not in it, and a worker that
only speaks the new schema will claim it after the restart and reject it. It
fails by name, which is what should happen, but it fails on a job the user
already watched be accepted, so they see a failed application rather than a
deploy artifact. Raised by a peer session on 2026-08-28 while reviewing this
change, and it was right.

So a schema bump gates on the queue as well as the directory. Both are cheap:

```
~/.job-application/document-tasks/ empty          -> nothing claimed
no session documentJob.state in queued|claimed|running -> nothing waiting
```

Measured before this merged: 13 `documentJob` entries across the store, all
`ready`, nothing queued or claimed. `document_job.retry_failed` rebuilds the
bundle under the current schema, so a job caught by this is recoverable with the
board's own Retry, but recovering it costs the user a click they did not earn
and should be avoided rather than relied on.

**Both of those checks read local state, and neither reads the queue.** The
transport is Cloudflare KV and `document_job.py` cannot enumerate it: all five
subcommands (`ensure`, `sync`, `retry`, `poll`, `show`) require `--id`, so it can
only answer about a job whose id you already hold, and `show` resolves to
`get_state`, which reads the session on disk. There is no "list what is waiting".

The gate is sound anyway, and the reason is an ordering rather than a
coincidence. `_ensure_unlocked` calls `_save_state` *before* `_submit`, with the
comment saying why: if the process stops after Cloudflare accepts the request,
the next pass reuses the same id. So a job cannot exist in KV without a local
session already recording it, and enumerating sessions is complete by
construction. The mirror can lag the other way, reading `queued` for a job that
has finished, which only makes the gate hold when it need not.

That completeness is a property of the single-device deployment, not of the
check. Section 6 of `docs/plans/multi-tenant-architecture.md` is what would
invalidate it: once another device can enqueue, the local mirror stops being the
whole queue and this gate needs a real KV enumeration behind it.

**No local fallback is left in the worker and none may be added back.** Reading
a second file when the first is absent is precisely how one document's wording
was served in place of another's for as long as the feature has existed, and a
substitution that says nothing is indistinguishable from working. A missing file
now fails the job by name. For the same reason, reading the base sits outside
the OpenAI override-shape fallback: inheriting "the verified base wording" means
something only once the base has been read, and swallowing the failure to read
it leaves a run with no wording and nobody naming why.

The shape check does not count list lengths, on purpose. `store._same_shape`
counts slots because it stands next to the template. This endpoint does not,
which is the whole reason the wording is transported, and `resume_build.render`
is the check that does hold the template and refuses a mismatch.

The tests are the other half of the lesson. Ten worker tests exercised this path
and all ten passed throughout, because they built runtimes by hand and inherited
the same fallback the product did; four had no `storeRoot` at all and were
reading the developer's own `~/.job-application`. A test that supplies an input
the real caller does not supply is testing a configuration nobody ships.

## A document greets once, signs once, and never stutters, whoever wrote it

**These gates live in trusted Python (`cover_letter.validate_content`,
`resume_build.assert_integrity`), behind every provider, and must never be
"moved" into a prompt.** A prompt is advice to one model; the pipeline is the
contract for all of them, and the user's instruction is explicit: the
guardrail holds regardless of which AI generated the content.

Measured 2026-08-28 over all 79 rendered documents: 16 letters read "Dear
Dear" (the model put a greeting in the salutation field the template already
prefixes), 5 ended the closing with "Sincerely, <name>" above the template's
own signature block, and real employers received them. So `validate_content`
strips leading Dears and trailing sign-offs at the one intake every consumer
shares (render and PDF-verify must see the same text or verification rejects
its own render), and `render_html` refuses unvalidated duplicates outright.

Three duplication rules, each calibrated against the corpus rather than
guessed: an adjacent doubled word is refused for alphabetic words only,
because "Dear Dear" was the only alpha double any provider ever produced
while digits legitimately repeat 264 times; paragraphs sharing 60% or more
of their words are one paragraph written twice, against a measured 18%
ceiling between genuine paragraphs; and no body paragraph may open with a
greeting (one real letter, Contentsquare, did). `resume_build.doubled_word`
is the single definition both documents import, and
`tests/test_resume_integrity_duplicates.py` covers the resume half in CI,
which excludes `test_resume_build`. Rendered PDFs from before these gates
are not healed by them; regeneration for still-open applications is its own
task.

## Artifacts live in R2, and the board links to them by filename

Generated resumes and cover letters (PDF and HTML), cached job descriptions and
per-application answer records are Class 1 data in
`docs/plans/multi-tenant-architecture.md` section 6: not secrets the way a
provider key is, so they sit in a private R2 bucket in plaintext and are read
back by the API after the board's own password check. The relational store did
not move and is not in scope; that is the plan's Phase 2.

The key layout is `u/{userId}/app/{applicationId}/{kind}.{ext}` with
`u/account-1/` today, so a deletion request stays one prefix sweep and
multi-tenant is this layout with a different id rather than a migration. A file
that cannot be tied to an application id keeps its company folder under
`u/{userId}/unfiled/{company}/{filename}` instead of being filed under an
invented id. Measured 2026-08-25 over the real tree: 621 objects, 31.1 MB, 287
attributed across 120 applications, 334 unfiled. The unfiled ones are working
drafts and hand-made documents from before `document_contract` owned the
naming, and every document an employer actually received is attributed.

Attribution reads the store, never the filename's shape. Only 37 of 187
application ids are 16-hex; the rest are slugs, and a hex-shaped sidecar pattern
filed 13 of 39 job descriptions as unattributable. The document kind comes from
the field it was recorded under, not from looking for "CoverLetter" in the name.

**The board links to documents by filename and that did not change.** `/doc/<name>`
resolves through `jobby:doc-index` in KV, one small key mapping filename to R2
key, so no published board and no saved copy has a stale link. A name that
occurs twice in the tree is left out of the index rather than resolved
arbitrarily: `posting.txt` exists in a dozen company folders and means something
different in each.

**The function reads the object and returns the bytes. It does not redirect.**
The first version minted a presigned GET and redirected, which is what the
architecture plan then said, and it was measured: a second round trip, and the
Vercel leg alone is 370 to 430ms whatever it is doing, so a 119KB resume cost
about 180ms more than the KV path it replaced. Proxying is one round trip and
the function's own read beats KV's, 104ms against 122ms at 119KB and 145ms
against 301ms at 540KB, because KV carries base64 and R2 carries bytes. Range
is forwarded so a phone's PDF viewer can page a large file. No signed URL
reaches the browser, so none lands in history or in a shared link. Do not put
the redirect back; the Workers-era design serves through an R2 binding, which
is this shape with less machinery, not the redirect.

Dual-read order: R2 first, then the KV copy for anything the index has not
learned. An R2 404 on an indexed key falls through to KV, because the index
being ahead of the bucket is emptiness rather than a fault. Any other R2 failure
with no KV copy behind it returns a 502 naming the fault. It never answers 404
for a document that exists.

SigV4 is written twice, in `scripts/r2.py` for the device and `deploy/r2.mjs`
for the function, because presigning needs the S3 credential and the deployment
mints the links. `tests/test_r2_contract.py` compares complete URLs across
twelve cases. Treat it the way `LogoContractTests` is treated: one contract in
two languages, and the test has to compare outputs rather than restate rules.

`scripts/r2_sync.py` is both the backfill and the steady state, deliberately.
A migration that maps a file to one key and a publish that maps it to another
never finishes. Idempotency is three layers: the local manifest at
`~/.job-application/r2-manifest.json` (zero operations when nothing changed),
a HEAD when the manifest does not know, and the object's own
`x-amz-meta-sha256`. `--verify` is deep by default because the stamped metadata
survives a corrupted body, so a HEAD-only pass calls corruption healthy.

An upload that fails fails the publish, loudly, before anything reaches KV. The
local file stays authoritative. Do not soften this into a warning.

R2 refuses a body that disagrees with its signed `x-amz-content-sha256`, so
corrupted bytes cannot be uploaded through this client at all. Verified against
the live bucket on 2026-08-25.

R2 is the system of record for application artifacts, and the local
`applications/` tree is a working set rather than storage. Documents go up when
they are installed and again when the application finishes, not at the next
publish, because the window between rendering and publishing is the one where
the only copy is local.

Local files cannot be abolished, and it is worth knowing why before someone
tries. The form agent uploads a PDF to the employer by path, and
`browser_mcp_proxy` pins that path by device, inode, size and sha256. A file
handed to a file chooser has to exist on a filesystem, so R2 cannot be the
source at submit time. What can be true, and now is, is that nothing is
*retained* locally: `r2_sync.py --evict` removes a local artifact only when the
application has reached a terminal outcome, R2 verifiably holds the same bytes
(re-downloaded and hashed, not trusted from the manifest), and the board can
still resolve the name. `--get NAME DEST` brings one back for a retry.

**The publish index reads the manifest, not the tree, and that is what makes
eviction safe.** It used to be derived from the files the collector walks, so
deleting a local file after uploading it would have dropped its entry on the
next publish and turned a live board link into a 404 with the object sitting
safely in R2. `doc_index_from_manifest` survives the file being removed. Do not
put the tree-derived version back, and do not evict from a checkout whose
publish still uses it.

The KV document copies under `jobby:doc:*` are still there and still served as
the fallback. Retiring them is its own commit after a week of clean serving,
and it re-does the KV write-budget math.

### A page can be byte-identical and still blank, 2026-08-30

**Correct markup plus a byte-identical stylesheet does not imply a rendered
page.** v2's `/settings` route shipped with the board's own `.settings-page`
markup and the board's own CSS, generated from `dashboard.py` and verified
byte for byte, and it rendered nothing at all.

The board is one document whose panels are toggled by a class on `<body>`:
`.settings-page` and `.profile-page` are `display:none` until
`body.settings-view` or `body.profile-view` is set. v2 serves them as separate
routes, so nothing set the class and the CSS did exactly what it says.

Every test passed. The markup check passed, because the markup was the board's.
The stylesheet check passed, because the stylesheet was the board's. The
deployed-shape check passed, because the route answered 200 with a full page of
HTML. **The failure was in the composition of two correct halves, and nothing
in the suite was looking at composition.**

That is the same shape as the four database incidents earlier the same day: two
things each correct on their own, and a property that only exists between them.
It is also why the check that found it was opening the page, which is a thing
no assertion in this repository does.

Generated-from-source removes drift; it does not remove this. When a piece of
the old board is carried across, the question "does the markup match" and the
question "does the page render" are different questions, and only the second
one is the one anybody cares about.

#### And the same CSS is correct to be inert on the other side, 2026-09-03

**A class the old board sets with a body toggle, which v2 serves as a route, is
not a gap. Counting it as one overstates the remaining work permanently.**

This is the finding above seen from the other end and it needs saying, because
the obvious reading of "styled and never rendered" is "somebody forgot". The
inverted reachability check reports 83 such classes, and seven of them --
`settings-view`, `profile-view`, `back-board`, `nav-n`, `settings-link`,
`profile-error`, `ui-separator` -- are the single-document board's navigation.
`.settings-page` is `display:none` until `body.settings-view` is set; v2 has
separate routes, so nothing sets it and nothing should. **The identical rule
that rendered a blank page on 2026-08-30 is correct here.**

So a list of unrendered classes needs three states rather than two, and the
third has to be declared rather than inferred, because nothing about the class
distinguishes "not built yet" from "never will be". Measured 2026-09-03: 64
buildable, 12 gated on the write path, 7 architectural. Without the third the
gap reads 83 forever and no amount of work closes it.

**The guard on the second state is the one worth copying.** A group claiming to
be blocked on the write gate must have classes belonging to a write surface
somebody has named, and the test fails otherwise. A wrong `gated` parks
buildable work behind a gate that was never in its way, and **that is the class
of error nobody finds, because it looks like caution.** Every other wrong
label produces work that fails; this one produces work that never starts, and
the absence of a symptom is what protects it. Verified by mislabelling the scan
banner and watching the test refuse it.

### A correct fix, mutation-verified, and the board stayed frozen, 2026-09-01

**Two uncovered `on conflict` targets, one table apart, froze the board for
seventeen hours. Fixing the first one changed nothing observable, and only
running a real sweep showed why.**

Both tables carry two unique constraints and both upserts named one:

```
postings   legacy_id  +  (source, board_slug, external_id)   on conflict (legacy_id)
employers  norm_name  +  domain                              on conflict (norm_name)
```

Each resolved collisions *within a batch* in Python, correctly. Neither knew
about a row already stored under a different key, so an arriving record whose
board key or domain was already held raised `UniqueViolation` inside a
multi-row INSERT and aborted the batch. Class A dual-write fails the operation
on a mirror error, so `save_jobs` failed, the sweep could not persist, and
every sweep after it failed identically. **2 records of 20,835 did this.**

The `postings` fix was right. Five negative tests written before it, three
failing with the production error. Mutation-verified: reverting it fails, and
so does replacing the winner rule with "the stored row always wins", which
would otherwise have passed every test drawn from the live pool because both
real collisions happened to have the incumbent winning. Full suite green,
2,925 tests. Deployed under the lock.

The board stayed frozen. `employers` failed one layer earlier, on
`employers_domain_key`, and from outside the two are indistinguishable: same
exception class, same effect on the sweep, same silence. Nothing about
inspecting the first fix could reach the second. What found it was running
`ingest.py --tier 1` against the production store and reading where it died.

**So: for a defect that stops a pipeline, the fix is not verified by its own
tests passing. It is verified by the pipeline running.** Third time this week
that observing rather than inferring was the only thing that worked, and the
first where the inference was supported by a green suite and a mutation test.

The generalisation, because two instances one table apart is a class: **any
table with two unique constraints whose upsert names one of them has this
failure available to it.** Before adding a constraint, or an upsert, check
which conflict target covers which constraint. `packages/schema/backfill.py`
is where all three live (`upsert_employers`, `upsert_postings`,
`upsert_decisions`).

#### The gate list did not include the file both outages were in

`parity_clock.GATE_PATHS` named `verify_parity`, `class_b`, `dual_write` and
`jobs_store`, and not `packages/schema/backfill.py` -- which `dual_write`
calls for every class A upsert. Neither fix reset the clock, so nine green runs
stayed on the counter across a day when the thing they measure had been
rewritten twice. The list named the caller and not the code that writes.

**The test guarding it was worse than absent.** It said `assertEqual` against
the four names, so it did not merely fail to notice the gap: it would have
failed the correct fix. A restatement of a list, defending the list from being
corrected. That is the shape to watch for -- a check whose subject is the
configuration rather than the property the configuration is for.

It is now a floor (those four are load-bearing, none may quietly leave), and
what the gate must *cover* is derived in
`tests/test_gate_paths_cover_the_mirror.py` from the `upsert_*` call sites in
`dual_write.py`. Same shape as parsing declared tables out of the migrations
rather than listing them alongside. Deliberately not the whole import closure:
`parity_clock.py` and `write_path_coverage.py` are both reachable from the
write path, and gating on those would restart the clock for editing the clock.

#### Nothing alerted, and the check that should have measured the wrong thing

The dead-man ping was composed from three warnings and not one could see it.
`stale_board_warning` reads `LAST_DEPLOY` and `LAST_PUBLISH`, which answer
"when did we last publish" and not "when did the data last change" -- and the
publish kept succeeding, because it publishes whatever the store holds. The
board was republished the whole time, fresh and a day old at once.
`parked_slot_warning` fired three hundred times about a lesser problem, which
is its own lesson about a loud alarm for the wrong thing. `chrome_flag_warning`
is unrelated.

Underneath that, `watch.spawn` says "Never waits on it" and means it: a sweep
that dies writes a traceback to `ingest-runs.log`, exits non-zero, releases its
lock, and is observed by nothing. The traceback naming the exact constraint sat
in that file for hours.

**And the first version of that alarm could never fire.** `pool_staleness`
called `jobs_store.paths(None)`, which raises `TypeError` because that function
takes the store root; a broad `except Exception` turned the raise into `None`,
and `stale_sweep_warning` reads `None` as "nothing to say". It returned `None`
on every machine. Shipped, with a green suite, as the fix for an outage that
nothing alerted on.

Every test in the file mocked `pool_staleness`, **including the one named for
testing it**, which mocked `jobs_store.paths` as well and so never exercised
the call that was wrong. A test that supplies an input the real caller does not
supply is testing a configuration nobody ships, which this file already says
about the document worker and which recurred here anyway, in a file written the
same afternoon.

What found it was running the function on the real machine after deploying and
reading the number, which printed `pool age: unknown` next to a pool twenty-seven
minutes old. Nothing else would have. So the rule for an alarm specifically: an
alarm that is silent has two possible meanings, and only one of them is good
news. Prove which by making it fire, or by reading the value it is silent
about. Silence is not evidence.

`stale_sweep_warning()` is the fix and it is one more reason on the same alarm,
not a second alarm. It reads the **pool's** mtime against the configured
tier-1 interval, fires after two of them, and names `ingest-runs.log` so it is
a diagnostic rather than a notification. Outcome rather than cause on purpose:
a sweep can fail to land without a non-zero exit, and the question is always
"is the pool current".

### Instrumentation on the branch tests take, 2026-09-01

**A hook can be correct, exercised, and never once run in production, because
the code forks on whether a database exists and the tests are on the other
side of the fork.**

`jobs_store._persist_pool` has two branches:

```
if not mirror.enabled():
    store.write_private(...)          # a test's branch: temp root, no database
    return jobs
...
mirror.persist(conn, account, path, before, jobs, ...)   # production's branch
```

The write-path coverage hook lived in `store.write_private`, chosen because it
is "the only function that puts bytes in the store". That was true of every
class B write and of class A **only when the mirror is off**, which is only
ever a test. `dual_write.persist` writes the pool file itself.

Measured, the same call on the same machine:

    mirror OFF (temp root)   ->  2 observations
    mirror ON  (production)  ->  0 observations

**19 of the 27 paths the parity gate requires are class A pool paths**, so a
counter built to prove the rare paths fire could not see 70% of its subject on
the only machine that matters. Every test was green. The counter's own tests
were green, and they were green *because* they ran on the branch that has the
hook.

This is the eighth check in this file that passed while measuring nothing, and
it is a shape none of the other seven share. The others were a filter, a
paraphrase, a fixture, a skip, a constant, an inference, a stale table. This
one is a **fork in the code selected by environment**, where the test
environment and production take different sides and only one side is
instrumented. Nothing about the hook was wrong. Nothing about the tests was
wrong. They were about different lines.

**It generalises worse than any of the others**, which is why it earns its own
section rather than a line in the store-isolation one. Any hook, guard,
counter or assertion placed inside a branch chosen by "is a database
configured", "is this CI", "is the store redirected", or "is the mirror
enabled" has the same defect available to it, and the symptom is always
silence: the instrumented branch is the one under test, so nothing ever fails.

So: **when instrumenting, find the fork before choosing the site.** If a
function has two ways to reach disk, instrument both or instrument below them.
And verify on the branch production takes -- for this one that meant driving a
real Pass on the real store with the mirror on, which ticked six observations
across five paths where a temp-root test had ticked two.

A related habit that would have caught it earlier: the counter reported "4 of
27, all class A sweeps" for a day, and class A sweeps run every four hours. A
number that plausible is exactly the kind nobody questions. What broke it open
was driving a path deliberately and watching the count not move.

### The check was fine. It was pointed at the wrong server, 2026-09-02

**A readiness probe that fetches `/` cannot tell this process's answer from
somebody else's.** `check_deployed_shape.mjs` builds the app, serves the traced
bundle on port 3199, and asserts against it. A run earlier that afternoon died
of an uncaught `TypeError` between the spawn and the cleanup, leaving two
`next-server` processes alive. Every run after it spawned a child that died of
`EADDRINUSE`, polled the port, got an answer, and reported `deployed shape OK`
against a bundle four minutes old.

It was found by a mutation that should have failed and did not. `writesEnabled`
was hardcoded to `return false`; the built output verifiably contained
`return!1`; a hand probe of the same standalone directory answered 503; and the
check went on saying 200. The three ways of reading the artifact all agreed and
all were about a file nobody was serving.

**This is not "a check that measures nothing". The measurement was correct and
its subject was substituted**, which is a different failure and a worse one,
because every artifact you would inspect to debug it is the right artifact. It
is the same shape as the Playwright page a test had not selected, and as the
`activity.json` two sessions each dated to their own run: provenance is the one
property the thing itself cannot answer.

Three lines close it. `boot` refuses a port already listening and names the
`lsof` that finds the holder; the readiness loop ends when the *child* exits
rather than when the port answers; and `uncaughtException`, `unhandledRejection`,
SIGINT and SIGTERM all run the cleanup, because the orphans came from an
exception one line away from it.

**And the control it was hiding is worth as much as the fix.** The check
asserted `POST /api/applications` answers 503 with `live_board` off, which a
route that had been deleted would also satisfy. It now turns the flag on
against the same database the server reads, sends the same request, and turns
it off again in a `finally`, **and asserts the row exists**, not the status.
A status is not evidence: `remember` stores the first answer for an idempotency
key and `replay` serves it back, so a reused key returns 200 forever without
the handler running. Measured locally, where the database survives between
runs; CI's throwaway container hides it, which is the excuse that let a test
suite write the real store for three days. Each half is verified by breaking
it: `writesEnabled` always false, and `commit` replaced with `rollback`.

**The lane cursor belonged to an ordering nobody carried.** `LaneList` has
always taken `sort` and `filters`, and has always documented why they must
travel with the cursor. `page.tsx` passed neither. Measured against the live
database: page one of Found by Match % ends on score 93, page two asks the date
query for rows before the timestamp "93", Postgres answers `date/time field
value out of range`, the client catches it and draws "50 shown, and that is all
of them" over 17,341 rows. Three optional props are three chances to pass some
of them, so there is now one `query` string built by `laneQuery`; the route
refuses a cursor whose type disagrees with its sort; and `done` and `failed`
are separate states, because they shared a variable under a comment reading
"Never silent".

### A stopwatch reports whatever it is pointed at, 2026-09-02

**The gate is "p95 click-to-visible under 250 ms across all lanes, on a real
device over a real network, recorded", and every clause of it turned out to be
load-bearing.** Building the rig produced three wrong numbers before a right
one, and none of the three looked wrong.

**A hidden tab does not paint.** Browsers pause `requestAnimationFrame` for a
backgrounded page and resume it whenever the tab returns, which could be
minutes, so the double-rAF that approximates "the frame that painted" simply
never fires. The first run recorded zero samples out of thirty and the drive
call hung. Recording those frames when they eventually arrive would have been
worse than the hang: a fabricated number, larger than any real one, failing the
gate for a reason nobody would look for. `mark` now drops a sample taken while
`document.hidden` and counts what it dropped.

**Warm-up is not steady state, and pooling them describes neither.** The
per-iteration series on Found ran 735, 44, 154, 248, 115, 228, 238, 243, 244,
250, 141, 131, 133 and then a flat 33 for the remaining twenty-seven. Reported
as one number that is a p95 of 244 and a lane over budget. Reported as two it
is a cold board around 250 and a warm one at 33. Both are real and a reader
meets both, so the warm-up is relabelled `apply-event-cold` rather than
discarded, recorded and not gated on.

**And the first reading sent the fix in the wrong direction.** Found at p95 244
against a six-row lane at 34 looked exactly like fifty cards re-rendering for
one changed row, which is a familiar and plausible React fault with a familiar
fix. `memo` on every row component moved it to 296, which is noise. What
settled it was printing the series instead of another summary statistic. **A
p95 is a summary, and a summary of the wrong thing is still a summary**: the
shape was in the data the whole time and no percentile could show it.

The consequence for anything else measured here: **print the series before
believing the statistic**, and separate the phases before pooling them. Two
lines of output would have saved a build, a measurement and a revert.

Three more properties the rig holds, each verified by breaking it. The
percentile is written twice, in TypeScript for the browser and Python for the
recorder, and comparing them over all 19,800 (p, n) pairs below 200 samples
found 27 disagreements: `(p / 100) * n` makes `0.07 * 100` into
7.000000000000001 and `Math.ceil` takes it to 8. Multiply before dividing on
both sides. Lanes are never pooled, because "across all lanes" is a
requirement on the worst of them: a quiet lane contributing under five percent
of the samples can be entirely over budget and leave the pooled number
untouched, and the quiet lanes are exactly the ones a reader clicks least. And
a run missing a kind is `incomplete` with the missing kinds named, never a
pass, which is the same rule `answer_record.py` applies to a salvaged question.

**A literal NUL byte reached a source file and nothing noticed for an hour.**
`measure.ts` grouped its samples on one, between the kind and the lane. Both
halves used the same byte so the code was correct and every test passed; it
surfaced only because `grep` refused the file as binary in the middle of a
mutation run. An editor draws nothing and a review sees nothing. It is written
as `\u0000` now, and `tests/test_gate_latency.py` walks the v2 source tree for
any control character outside tab, newline and carriage return.

### v2 asked for a route it never had, on every card, 2026-09-02

**`/api/company-logo` came across in the markup and not in the code.** Fifty
Found cards requested it per page load, every one 404ed, the CSS took the
failed `<img>` to `opacity: 0`, and the monogram underneath showed. It looked
like a design choice. Measured on the Python board the same day, 891 of 906
rendered records resolve to a real logo, so v2 was drawing a monogram for every
company where the board it replaces draws one for about one in six.

It was found by the latency rig, not by a test, and only because the rig needed
a database with postings in it: CI's container has none, so the board emits no
logo URLs there and the deployed-shape crawl had nothing to fail on. **An empty
fixture hides a whole class of missing route**, which is the same excuse that
let a test suite write the real store for three days.

The route is ported rather than redesigned, and its allowlist is now generated
from `company_logos.TRUSTED_SOURCE_HOSTS`. There were already two hand-written
copies; a third would have been the one that drifts, and drift in that set is
an open redirect nobody is watching. `tests/test_company_logo_route.test.mjs`
compares all three, including `deploy/api/board.js`'s.

**And the crawl could not tell a missing route from a missing logo.** Both
answer 404. That is the `.gitignore` finding's shape exactly, one directory
over: a route git ignores and a route nobody wrote look identical from CI. The
crawl now reads the body, because this route's 404 says "No company logo." and
Next's router 404 is HTML, and it was verified by deleting the route and
watching the check quote the HTML back.

### Parity guarantees agreement, not correctness, 2026-09-02

**Both stores agreeing on a wrong value is a green parity run.** 122 postings
are filed under the employer `Appcast Enterprise`, which is the feed publisher
and not the employer: the descriptions are Johnson Controls, Tyco, Hologic,
Pfizer. Postgres holds 122 and the file store holds 122. Parity is green and
correct to be green. The fault is upstream of both, in ingest, and no
comparison of two mirrors can ever see it.

The same publisher is stored under two spellings, `Appcast Enterprise` (64 reed
rows) and `Appcastenterprise` (58), so employer dedup misses it as well. That
is the first real test case for the employer-resolution work rather than a
display bug to patch over.

So parity answers exactly one question: has the mirror lost or altered anything
on its way across. It is silent about whether what crossed was right. Reaching
for it as evidence of correctness is how a green clock ends up standing behind
a board showing a stranger's name on 122 roles.

### Parity was comparing a subset nobody had written down, 2026-09-02

**Seven consecutive green runs compared 11 of the 28 columns the class A mirror
writes, and one of the four the class B mirror writes.** A session was
`applications.status`, one string. `phase` was never looked at. Neither was
`posted_at`, `summary`, `years_min`, `sections`, `remote`, `tags`, `closed_at`,
`employers` in its entirety, `job_scores` in its entirety, or
`application_events.payload`, which is the only record of what an employer
received.

`verify_parity.FIELDS` was eleven lambdas, and they were a second
implementation of `backfill.posting_row`. **Being a subset is how a second
implementation drifts**: nobody deletes a field from a comparison, they add one
to the writer and forget the reader. Same shape as the migration table list,
`GATE_PATHS`, and the generated-file list `sync_v2_styles --check` walked.
Fourth instance, and the first where the drifting copy was a *behaviour*
rather than a list of names.

**Nothing was diverging, and that is the point.** Measured over all 22,019
records the two stores share, every one of the 28 columns agreed once
str-vs-timestamptz and str-vs-jsonb were compared as values. This was not a
latent divergence, it was a latent blindness: an entire class of field could
have gone wrong with the clock green by construction.

**The first run that did look found one.** Harvey `17121dc2523fec5e` held
`phase = 'Queued'` in Postgres against a session file with no `phase` key at
all, because `_application_id` wrote
`phase = coalesce(excluded.phase, applications.phase)` and a NULL therefore
meant "keep whatever you had". That branch is only reachable when a session
supplied a status, and a session is the authority on its own phase including
when it has none, so the coalesce could only ever be wrong. It is also v2's
stale "Filling the form" pill.

**And `migrate.py status` is the same shape one layer down.** It compares the
migrations *table* against the migrations *directory*, so it answers "has every
file been applied" and is silent about whether applying them produces what is
there. It was green while production carried 25 drifts across 23 live tables
against the 17 the migrations build: a recorded migration with no file, a
`board_meta` nobody declared, five `provider_links` columns added outside the
system, and nine on `applications` holding real data. Three of those were found
by tripping over them one at a time, which is what made each look isolated.
`scripts/schema_drift.py` applies every migration into a scratch schema and
diffs the catalogs, and it is in CI. **That is GATE_PATHS again: a check
answering an adjacent question and reading as coverage.**

`applied_by` is the same family in miniature. It defaults to `current_user`, and
every runner on this project connects as `neondb_owner`, so a column that looks
like attribution carried none: eight migrations, one value, and no way to say
whose the two unexplained ones were.

**Fifth instance, 2026-09-06, and its hiding place is the lesson.** The Found
lane's filters were read in two places: `page.tsx` destructured seven of them
and `/api/lane` hand-listed three. So the server component served a filtered
page one and the route served an unfiltered page two, from the same URL, with
a header count agreeing with neither. Nothing errored; Load more simply
returned rows the filter excluded.

**One URL, two answers, and the second reader was the one nobody looks at.** A
page component is where anybody debugging a filter starts. The route that
serves page two is a different file, reached only by scrolling, and it had been
correct for the three filters that existed when it was written.

**What found it was checking rows rather than counts.** Every new filter moved
the count -- the count comes from `counts()`, which the page calls -- so a
count-only check passed on all six. Comparing the row *sets* between filtered
and unfiltered showed 0 rows added and 0 removed for every filter but the one
that predated the route. A predicate that moves the count and not the rows is
the same shape as a cursor that pages nowhere: the summary is right and the
thing it summarises is not.

So when verifying a filter, a sort, or anything else that narrows: compare the
set, not its size. `filtersFrom` is the single derivation now.

`scripts/mirror_fields.py` discovers the field set from the mirror: `postings`
by calling `posting_row`, everything else by reading its INSERT. Parity's
expected side is now the mirror's own transform -- `posting_row`, `score_row`,
`employer_rows`, `session_row`, `history_row`, `answer_row`, `profile_row` --
so a column added to the writer is compared without anybody adding it twice.
Exemptions carry a reason each and a test asserts they do.

#### Why the module that derives one side kept a list for the other

**`mirror_fields` was written to end the declared-twice class and had an
instance of it inside itself.** It derived what the mirror *writes*, and
`class_b_postgres_state` next door hand-wrote the columns to *read back*. The
two lists drifted, and the symptom was 355 rows reported divergent with
`postgres=None` on columns Postgres was holding perfectly well. Fifth instance
of the class in this file, and the second inside the module built to end it.

The question worth sitting with is not how the list got out of date. It is why
anybody wrote a list there at all, in the one module whose entire premise is
that lists of fields go stale. Three things made it invisible, and none of them
is carelessness:

**The two sides did not look like the same fact.** "What the mirror writes" is
plainly a property of the writer, so deriving it is the obvious move. "Which
columns to select" reads as a query detail, a local decision about a SELECT,
and its being the same field set is a coincidence you have to notice rather
than a shape you can see. The duplication is only visible from outside both.

**The derivation went where the duplication was already painful.** There were
two writers disagreeing, that was the reported bug, and deriving the write side
fixed it. Nothing about fixing a live fault prompts you to audit the module you
are fixing it in for the same fault in a different direction. The fix's success
is what closes the search.

**A read-back list fails silently by construction.** A missing column in a
write is a missing value someone eventually sees. A missing column in the
comparison's read is a `None`, and `None` compares unequal to a real value, so
the check does not go quiet -- it goes *loud*, in the safe-looking direction,
and 355 false positives read as "parity is doing its job" rather than "parity
is broken". Compare the write-path counter, which read 4 of 27 and looked
plausible. Both numbers were wrong and neither was suspicious.

So the rule is narrower than "derive everything", which nobody can act on:
**when a module derives one side of a comparison, ask what the other side is
made of before trusting the comparison.** A derived expectation checked against
a hand-written observation is half-derived, and the half that is written down
decides what the check can see.

**Widening a comparison is where false positives come from, and two arrived.**
22,001 findings on `job_scores.config_version`, which is `text` in Postgres and
an int in the pool: 23 against '23'. And 19 on `employers.domain`, which the
mirror writes as `coalesce(employers.domain, excluded.domain)` so that a domain
learned once is never overwritten with a blank -- making a stored employer row
a running union across every sweep rather than a function of today's pool. A
check that cries wolf on 22,000 rows is a check nobody reads, so both are
handled: the comparator compares values rather than reprs, and the employer
comparison applies the mirror's own update rule.

Eleven employers do still disagree, and they are reported as
`employer_field_pinned` rather than hidden: the pinned value is the worse one
more often than not, `blog.duolingo.com` over `careers.duolingo.com` and
`cts.businesswire.com` over `impact.com`. That count is the first real input to
the employer-resolution work.

### The instrument found its own documentation and counted it as coverage

**A check for "we shipped a class we cannot activate" searched for the class
name anywhere in the source. It passed, because `fresh`, `loaded` and `over`
appear in the comments explaining them.** The instrument read its own
documentation and scored it as the thing being documented.

That is the eleventh check in this file that measured nothing, and it
generalises well past CSS. Any check that asks "does this name appear
anywhere" is asking about the *corpus*, and the corpus contains the writing
about the thing as well as the thing. Prose, docstrings, commit messages, test
names, variable names and log strings all mention the subject. A grep-shaped
check cannot tell a use from a mention, and the file where the mention is
densest is always the file where the implementation should be.

The fix is not a cleverer pattern. It is to ask a question only a use can
answer: not "is `loaded` written down" but "is `loaded` put on an element" --
`className` attributes and `classList` calls, nothing else. Same shape as
counting an upsert's actual column list rather than grepping for the column
name.

**The second attempt was worse and is the shape to fear.** An edit removed the
`assertEqual` from the method, so it built its findings list and returned. A
test that cannot go red for any input, sitting green in a passing suite. It was
not caught by review, by the suite, or by reading the file; it was caught by
running the three mutations it existed to detect and noticing that all three
passed.

So, for any check of this class: **write the mutation before believing the
green.** Disable the thing the check is for and watch it fail, once per case.
Three runs, a minute each. Both failures above were invisible to everything
cheaper.

**And the shape recurred one level down, in the same session, after this entry
was written.** The staleness test beside the main check asked whether an
allowance's class name appeared anywhere in v2 -- the same question, about the
same file -- and so called an allowance stale while the rule it suppressed was
still unreachable. Knowing the shape did not prevent repeating it; what caught
it was the check disagreeing with a change that should not have moved it. A
lesson written down is not a lesson applied, and the second instance is always
somewhere adjacent to the first.

### A fixture with an empty collection under-renders, and nothing looks wrong

**A check that renders a fixture and inspects the output is only as complete as
the fixture, and an empty collection is the most likely way to under-render.**

`test_v2_matches_the_board` proves v2 invents no class the board does not emit.
It builds the board from `ROW_BEARING_STATE` and collects every class in the
HTML. That state carried `jobs: []`, so `found_lane.select` returned nothing,
Found drew `empty_state`, and **the entire filter block above the lane** -- the
date chips, Advanced search, the 199-country picker, the actions row -- never
reached the markup. The check ran against roughly two thirds of the board for as
long as it has existed, and reported `jb-actions` as a class v2 had invented
while the board emits it on every real render.

Measured, one job added:

    a wholly empty state          99 classes
    rows but an empty pool       132
    one job with state "new"     164

**This is not another check that measured nothing.** It measured something, and
what it measured was correct. Its *subject* was two thirds the size it appeared
to be, which is a different failure and a harder one to see: nothing is wrong
with the assertion, the fixture, or the code under test, only with how much of
the subject the fixture reaches.

**The anti-vacuity guard was already there, set to a threshold nothing could
fail.** The file asserted `assertTrue(emitted, "the board rendered no classes;
the check is vacuous")` -- exactly the precaution this file prescribes -- and 99,
132 and 164 all pass it. **"Not zero" is not "complete."** A guard against a
vacuous check has to be calibrated against the size of the real subject, not
against emptiness, or it certifies the under-rendered case as healthy.

The fix is a floor derived from the same render: the fixture must beat the same
state with every collection emptied, by a margin. It moves when the board moves,
where a literal count would rot.

**The audit, because one instance implies others.** Six tests here render a
fixture and scan the output, and the other five are safe for a reason worth
stating rather than assuming: `test_v2_head_parity` scans the `<head>`,
`test_found_script` scans `found_lane.SCRIPT` as source text, and
`test_answers_viewer` and `test_dashboard` scan markup they build in the same
test. **The hole needs a scan whose subject is "the whole rendered page"** rather
than a named part of it, and only the class check had that shape.

So when writing a check that renders and scans, ask what the fixture does *not*
contain, and what the renderer does when a collection is empty. A lane that
draws an empty state usually takes its whole panel with it, and the output is
still perfectly valid markup.

### A name assembled at runtime is invisible to every check that reads the source

**Two guards disagreed about one file, both were wrong, and they were wrong in
opposite directions.** The subject was a class name built by an expression:

    className={"bank-row" + (open ? " open" : "")}
    className={`bank-tag state-${entry.state ?? "confirmed"}`}

`test_v2_styles_are_reachable` reads the markup to prove no styled state is
unreachable. It could not see `state-sensitive`, `state-missing` or
`state-dismissed` anywhere, and reported all three rules as dead CSS. False
positive. `test_v2_matches_the_board` reads the markup to prove no class is
invented, tokenised the same attributes, and reported `+`, `?`, `:`, `===` and
`(entry.state` as classes v2 had made up. False positive of the opposite kind:
one guard saw too few names, the other too many, from the identical text.

**Neither is fixable by a better regex, and reaching for one is the trap.** The
name does not exist in the source at all. It exists after the expression runs,
and the set of names it can produce is a property of the data. A parser that
resolved these two cases would still be defeated by the next
`classes[kind]`, and every improvement would make the checks more confident and
no more correct.

Two responses, and both are needed:

**Write the name out where the check has to see it.** Whole literals in a
ternary, one branch per state, which is also how the stylesheet declares them.
It reads no worse and both guards agree with it.

**Where a name genuinely is computed, say so and stop asking about it.**
`` `jb-chip ${kind}` `` renders six real variants that cannot be enumerated from
the text, so `dynamic_bases()` collects the static part and the check skips
every rule whose subject carries it. That is coverage deliberately given up,
which is honest, where a listed guess at the six values would rot.

The general form, past CSS: **a static check can only see the names that are
written down.** Anything a program composes -- a class, a column, a table, a
route, a metric key, an event name -- is outside what any source reader can
enumerate, and a checker that quietly treats "not written down" as "does not
exist" will be confidently wrong in whichever direction its default points.
Decide which names must stay literal for the checks to work, and make the rest
explicitly out of scope rather than accidentally so.

### A summary statistic over a non-stationary series is not a measurement

**Ten of the checks written up here measured nothing. This one measured the
wrong thing and produced a confident number**, which is a different failure and
a worse one, because there is a number to act on and it is precise.

The gate's stopwatch read Found at p95 244 ms against a six-row lane at 34 ms.
That is exactly what fifty cards re-rendering for one changed row looks like,
so `memo` went on every row component. It moved to 296 ms, which is noise. What
settled it was printing the series instead of another statistic:

    735 44 154 248 115 228 238 243 244 250 141 131 133 | 33 33 33 ... (x27)

Page warm-up, ending by the fourteenth call. The row count had nothing to do
with it, the p95 was a summary of two different populations, and no percentile
of that series could have shown it. The memo was reverted.

So: **print the series before believing the statistic, and separate the phases
before pooling them.** A percentile assumes the samples come from one process.
When they do not, every summary of them describes a population that does not
exist, and it will be precise about it. Two lines of output would have saved a
build, a measurement and a revert.

The rig keeps both populations now, `apply-event-cold` and `apply-event`, and
gates on the warm one with the cold one recorded beside it, because a reader
really does meet the cold board once per page load.

### A constant imported across the client boundary is not the constant, 2026-09-02

**`export const LANE_COOKIE = "jobby.lane"` lived in a `"use client"` file. A
server component importing it got a client reference, so `cookies().get(...)`
was called with `undefined` and returned nothing.** No throw, no warning, no
type error: `jar.get(undefined)` is a miss like any other, and a miss is what
an absent cookie looks like.

What made it hard to see is that **the theme cookie beside it worked
perfectly**. `THEME_COOKIE` is declared in `_ui/theme.ts`, which is not a client
module, so it resolved to the string. Two cookies read from the same jar, three
lines apart, one right and one silently empty. The symptom was a logo that
ignored the remembered lane while the theme was flawless, which reads as a bug
in the lane feature and is a bug in an import.

`tsc` cannot catch it: the type is `string` on both sides of the boundary,
because the boundary is a bundler transform rather than a type-level one. Nor
would a unit test on either module, since each is correct in isolation. **A
test that would plausibly have caught it does not obviously exist** -- the only
thing that found it was reading the rendered href, seeing `/?lane=found` where
the cookie said `done`, and asking the server directly with a hand-written
`Cookie:` header. That is worth admitting rather than inventing a guard that
would not have fired.

The rule is small and absolute: **a value a server component reads must be
declared in a module that is not `"use client"`.** Constants shared across the
boundary belong in a plain module both sides import, never in the client file
that happens to use them most.

#### The same rule, both directions, one afternoon, 2026-09-06

**The rule above says where a shared value goes. Getting it wrong in either
direction took the Profile page down twice in an hour.**

    page.tsx held `catLabel` and passed    Functions cannot be passed directly
    it to a client row component            to Client Components
    ProfileSurface held it and page.tsx    Attempted to call categoryLabel()
    called it on the server                 from the server but categoryLabel
                                            is on the client

A function is the loud case of the constant above, and it is worse in one
respect: **it throws only on a render that carries a row.** `tsc` types it
correctly on both sides, because the boundary is a bundler transform rather
than a type-level one. `next build` compiles it, because it fails at
serialization. And the deployed-shape crawl passed it, because CI's Postgres
had no rows: `rows.map` produced nothing, the component was never created, and
the prop was never serialized. Production has 195.

The fix for the instance is `_ui/bankLabels.ts`, a module that is neither side.
The fix for the class is the fixture below.

### An empty database is a check that covers a fraction of the page, 2026-09-06

**`check_deployed_shape.mjs` builds the traced bundle, serves it, and crawls
every route, and against CI's throwaway container every lane drew its empty
state.** Three faults have now reached production through that one gap, and
each was invisible for the identical reason:

    /api/company-logo   came across in the markup and not in the code. Fifty
                        Found cards asked for it per page load and 404'd. CI
                        emitted no logo URLs because it had no postings.
    the RSC boundary    throws at serialization, so only a render carrying a
                        row throws, and there were no rows.
    .gitignore          `applications/` unanchored swallowed the write
                        endpoint's route file. A route git ignores and a route
                        nobody wrote answer identically from CI.

This is `test_v2_matches_the_board`'s empty-fixture finding one directory over,
and the same correction applies: **"not zero" is not "complete."** That check
asserted the board rendered *some* classes and passed on 99 when a real render
emits 164. This one asserted the route answered 200 and passed on a page
missing every row-bearing branch it exists to exercise.

`packages/schema/seed_shape_fixture.py` puts one row of everything in --
employer, posting, score, an application per lane, a bank entry per kind, a
profile with a base resume -- under sentinel names nobody could mistake for
real data, and writes the markers it used. The crawl reads that file rather
than restating the strings, and **a missing fixture fails**: crawling an
unseeded page and reporting a pass is precisely the failure being closed.
Measured: 74 asset URLs before, 94 after, and it caught a second boundary error
on its first run.

**Its own guard was wrong first, in the instructive direction.** It refused to
seed a database holding more than 50 postings, on the theory that a real store
has thousands and a throwaway has none. Measured on a container that had just
run the suite: 300 postings and 229 applications, from tests that create their
own accounts and rows. So it would have refused in CI, on the one database it
exists to run against -- and a row count was never going to answer the question
anyway, because the question is *whose* database this is. It compares the URL
against `migrate._device_url` now, pooled endpoint normalised, and an
unparseable URL refuses rather than seeds. Provenance is the property the thing
itself cannot answer, which this file already says about `activity.json` and
about which kind of checkout you are standing in.

### SQL comparison is three-valued, and the false branch is silent, 2026-09-06

**`NULL <> 'title'` is NULL, and NULL is not true.** A disjunct written to mean
"this filter is off, so let every row through" instead lets nothing through,
and no error is raised anywhere.

The clause was Found's title-scope filter:

```sql
and (%(q)s::text is null or %(scope)s::text <> 'title'
     or p.title ilike '%' || %(q)s::text || '%')
```

With no scope chosen, which is every default page load, the middle term is NULL
rather than true, so a row survived only when the reader's raw keyword was a
literal substring of the title. Full-text search underneath was answering
perfectly and the lane came back empty.

**The corpus decided which third of it looked broken.** Three keyword tests
existed. `product manager` passed, because it is a substring of "Product
Manager" and a stemming index and a raw `ilike` agree on it. `manager
-warehouse` and `"product manager"` are substrings of nothing and both failed.
That is the substring-matching entry's shape from the other end: the rule read
correctly and the fixture decided what it meant.

**And nothing would have run those tests.** `test_found_filters` skips without
a database, so it runs in CI only, and it only ran at all because a *different*
fault broke first -- a hand-written parameter list that had not learned about
six new filters. The real defect was underneath a mechanical one, and the
mechanical one is what made anybody look.

So, for any SQL a filter is compiled into: **a parameter compared with an
operator rather than `is null` must be `coalesce`d, or guarded by an explicit
`is null` disjunct that short-circuits before it.** The failure direction is
silent removal, never an error, which is why it cannot be left to be noticed.
Measured across all eleven generated lane queries after the fix: one site
remains comparing a parameter with an operator, `(%(cos)s::text = 'only')`, and
it sits behind `%(cos)s::text is null or`, which short-circuits.

The general form outlives SQL. Any three-valued logic -- SQL predicates,
`NaN` comparisons, an `Option` compared with `==` -- has a middle value that is
neither branch, and a condition written as a two-way choice will take the
branch its author was not thinking about. Write the null case, do not let it
fall out.

### A confident rule renaming a real employer after its competitor, 2026-09-06

**The rule was going to be right about the corpus it was written for and wrong
about the corpus next to it, and only running it on the second one showed
that.**

An extraction rule reads a posting's description and proposes the employer it
introduces. Written for 222 postings filed under a feed publisher, where the
employer's name exists nowhere else in the record, it produced 59 proposals and
163 refusals and **would have passed those 222 clean**. Every proposal on that
slice was a real company.

Run against a different list -- employers whose *domain* was wrong, which is a
separate defect entirely -- it proposed, at **high** confidence:

    Deliveroo -> Doordash     "At Doordash, Deliveroo and Wolt, we're building"
    Toast     -> Bengaluru.   "in Bengaluru. We are seeking an Engineering..."

The first is the one worth remembering. The opening sentence is a comparison,
naming three competitors in a row, and `At X,` is the strongest frame the rule
has. So the rule was **most confident exactly where it was most wrong**, and
the output was a real employer renamed after its competitor, on a board that
generates job applications under a real name.

**Three things generalise, and the third is the point.**

A rule validated on the corpus that motivated it has been validated against the
distribution that motivated it. The 122 Appcast rows are UK job-board listings
with a house style; Deliveroo's careers copy is not, and nothing about the
first predicts the second. **Running it on a second corpus was worth more than
any amount of further work on the first.**

Confidence measured how the name was found, not how likely it was to be right.
`At X,` in the opening sentence is the rule's strongest signal and it is exactly
what a comparison sentence looks like. A confidence score that rises with the
feature that also produces the worst error is not a safety margin, it is an
amplifier.

And the fix was a distinction rather than a rule. "In `At X,` the keyword leads,
so a period inside X means the pattern ran on and the name is cut back; in
`X is seeking` the verb identifies the frame, so a period between them means
the frame never applied." Cutting in both places proposes the city; refusing in
both refuses "Warner Bros. Discovery". **Neither uniform answer is available**,
which is what makes it a distinction, and finding it took reading the two
failures side by side rather than generalising from either.

The narrow version, for anyone writing an extractor here: **a rule that
proposes gets run against a corpus it was not written for, before it is
allowed to propose anything.** Not to measure its accuracy there -- it may
legitimately refuse everything -- but to find out what it is confident about
when it is out of its depth.

## Security invariants

Provider processes receive allowlisted environment variables, bounded output,
fixed task contracts, and no inherited model default. Resume and cover-letter
rendering happens in trusted Python after provider output passes schema and
claim checks.

Form agents receive a local MCP firewall from
`scripts/browser_mcp_proxy.py`. The firewall exposes a small static Playwright
tool set, removes arbitrary navigation, JavaScript, shell, storage, routing,
and network-log tools, blocks Enter-based submission, and binds upload access
to the two staged PDFs by path, inode, size, and SHA-256 hash. Keep the trusted
runner responsible for opening the application URL.

That responsibility is `browser.open_application`, called from `run_apply.py`
before the provider is invoked, and it went unimplemented until 2026-08-25. The
agent has no navigation tool, so with nothing opening the page every run looked
at the `about:blank` tab `serve()` uses to hold the window and parked itself.
Do not remove that call, and do not hand an agent a slot it has not been put on.

Two rules bind the firewall's advertised tool surface. Every `inputSchema` must
be an object schema: a bare top-level `oneOf` makes a client drop the whole
server's tool list, not just that tool, and the argument rules those schemas
expressed belong in the validators anyway. And the answers to `server/discover`,
`tools/list`, and `tools/call` must carry the stateless completion markers that
`application_mcp.py` already sends, because a client on the 2026-07-28 revision
never sends `initialize` and reads capabilities from the results. Either fault
alone leaves the server reporting connected with no tools.

Submission uses a one-use nonce. `pre_submit` writes the `submitting` event and
returns the nonce. `record_completed` must present that nonce for the same
application while the latest event is still `submitting`, along with visible
confirmation text. Direct completion, reuse, and cross-application use fail.
Generated nonces begin with `jobby_`. Keep that prefix because an unprefixed
URL-safe token can begin with `-` and argparse then reads it as an option.

## The Found lane's Apply was dead for five days, 2026-08-28

Three stacked faults, found from one screen recording of the user pressing
Apply while nothing happened. Each fix left a guard; the numbers are measured.

**Every board mutation must send `x-jobby-request: 1`.** The deployed API
refuses a POST without it as cross-site (403, `request token missing`), and
that check landed 2026-08-23. `found_lane.py` had grown its own `post()` on
2026-08-19 without the header, so Watch, Pass and Apply from Found all 403ed
for five days while the dashboard's own buttons (which send it) kept working.
The toast said "That did not reach the queue" and named no reason, so fifteen
sessions of bug-fixing walked past it; it now appends the server's error, and
`tests/test_click_requests_carry_the_token.py` scans every embedded POST for
the header and holds a floor count so the scan itself cannot rot. When a click
"does nothing", reproduce the exact POST with the exact headers before
reading any code.

**A sessionless Apply head wedged the watcher, silently, behind one click.**
Apply on a Found card sends watch then apply; the watch click is what creates
the session (promote.py), and both ride one KV key through read-modify-write
with no compare-and-swap, so the apply enqueue can eat the watch enqueue.
The sessionless head then failed every tick with `session_missing`, nothing
ever claimed it, and the failure printer goes quiet between the 3rd and 60th
consecutive failure: measured 88 failed ticks behind one click, board frozen
throughout, both alarm branches silent. `ensure_application_session` now
promotes the head from the pool when its session is missing, and a head that
can never start (promotion refused) is claimed and logged `notStarted` with
promote's own reason. The lesson that outlives the fix: **a queue head that
is never claimed and never startable is a wedge on everything behind it**,
and "loud on the way in, then quiet" hid 56 of the 88 failures. `watch.err`
silence is not tick health; the lock file's mtime is.

**A URL whose identity rides its query is not a posting, it is a careers
page.** `norm_url` strips queries, so `stripe.com/jobs/search?gh_jid=8160754`
became `stripe.com/jobs/search`, which 258 different Stripe postings share:
one recorded Stripe role made `seen.check` call every other Stripe job
"already known", and one submitted Elastic application did the same in
`dupe_check` (`jobs.elastic.co/jobs`). Measured store-wide: 4,202 of 14,569
pool jobs carry their identity in the query; 10 of 102 submitted applications
do. `seen.url_identifies_posting` is now the one place that distinction
lives: a stripped URL participates in URL matching only when stripping lost
nothing, and query-keyed links match by (company, ATS key) instead. Both
matchers were measured against the real store before and after, per the
substring-matching rule above, and the same false-positive shape applies:
the matcher read correctly and the corpus decided what it meant.

## The apply path fails loud, 2026-08-27

Two faults were found by looking, not by any alarm, with three of five slots
parked and nothing saying so. Both are fixed and both left a guard behind.

**A provider that fails open turns a capability fault into a silent park.** One
run held a browser server that had passed `mcp_preflight` ninety seconds
earlier and was dead by the time a provider built its session. Codex named it
(`required MCP servers failed to initialize`) and refused to start, which is
correct. Claude Code took the same dead server, started the session anyway, ran
two turns, and parked the application on a blocker addressed to a person who
could do nothing about it. Do not read that as "Anthropic cannot fill forms":
the server was broken for both, and what differs is only who says so.

So the fence is re-checked immediately before each provider attempt rather than
once before the loop. It costs about 1.3 seconds against a run whose ceiling is
45 minutes. **That ceiling is `run_apply.TIMEOUT_SECONDS` and it is not a
duration; do not quote it as one.** Measured 2026-09-03 over the 62 apply runs
on this machine with a readable wall time: median 5.7 minutes, p75 7.7, p90
11.0, p95 16.0, max 18.6, and zero over 45. That matters wherever something has
to wait for a run to finish, which is Phase 4's drain. And a
park is classified after the fact: the firewall counts every accepted tool call
into `uploads/<id>/activity.json`, and a park with zero browser calls is
recorded as `parkClass: capability`, which the board draws in red saying the
fault is Jobby's. A park where the agent worked the form stays a park.

**That counter is written when the proxy is signalled, not only when it exits,
and for two months it was only the latter.** The write sat at the end of
`main()`, after `StdioProxy.run()` returns, which a process killed by a signal
never reaches. Measured on 2026-08-28 over every staged upload directory on the
production Mac: five real applications with their PDFs still staged and no
`activity.json` between them, and the one real record belonging to the run whose
Playwright backend never started, where the proxy exited on its own. Measured
the same day against the live CLIs with a signal-recording MCP server, codex
sends its MCP child SIGTERM and Claude Code sends SIGTERM to the server it
spawns for discovery and SIGINT to the one running the session. Neither ever
closes stdin, so no run that reached a form had ever written a record.

The consequence was asymmetric, which is why nothing looked broken. The
capability branch still fired, because a backend that fails to start lets the
proxy exit cleanly and write `totalCalls` 0. What never fired was the positive
half: `attempts[].browserCalls` was `None` for every real run and the
`tag_session(application_id, "parkClass", "")` clearing had never once run.

`_install_activity_signal_handlers` writes on SIGTERM, SIGINT and SIGHUP and
then dies of what it was sent, so the exit status still reports the signal. Two
things it must keep doing. It reads `process.returncode` rather than calling
`poll()`, because `poll` takes subprocess's non-reentrant waitpid lock and a
handler runs on the main thread that may be holding it; a deadlock inside the
bookkeeping would cost the run the thing the bookkeeping is for. And it
snapshots the stderr deque, which the drain thread is still appending to, since
iterating one under a concurrent append raises `RuntimeError` and best-effort
has to mean it on what is now the common path.

**An absent record still means "nothing known" and that is load-bearing, not
incidental.** `mcp_preflight` kills this process with `process.kill()`, SIGKILL,
which cannot be caught, so a probe leaves nothing behind that could be read as a
run which made zero calls. Verified against the real preflight and the real
Playwright backend on 2026-08-28: thirteen tools advertised, no file written. Do
not give preflight a graceful shutdown without moving that distinction
somewhere else first.

Verified the way CLAUDE.md asks for: a real `gpt-5.6-luna` run through the real
firewall and the real `@playwright/mcp@0.0.79` recorded
`{"browser_find":1,"browser_snapshot":1}`, `totalCalls` 2, `ended` SIGTERM; a
real `claude-sonnet-5` run recorded `totalCalls` 1, `ended` SIGINT, its
discovery probe's zero-call record correctly overwritten by the session's.

`mcp_preflight.REQUIRED` and `_tool_definitions` are one contract in two files,
pinned by `FenceSurfaceContractTests`. A tool in one and not the other is either
unguarded or impossible, and both fail quietly.

The browser proxy no longer sends the Playwright child's stderr to `/dev/null`.
That is why this fault survived so long: the client saw `connection closed:
initialize response` and the reason was destroyed as it was produced, so every
hand probe afterwards succeeded and the cause was never named. It is drained on
a thread, kept to the last 40 lines, and printed when a run ends badly.

**`browser_file_upload` cannot attach a file to most application forms.** It
answers only while a file chooser is open, and the click that opens one is the
click that hangs: the real input is usually a 1px transparent element behind a
styled button, so Playwright waits for actionability that never arrives. A real
GitLab run made six upload calls, got "can only be used when there is related
modal state present" every time, and parked with both PDFs unattached.

`browser_attach_file` is the replacement and it needs no chooser. It reaches the
page as `browser_run_code_unsafe`, the one tool the firewall most deliberately
removes, so the code is written in `ATTACH_FILE_CODE` and never assembled from
client text: the agent chooses a target and which of its own two bound PDFs, and
both cross into JavaScript as JSON literals. The unsafe tool stays unreachable
by name. Two behaviours are deliberate. With no target and several file inputs
it refuses and names them, because taking the first is how a cover letter lands
in the resume field on a form nobody reads back. And the input is described
before the attach, not after: a React form swaps the field out the moment it
accepts a file, which made a working upload report a 30-second timeout on the
live Justworks posting, and an agent told its upload failed retries it.

## The file input cannot say whether the attach worked, 2026-08-28

**`setInputFiles` resolving proves Playwright wrote files onto the DOM element.
Everything that decides the outcome happens afterwards, in the page's own
change handler, and `node.files` reports the same emptiness for all of it.**

Three shapes, measured: SmartRecruiters takes the file off the input, sends it,
and sets `input.value = ''` so re-choosing the same file still fires change,
which empties `node.files` on **success**. Greenhouse swaps the field out for
its own "file.pdf / Remove" view, which detaches the node the read would ask.
And a form that rejects on type or size empties it on **failure**. So a tool
reading only the input has to be wrong somewhere, and this one was wrong in
both directions at once: across every `browser_attach_file` call ever recorded,
the `node.files` confirmation branch fired zero times, five calls threw on a
SmartRecruiters attach that had landed, and the two successes it ever reported
both came out of the detached-node catch, from an `error` the code never read.

Versant `6c3824319c68c547` is the proof of the first direction. Both targeted
calls answered "the input reported no file after the attachment"; that
application's own park note says the resume is attached; and the snapshot it
took immediately after the last failed call has `resume.pdf` rendered beside
the field. SWIGGY `40c1f32b247a885a` produced the identical error the same
night, so this is SmartRecruiters and not Easy Apply.

**The second observable is the page's own rendering of the filename, and it is
scoped rather than searched for.** Both bound filenames are generic, so a
document-wide `text.includes(name)` would confirm a resume from help text or
from the cover letter beside it. The scopes are the input's own ancestors,
innermost first, crossing shadow boundaries, and they are captured as a live
handle **before** the attach, because the container that ends up showing the
name is frequently the one the page rewrote. A match must be a text node's own
text, must be a change against a baseline taken before the attach, and must not
sit inside an alert: a form that rejects a file names that file in its error.

**Do not flip the unconfirmed case into an attached-but-unconfirmed success.**
It is the same observation as a rejection, so flipping it converts a false
negative into a false positive, and the costs are not symmetric. A false
negative parks a run and a person looks at it. A false positive walks an agent
toward submit believing a resume is attached, under a real name, and cannot be
recalled. What did change is the wording: unconfirmed rather than failed,
carrying the page's own complaint when there is one, and telling the agent to
read the field rather than attach again, which is the retry loop the Justworks
case already warned about. The detached-node catch is held to the same
standard, and reports the error it used to discard.

**Both SmartRecruiters file inputs carry `id=file-input`, legally**, because
each sits in its own `spl-dropzone` open shadow root where the id is unique.
Playwright's CSS engine pierces those roots and finds two; `document.querySelector`
finds none. So the id named neither, every error on that form read identically,
and two sessions concluded from those logs that `nth=0` and `nth=1` had
resolved to the same element, on evidence that could not support it. `describe()`
now prints the way out of the element, which does tell them apart:
`spl-dropzone[data-test=apply-with-resume-container]` is the autocomplete
helper and `spl-dropzone[data-test=resume-upload]` is the resume field. Do not
scope anything here on the id.

`tests/fixtures/clearing-upload-form.html` reproduces both directions in one
page, with the input inside an open shadow root and the ids duplicated the way
the real form has them. It needs a live browser slot like the rest of
`test_apply_end_to_end.py`, so it skips in CI.

**A parked application may retry into its own slot, and only its own.**
`candidate_apply_slots` returns the affinity slot when it is parked under the
same application id, and `choose_apply_head` lets such an application pass the
head when the head has no slot. That pairing is what the refusal must not be
drawn wider than: a guard that refuses everything parked would refuse the one
retry that costs nothing, since an application already holding its slot consumes
nothing anyone else is waiting for. Verified against the live fleet on
2026-08-27 with zero free slots: the parked application sitting last in a queue
of five was chosen as the head, into `apply-2`, its own.

**A fleet of parked slots is an outage wearing a designed state's clothes.**
`parked_slot_warning` fails the same dead-man ping the board staleness uses, one
alarm with two reasons rather than two alarms, when most of the fleet is parked
or one slot has been held most of a day. Ages are parsed as UTC; `time.mktime`
reads local and an alarm that is hours out is an alarm nobody keeps.

Measured on 2026-08-27, when all five slots were parked with none running: by
browser-call count, four had reached the form and one had made zero. So four of
the five parks were correct and the pipeline was stopped regardless. No single
park being wrong is exactly why nothing said anything.

The same sentence goes to two places, because they answer different questions.
The ping tells the user something is wrong wherever they are. `meta.slotsParked`
puts it at the top of the In flight panel, which is where they were already
looking the last time this happened; the way it surfaced was the user asking how
many applications were running. It is written every tick rather than only when
it appears, so it clears itself when a slot frees. It is not the "Automation is
stopped" banner: capacity gone and automation stopped are different facts and
merging them costs the distinction.

**A remembered click must expire, and between a drop and an apply the newest
click wins.** The board remembers Apply and Drop clicks in localStorage and
re-sends them on load, bridging the seconds before the watcher reflects a
click. The Apply memory was already stamped and expired after ten minutes; the
Drop memory was a bare, immortal id list. While the fleet above sat parked on
2026-08-27, the user pressed Stop on queued cards and then Retry, and every
later page load replayed those drops for as long as the served page still drew
the card. The tick acts on drops before applies and discarded the apply for a
just-dropped id, so each replayed drop killed that tick's Retry: drop requests
for the same four applications arrived from 21:10Z to 22:58Z, every one paired
with a fresh apply click it erased, until all four sat in the archive nobody
had put them in. Three rules now hold. Both memories live in
`dashboard.CLICK_MEMORY`, stamped, expiring, and run directly by node in
`tests/test_click_memory_script.py`; a pre-stamp drop entry is discarded
rather than migrated, because its POST already went out and replaying it can
only kill a later click. An Apply or Retry click erases the remembered drop
for its application. And the tick resolves a claimed drop against the pending
apply queue by the entries' own stamps (`split_superseded_drops`, logged as
`dropSuperseded`): the newer request is the user's decision, and a missing
stamp keeps the old rule, the drop wins.

**Known gap, and which guard covers it.** The preflight proves the server
answers, not that Chrome behind it is reachable. Playwright MCP connects to the
browser lazily, on the first tool call, so a dead Chrome still advertises all
thirteen tools and passes the fence. `browser.open_application` is the guard
that catches that one step later, failing with "the posting would not open in
apply-N". Stating which guard covers which case is what makes a failure
readable: without it, a preflight pass followed by a dead browser looks like the
fence lying, and the fence is not lying, it is answering a different question.

`tests/test_apply_end_to_end.py` is the invariant none of this had. It drives
the real firewall against a real Chrome and attaches real files to a form whose
input is hidden. Both faults were invisible to the whole suite because every
test stopped at a seam. Unlike the resume render tests it is *not* on the
workflow's exclusion list: CI runs it and it skips there, because no slot
answers on a runner. So the only machine that ever executes it is this one, and
a failure in it is invisible to every green check until the suite is run by
hand. That is how a stale constant in it survived: the count it asserts became
wrong three hours after it was written, when the fix for a load race added a
readiness probe whose calls the firewall counts like any other. Assert such a
total against what the test knows it sent, never against a literal.

**That suite drives production slots, so it has to ask whether one is free
rather than whether one is up.** Every slot whose Chrome answers on this Mac is
a production slot. `live_slot` took the first one that answered, and on
2026-08-28 that was apply-1 while an agent was filling a real SmartRecruiters
application in it: the suite opened fixture tabs in that browser and its three
browser tests failed with "the upload form never appeared", because the page
they were driving belonged to somebody's job application. A running Chrome and
an unused Chrome are identical from the browser's side, and only the lock tells
them apart, so it now takes its candidates from `watch.free_apply_slots` rather
than restating what free means: `reserve` writes the slot lock before spawning
`run_apply`, the watcher's definition is the one that has to be right, and
borrowing it means the test follows if it ever changes. Parked slots are
excluded by it too, for a better reason than tidiness: a parked application is
one somebody was told to come back to, and driving a test through its browser
is the same intrusion as driving through a live run's. (**This used to say a
parked slot keeps its page state so its application can retry into it. That
stopped being true when `reconcile_browser_slots` was changed** -- a parked
application is `review` rather than `inflight`, so the next tick releases the
slot and closes every page. See the correction below.) The skip
message names which case it is, because "no browser at all" and "every browser
is busy" stop the suite identically and mean opposite things.

### A parked slot is released within a tick, and this file said otherwise

**Two passages above described behaviour that had been removed, and one of them
was quoted as current on 2026-09-07 while diagnosing a live application.**

`reconcile_browser_slots` holds a slot while its application is `inflight` and
at no other time. A parked application is `review`, so the next tick calls
`release_browser_slot`, which calls `browser.clear_slot` and closes every page.
Its own comment gives the reason, and the reason is good:

> A parked application kept its slot so a Retry could reuse the filled tab,
> which is a real convenience and it deadlocked the pool: five parked
> applications held all five slots, four queued retries could never get one,
> and three Drops drained into a fleet that had nothing to give them. Twenty
> hours, and every application involved looked healthy.

Observed rather than reasoned about: a Chainguard run parked at 02:50:55Z on a
verification code, and the tick log recorded
`{"browserSlots": {"released": [{"applicationId": "31c8aa9c1e988c56", "slot":
"apply-1"}]}}` at 02:54:29Z. `tabs` was 0 afterwards.

**The rule this earns is about documents rather than about slots.** A docstring
or a CLAUDE.md entry describes the code *at the time it was written*, and the
code is the only authority on what it does now. That is not a reason to write
fewer of them -- this file is the reason most of the last month's faults were
found quickly -- but a claim from here that decides an action gets checked
against the code first, exactly like a claim from a peer.

The failure mode is specific and it happened today: an entry is *correct*, then
a later change is *also correct*, and nobody re-reads the entry because nothing
about the change points at it. The two passages above were written when parking
held a slot. The reconciler was rewritten to fix a twenty-hour outage. Neither
author was wrong and the file became wrong anyway.

Three that have now had this shape, so it is a class rather than an accident:
`brief.py` as "the exception" to spawned-versus-imported when fourteen other
scripts were the same; `STATUS_QUEUE`'s docstring asserting `inflight` is
daemon-owned while three controls sent it; and this. **Each was a confident
sentence about a mechanism, and each stopped anybody looking.** Prefer "as of
<date>" on a mechanism claim, and prefer naming the function that decides so
the next reader can open it.

## No agent Chrome consults the keychain, and none runs a deleted binary

Two separate faults, found through one symptom on 2026-08-28: a modal macOS
"Keychain Not Found" dialog looping on the user's screen that Cancel could not
end.

**The dialog is closed at the source, and that is PR #111 rather than anything
here.** Chrome keeps its storage key in the macOS login keychain and keychain
access is bound to code signature, so a process whose bundle was replaced
underneath it fails and re-asks forever. Both of the repo's Chrome launch sites,
the slot browsers in `browser.py` and the headless renderer in `resume_pdf.py`,
now pass `--use-mock-keychain`. An isolated agent profile and a print-to-PDF
process need nothing the keychain holds.
`tests/test_chrome_never_touches_the_keychain.py` pins both, and a new launch
site anywhere is a third one to pin.

The renderer half is the one that is easy to miss. The dialog belongs to
SecurityAgent rather than to Chrome's own UI, so a headless document build
raises it too, out of a process nobody is watching.

The one-time cost was taken knowingly: cookies the slot profiles encrypted under
the real Safe Storage key cannot be read under the mock one, so stored site
logins reset once. LinkedIn and employer-site sessions live in those profiles
and some email-verification flows depend on them. It should be once per profile
rather than once per launch, because the mock key is fixed across launches and
cookies written afterwards are encrypted under it. That reasoning has not been
confirmed against a profile yet. As of 2026-08-28 apply-2 has relaunched under
the flag and apply-1 has not, so what the reset actually costs is observable now
and nobody has looked. Replace this paragraph with the measurement rather than
leaving the expectation standing as fact.

**A merge does not relaunch a browser, and that is the fault #111 could not
close by itself.** A running Chrome cannot be given a flag, and `serve()` starts
one only when the slot has none, so a browser that predates #111 keeps its old
command line until something stops it. Measured on the live fleet on 2026-08-28:
apply-1 started at 00:38:14 and #111 was committed at 00:38:36, twenty-two
seconds later. apply-1 was still running **without** `--use-mock-keychain` hours
afterwards, on the primary `browser-profile`, on the current build. It could
still raise the dialog, and it was invisible, because a version check has no
opinion about how a process was started.

**So drift is two questions.** Is it the binary we would launch now, and is it
the configuration we would launch now. `watch.chrome_drift()` asks both once a
minute. The binary comes from `browser.installed_version()`, the bundle's
`CFBundleShortVersionString`, against `browser.running_version(slot)`, CDP's own
`Browser` field. Not the `User-Agent` beside it: that is rounded to `151.0.0.0`
to blunt fingerprinting, so comparing it would report every slot as drifted
forever. The configuration comes from `browser.running_launch(slot)`, which
reads the slot's main out of the process table, matching port and profile
together and excluding helpers by `--type`, and diffs it against `_launch_argv`
so the expected flags are read from the launch site rather than restated.

The version half fails open and the flag half does not need to. An unreadable
version on either side counts as no drift, so a moved plist cannot empty the
fleet.

A drifted slot with no lock on it is stopped, and not restarted. `serve()` runs
before every apply and again inside `run_apply`, so it comes back correct when
it is next needed, and eight windows do not appear on the user's screen for
maintenance nobody asked for. **A slot holding a live run is never closed, and
a parked one is closed within a tick** -- see the correction below; this
paragraph said "or a parked application" and that has been false since
`reconcile_browser_slots` was rewritten. A form being filled and a form the user was told
to come back to are each worth more than retiring a browser early. Those are
named in `watch.py --status` and in the tick log, and
`browser.py --stop --slot apply-N` retires one by hand.

**The two halves do not earn the same response, and merging them costs the
distinction.** A stale binary stays out of the dead-man ping: with #111 in place
it is untidy rather than something the user can see, and a ping that fires for
untidiness is how an alarm stops being believed. A missing flag on a browser
nothing is allowed to close does ping, because that flag is the whole reason the
browser stays off the keychain. `chrome_flag_warning()` is the flags half only,
and `tests/test_chrome_drift.py` pins both the scope of the ping and the fact
that the version half is absent from it.

The two designs were weighed the other way round at the start, so it is worth
recording why it flipped. Recycling on drift keeps real Safe Storage and every
stored session, and it can only cure the dialog for browsers it is allowed to
close, which by construction excludes every slot holding a form. Mock keychain
ends the class everywhere, the renderer included, for one reset of the stored
logins. Ending the class everywhere won.

## Application worker repair, 2026-08-24

Read `docs/handoffs/claude-application-worker-2026-08-24.md` before changing
document selection, ATS validation, browser-slot ownership, or Apply refusal
handling. The repair is covered by the real brief selector, worker pipeline,
watcher state machine, and provider-runner tests. Do not replace exact document
ownership with fuzzy role matching, and do not weaken the ATS provenance gate.

## Main implementation seams

- `scripts/providers.py`: registry, connection order, health, model pins, CLI commands.
- `scripts/provider_login.py`: native interactive login and status publication.
- `scripts/settings_panel.py`: the three connection cards and local-only copy.
- `scripts/dashboard.py`: self-contained board, centred Home and Settings pills.
- `scripts/sync_vercel_renderer.py`: deterministic copy step for the deployed renderer.
- `scripts/premium_ui.py`: Soft Premium tokens, layout, motion, and responsive rules.
- `scripts/company_logos.py`: trusted logo selection, proxy URLs, and monogram fallback.
- `scripts/r2.py`: R2 access and SigV4, stdlib only, device side.
- `scripts/r2_artifacts.py`: which local file is a Class 1 artifact, and its key.
- `scripts/r2_sync.py`: upload, verify, prune, and the filename index.
- `deploy/r2.mjs`: the same signing, and the server-side read the board serves from.
- `deploy/api/page.py`: password-gated server render using sanitized KV state and deployed code.
- `deploy/api/board.js`: queue, document, logo, health, and provider endpoints.
- `deploy/renderer/`: generated deployment copy of the renderer. Never edit it directly.
- `scripts/watch.py`: Apply orchestration, three launchd plists, health pings.
- `scripts/document_job.py`: encrypted document-job client and local installation.
- `scripts/document_worker.py`: serial local document generator and fixed gates.
- `scripts/browser.py`: trusted Chrome lifecycle and per-run MCP configuration.
- `scripts/browser_mcp_proxy.py`: application browser firewall.
- `scripts/run_apply.py`: form-only provider task and staged upload binding.
- `scripts/answer_record.py`: the application answer transcript, its secret and sensitivity gates.
- `scripts/finish.py` and `scripts/application_mcp.py`: submission nonce state machine.
- `references/oci-worker.md`: retired historical recovery path, not current setup.

## What the deployed bundle may read

`deploy/public/` is Vercel's static output directory. Vercel serves those files
at the edge and leaves them out of the serverless function, so a renderer that
reads a path under `public/` resolves it off the working tree on any dev
machine and raises `FileNotFoundError` on every deployed request. That took the
board down from the Soft Premium renderer until 2026-08-25, showing only
"The board state could not be rendered." while local renders and the full test
suite stayed green.

Anything the renderer reads at render time travels in `deploy/assets/`, copied
there by `sync_vercel_renderer.BRAND_ASSETS`. `BundleDataFilesTest` renders
against a copy of the bundle with `public/` removed and is the check that
separates "renders here" from "renders deployed". The image allowlist for that
directory is written in three places that must agree: `.gitignore`,
`IMAGE_ALLOWED` in `tests/test_no_pii.py`, and `.githooks/pre-commit`.

To read a deployed failure: preview and `*.vercel.app` URLs sit behind Vercel
SSO (`all_except_custom_domains`), so only `jobby.fyi` answers a scripted
request. `vercel logs <deployment-url>` returns the function's own stdout,
which is where `board_render_failed` names the exception type.

## Keeping the renderer and the published state together

`.github/workflows/checks.yml` runs on every pull request (opened, synchronized,
reopened) and on every push to main. It builds on Python 3.12, the version
Vercel uses, and it fails the PR when `scripts/sync_vercel_renderer.py` produces
any change under `deploy/renderer/` or `deploy/assets/`. That is the check that
would have caught both of the outages: the three commits of UI work that never
reached the board, and the favicon the bundle read but did not carry. It is
scoped to those two generated directories on purpose, because the rest of
`deploy/` is hand-written and failing a PR for editing `api/page.py` would only
teach everyone to ignore the check.

The Python step runs every test module except `test_resume_pdf` and
`test_resume_build`, which render the real base resume out of
`~/.job-application` and need a local Chrome. Exclusions are listed rather than
inclusions, so a new test file is covered the moment it is added.

A renderer fingerprint cannot be made to agree everywhere. There is one
`jobby:board-state` and many live deployments, and every open PR gets a preview
whose renderer hashes differently from main's, while the laptop can only stamp
the branch it is standing on. So `deploy/api/page.py` measures drift before the
render and acts on it only after a render actually failed: a working preview
serves the board and reports both hashes in `X-Jobby-Renderer-Fingerprint` and
`X-Jobby-State-Fingerprint`, and a failed render under drift returns the 503
saying which half is behind instead of the bare "could not be rendered". Do not
restore the pre-emptive gate; it 503s every preview permanently, which is the
one thing previews exist to prevent. `rendererSchemaVersion` is what guards the
shape of the state itself, and it is the thing to increment when that shape
changes.

## Health monitoring

The private `HEALTHCHECKS_URL` belongs in
`~/.job-application/config.env`, never in Git or `deploy/.env.local`. The quiet
heartbeat runs every 55 minutes for a one-hour Healthchecks period with ten
minutes of grace. Real work can ping sooner and failures use `/fail`.
Use `scripts/worker_key.py set-healthchecks` to update it atomically without
printing private values.

## Verification

Run from the repository root:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/jobby-pycache python3 -m unittest discover -s tests
node --test tests/*.test.mjs
python3 -m py_compile scripts/*.py
bash -n deploy/oci/setup.sh
```

Provider and browser security tests live in
`tests/test_provider_runners.py`, `tests/test_browser_mcp_security.py`, and
`tests/test_browser_mcp_proxy.py`. The centered-navigation contract is pinned
by
`ProviderSettingsTests::test_home_and_settings_are_centered_above_the_hero_not_in_a_left_rail`.

## Workspace state on 2026-08-24

All three launchd services are installed and running with
`/opt/anaconda3/bin/python3`. The installer now retains that interpreter. The
private Healthchecks URL requested by the user is configured in
`~/.job-application/config.env`, mode 0600. The old Oracle staging directory in
`/private/tmp` was removed, and the Oracle VM itself was not touched.

Connect no longer attempts third-party consumer-login flows for Anthropic or
Gemini. OpenAI runs `codex login`, which supports local ChatGPT subscription
access. Anthropic opens the Console API key page. Gemini opens the Google AI
Studio key page. Both key flows use a private terminal prompt and validate the
exact model before reporting Connected. Never publish, log, or copy those keys
into the provider registry, queue, Vercel environment, or Cloudflare KV.

OpenAI document output uses Codex `--output-schema`. Do not replace it with
prompt-only JSON. OpenAI strict schemas require every object property in
`required`, so optional resume overrides and the optional cover-letter date use
null sentinels that trusted Python removes before shared validation. The Luna
contract also caps one-page cover-letter paragraphs and tells the model to keep
resume replacements no longer than the original text. Low-memory Chromium is
Linux-only. Forcing its fresh private profile on macOS hangs for 60 seconds.

`resume_build.assert_integrity()` is a required gate between override merge and
render. It rejects duplicate and near-duplicate bullets, changed metrics or
versions, low source-token retention, changed bullet counts, and oversized
rewrites. Do not weaken or bypass it for a provider-specific output. The two
duplicate source points found in the CAMP and Spechawk roles were replaced with
distinct statements supported by their existing role ledes.

The real IFS application `bc839847c93d9e46` passed the complete OpenAI path on
2026-08-24: provider output parsed, content validated, four artifacts rendered,
ATS and geometry checks passed, the one-page cover letter passed, the encrypted
result was installed, and the local job reached `ready`. Safe worker logs now
record fixed validation stages without retaining document text. Both retained
IFS resume HTML variants have zero duplicate or near-duplicate points.

A real `gpt-5.6-luna` form-agent run also passed through the browser MCP
firewall against the local practice form. It filled the expected first name,
last name, and email, returned `VERIFICATION_OK`, and left both submission and
attestation false. The trusted browser controller now forwards `--slot` on
focus and login, which keeps each application in its isolated Chrome profile.

Local browser verification passed for `/` and `/settings`: the Home and
Settings pills are centered in the top app bar between the logo and theme
control, the active pill follows the route, and Settings shows OpenAI,
Anthropic, and Gemini cards labelled Your Device. No left rail, Back to
applications control, OCI worker, or Cloud Agent appears in the active UI.

Soft Premium browser verification passed at desktop and 390 by 844 phone
sizes. System, Light, and Dark switch in place without console errors. The
phone page has no document-level horizontal overflow. The Dropped control is
absent from the five-tab workflow, expands at the lower left, and opens its
searchable archive dialog. Found cards use a one-row desktop treatment, stack
on phones, and expose their existing details by hover or explicit activation.
Trusted logo URLs render through the same-origin proxy, with monograms left in
place when no trustworthy employer image exists. The local generated Found
page resolved 891 of its 906 rendered records to a trusted employer logo URL.
The remaining 15 use monograms until a later source scan supplies a feed logo,
direct employer domain, or matching employer-owned website link.

Current branch verification ran 1,168 Python tests at the host level. All
passed, with one intentional skip, including the Chrome PDF render cases. The
installed launchd service supplied a second real render proof through the
successful IFS job. All 45 Node tests pass. Local browser
verification measured the Home and Settings control at the exact horizontal
centre of the top app bar and found three provider cards with no Back to
applications control.

The Found location picker was also verified in the real dashboard at desktop
and phone widths in dark mode. It rendered 199 complete location names across
six continents with no picker-level horizontal overflow or console errors.
Clear all produced zero matches, selecting North America checked all 23 child
countries, Canada's `Only` action left one country, and Worldwide restored all
199 locations. Long names wrap instead of truncating.

Found date contract, added 2026-08-24: show, filter, sort, and retain by
`postedAt` only. Never substitute `firstSeenAt`; it is internal discovery
metadata. Never relabel a source's `updated` field as its posting date. Workday
CXS `postedOn` values with an exact age resolve to an Eastern calendar date.
Sources without a genuine publication field stay `Posted date unknown`. See
`docs/handoffs/claude-posted-dates-2026-08-24.md` before changing connector or
Found payload date fields.

The exact Luna model probe and the real Luna document and form-agent paths
passed. Anthropic and Gemini have no API keys on this Mac, so their exact live
model calls remain intentionally unverified until the user connects each key
in Settings. Their connector, environment isolation, model pinning, schema,
health, and failover paths are covered by automated tests. Do not report a live
Claude or Gemini pass without connecting a real key and running both contracts.

Production follow-up on 2026-08-24: two Versant retries produced parseable
OpenAI JSON but failed before content validation because strict output cannot
encode base-resume-dependent list lengths. The worker now inherits the complete
verified base resume when an OpenAI override has an invalid dynamic shape, and
sets letter employer and role from the trusted request. Do not remove that
fallback or broaden it to malformed prose. ATS, layout, cover-letter, and file
checks still fail closed. See
`docs/handoffs/claude-application-worker-2026-08-24.md`.

PR #5 is the production release for the provider health, failover, dedicated
control service, secure API-key connectors, browser slot fix, and resume
integrity gate. Its Vercel preview passed before merge. The Vercel functions
call Cloudflare KV through its REST API. There is no separately deployed
Cloudflare Worker in this request path. The page function renders
`jobby:board-state` with the renderer bundled in its own Git commit. Do not
restore active HTML serving from `jobby:board`; that value exists only for the
rolling migration from deployments created before PR #13.

Do not run `python3 scripts/publish.py --deploy` as a substitute for merging
the PR. Publishing updates sanitized state and application documents in KV.
Git deployment owns the renderer. After changing any renderer module listed in
`scripts/sync_vercel_renderer.py`, run that script before testing or committing.

## Worktree discipline

**The checkout the launchd plist points at is production. Nothing but `main` is
ever checked out there. All agent work happens in worktrees, without exception.**

The three services execute
`/Users/sahilsapra/Documents/Development.nosync/jobby-auto-apply/scripts/*.py`
by absolute path. A branch checked out there is not a branch, it is a
deployment, and applications submitted from it go out under a real name and
cannot be recalled. On 2026-08-25 the daemon spent part of an afternoon running
an unmerged branch in a transient rebase state while three finished fixes sat on
`main` doing nothing.

This class of failure has now cost the project three times: the conflicted merge
that took the board down, the force-push that ate three commits, and this. The
rule costs nothing, so it holds without exception.

Corollaries, each of which was learned the expensive way:

- Announce in writing before moving that branch or restarting a service, and say
  which you are taking when two sessions could reach for the same one.
- Merging is not deploying. A long-lived Python process does not reload modules,
  so for anything a daemon **imports**, the arming event is the restart. For
  anything it **spawns**, the pull is the arming event and the file is live on
  save. **Ask which, per file, never per repo**: a deploy carrying five changed
  scripts is routinely both at once, and on 2026-08-27 two sessions asserted the
  wrong half of it about `browser_mcp_proxy.py` in a deploy announcement before
  anyone ran the one `grep` that settles it.

  This used to say `scripts/brief.py` is *the* exception. Measured: `watch.py`
  spawns fourteen scripts by path — `apply_marks`, `document_worker`, `drop`,
  `finish`, `ingest`, `phase`, `promote`, `provider_login`, `run_apply`,
  `run_scan`, `search_config`, `store`, `sync_board` and itself — and imports
  `agent`, `browser`, `document_job`, `provider_login`, `providers` and
  `toolpath`. `provider_login` is on both lists — imported at `watch.py:54`,
  spawned at `watch.py:827` — so the granularity is the call path, not even the
  file.

  **When a doc says "X is the exception", suspect that X is the only case
  anyone checked.** `brief.py` was not the exception, it was the instance
  somebody happened to notice, and the rule got written around the observation
  rather than the mechanism. The other fourteen were in the same file the whole
  time, findable by the same grep.

  The trap is that the two travel together. `browser_mcp_proxy.py` is spawned
  and live on pull, while `browser.py`, which builds its argv, is imported and
  is not. A pull without a restart delivers the launched thing and not its
  launcher.
- After a restart, confirm from each daemon's own start time against the
  checkout's reflog which SHA it is actually running. A healthy-looking process
  can be executing code from hours ago.

This checkout also contains user-owned modified and untracked work unrelated to
any one change. Inspect diffs by path. Do not delete, rewrite, stage, or fold
those files into your work. In particular, leave sourcing investigations,
captured job pages, and schema experiments alone unless the user names them.

## A pull of the production checkout is a deploy

**Announce it before it happens, restart every service immediately after, and
verify each service's own start time against the reflog. A pull without the
restart and the verification is a half-deploy, and half-deploys are forbidden.**

Not because they leave the system on old code. Because they leave it running
**two commits at once**. The three long-lived daemons hold whatever SHA they
started on in resident memory. Every subprocess they spawn from that moment on,
every publish and ingest and sync, loads the new SHA off disk.

That split is why the mistake is easy to make and hard to see. A change that
lives in a spawned path really is delivered by the pull alone, so whoever pulled
can test their own change, watch it work, and report it live in production
without having said anything untrue about it. A change resident in a daemon is
not delivered at all. Both halves are true at once and nobody chose the
configuration.

It stays benign while the two commits are additive. It stops being benign the
first time the resident half and the spawned half share a contract, and that day
will not announce itself.

Measured on 2026-08-27: the checkout moved to `260048d` at 17:16:04 while all
three services still carried 16:07 start times from the previous deploy to
`18e70c3`. Found by comparing start times against the reflog, which is the only
way any instance of this has ever been found. It is the fifth of this shape in a
week and the first caught while it was still live.

So: one owner per deploy, announced before, and these four in one action rather
than one of them.

```
0. take the deploy lock, and stop if somebody already holds it
1. announce it, and say which services are going down
2. pull the production checkout, staying on main
3. restart all three services, dependencies before the orchestrator
4. verify no reflog entry fell between the pull and the restarts
5. release the deploy lock
```

Step 0 is the one that actually excludes a second owner, and step 4 is stricter
than it used to be for a reason given below. Both are cheap:

```bash
python3 scripts/deploy_lock.py take --owner <session> --note "<from> -> <to>"
```

It refuses with exit 1 and names the holder. `watch.py --status` reports it
under `deploy`, so the gate command everybody already runs answers this too and
there is no second command to remember.

**A held deploy that is announced is completely fine.** If there is a reason to
stop halfway, a run about to start or a collision you can see, say so where the
other sessions will read it. What is forbidden is the silent half. The rule is
not about the pause, it is about nobody knowing the system is in two states.

### Knowing it is safe, before step 2

The rule above says restart every service. It did not say how to know that is
safe to do, and on 2026-08-28 that gap cost a near-miss.

**Ask `watch.py --status` and read `locks`. One command, already pid-checked.**

```
python3 scripts/watch.py --status
```

```json
"locks": { "apply-1": null, "apply-2": null, ... , "scan": null }
```

- `null` is a free slot.
- A lock with `"parked": true` holds a slot for an application waiting on the
  user. There is no process behind it and restarting over it is safe.
- **Anything else is a browser in the middle of somebody's application.** That
  is the one to wait for.

Corroborate with `document-tasks/` being empty, which is document jobs rather
than form runs, and with no session in status `inflight`. Three checks, one
command each, and they cover different work:

```
watch.py --status, locks all null or parked  -> no form run mid-flight
~/.job-application/document-tasks/ empty     -> no document job mid-flight
no session status "inflight"                 -> corroborates both
```

**The worked example.** A session checked `document-tasks/`, found it empty,
and restarted. `apply-1` had been claimed 88 seconds earlier by a live form run
that its check could not see:

```
apply-1 taken at    : 23:56:26   pid 12927, live, not parked
checkout moved at   : 23:57:46
watcher restarted at: 23:57:54
```

It resolved benignly. The runner is a spawned child rather than the service, so
`kickstart -k` did not kill it; it ran to its own end and released the slot, and
the new watcher never had to arbitrate a lock held by a process it did not
spawn. But the application was mid-form with no parent for the duration, which
is not a state anyone chose, and had it been mid-submit rather than at "Queued"
the cost would have been a duplicate application under a real name.

**And the reason the rule names a command rather than a file glob.** That
session's first check was `ls ~/.job-application/locks/apply-*.json`, which
returned "no matches found" because the files are `.lock`. A wrong glob and an
empty directory are the same output. A safety check whose failure mode is
indistinguishable from success is not a safety check, which is why this asks a
program that fails loudly instead of a pattern that fails silently.


### An announcement is not a lock

**Announcing a deploy tells nobody anything until they read it. Two sessions can
both announce, both be right, and both be the owner. Re-read the inbox
immediately before pulling, not just before gating.**

On 2026-08-28 that happened. Two sessions announced a deploy of the same
production checkout from the same base, `cc860a3`, within about a minute of each
other. Both named themselves sole owner. Both had measured a clean gate and
reported the numbers, and the numbers agreed: nine locks free, `document-tasks/`
empty, nothing inflight. Neither had read the other's message when they sent
their own, because both were written in the gap between finishing a gate and
acting on it, which is exactly the gap the other's announcement lands in.

**The gates were not insufficient. They were irrelevant.** Every reading either
session took was about the machine, and the hazard was another session's
intention, which leaves no trace in the machine until it acts. `watch.py
--status` cannot see an agent that has decided to deploy and not yet moved. So
more checking does not help, and adding a sixth check to the list above would not
have caught it. That is why this rule sits here rather than in the gate list.

**Be precise about what the hazard was, because the obvious fix is the wrong
one.** It was not that the two would have deployed different trees. Both
intended `git pull --ff-only origin main`, and whenever either ran it that day it
would have landed `1a69452`, because that is what the ref held. The hazard was
two sessions cycling the same three daemons minutes apart, each believing it
owned the window, each having gated correctly against a machine state that said
nothing about the other's intention. Writing this up as a SHA divergence invites
the remedy "announce a SHA and pull exactly that SHA", which would not have
helped here and would have made the deploy that ran carry less: the session that
moved announced `cc860a3 -> c5671b6` and landed `1a69452`, six commits further
on, because `c5671b6` had been superseded between its announcement and its pull.

**What luck supplied was the timing, and calling it a protocol success would be
false.** The second PR merged into the gap between the first session's
announcement and its pull, so the two targets converged instead of arriving
minutes apart. Reverse those sixty seconds and one session restarts three
services that the other has just brought up. Nothing in either gate would have
been different.

Measured from the production reflog rather than from anyone's account of it:

```
01:26:51  cc860a3
01:45:05  1a69452   announced as cc860a3 -> c5671b6
01:51:39  28f9970   announced as 1a69452 -> 28f9970, landed as announced
```

`git merge-base --is-ancestor c5671b6 1a69452` is true, six commits apart.

What did work was social and it is worth copying. The second session read the
first's announcement before pulling, stopped, said "I have touched nothing", and
asked which of two options to take rather than proceeding on the assumption its
own window was clear. Nothing was lost because somebody held.

**Three sessions independently noticing an unwritten rule is evidence the rule is
needed. It is not evidence that any of them was authorised to write it.** Those
are different claims, and the second rides in on the first very easily. While
deconflicting who should write this section, one session offered another "take it
if your user says go and I will stand down", which sounds like coordination and
functions as the missing half of an authorisation the other's user had not given.
A peer's willingness to defer is not an instruction. The rule the two settled on
is the one to keep: neither writes until one has an explicit instruction from
their own user, and whoever gets it announces before writing rather than after.

**It happened twice in ninety minutes, and the second one was not a near-miss.**
`jobby-auto-apply-71` pulled at 02:11:31 and restarted at 02:11:44.
`nervous-cerf` pulled at 02:12:09, twenty-five seconds *after* those restarts,
leaving the daemons resident on `3a34a51` while the disk read `eb27ec8`. That is
the two-commits-at-once state this file already forbids, produced by two sessions
that both announced and both gated correctly. Checking only "the start times
postdate the entry I expect" passes for each of them separately, which is why
step 4 above now asks whether any reflog entry fell *between* the pull and the
restarts. That is the check that caught it.

**So the fix is a lock, not another rule about telling each other things.** Every
mechanism that failed here was a message. `scripts/deploy_lock.py` is the lock: a
second session finds it held rather than has to be told, and the refusal names
who holds it, because the next question is always who.

It is a lease rather than a pid lock, and `runlock` learned about leases rather
than gaining a sibling module. A deploy is a pull, three restarts and a
verification, a dozen short-lived commands with no single process alive across
them, so a pid-alive test would report the lock free while the pull was still
running. Two internally consistent locks that do not exclude each other is the
race, not the fix, which is the same argument `runlock`'s own docstring makes
about the scan lock.

The lease expires, twenty minutes by default against measured deploys of eight
and fourteen seconds, so a session that dies mid-deploy cannot wedge production.
**Nothing renews it in the background on the holder's behalf**, because a lease
that renews itself while its owner is gone is exactly the wedge it exists to
prevent; `extend` is deliberate and needs the token. `break --reason` takes it
away and says on stderr who it was taken from, so it can never happen quietly.

What the lock does not do is make the announcement pointless. The lock stops two
deploys; the announcement is still how everyone else learns their slots are about
to go away.

**Arming is a property of each file, not of a deploy, so any single sentence
about when a deploy arms is false.** Not "false when the deploy is mixed": almost
every deploy here is mixed, so this is the general case. A changed file reached by
a resident import arms on the restart; one reached only by a spawned process arms
on the pull; and one deploy routinely carries both, which means neither the pull
nor the restart is "the" moment.

The session that carried `eb27ec8` told five sessions "#120 arms on the restart,
because `watch.py` and `browser.py` are resident imports". True of #120 and false
of the deploy it was describing:

```
scripts/watch.py          resident  -> arms on the RESTART   (#120)
scripts/browser.py        resident  -> arms on the RESTART   (#120)
scripts/answer_record.py  spawned   -> arms on the PULL      (#122)
scripts/r2_sync.py        spawned   -> arms on the PULL      (#121)
```

So the window between that pull and the last restart ran the spawned halves of
two PRs against the resident half of a third. It cost nothing, and the reason is
narrower than "nothing was running": the commit in the gap was #122 touching
`answer_record`, which nothing resident imports. Had the gap held a change to
anything the daemons hold, three freshly started services would have been running
one commit behind their own disk with nobody aware of it.

Three reasons this earns a line rather than a war story. It is invisible by
construction: both halves are correct code, nobody chose the configuration, and
the only symptom appears when the two halves share a contract. An announcement
naming a single arming event is worse than one naming none, because the next
reader reasons from it. And the wrong single-sentence version is very easy to
say: it went to five sessions in good faith and stood unchallenged for twenty
minutes among people actively hunting deploy faults, then took three sessions
converging from different directions to correct. Do not trust anyone, including
yourself, to derive this per deploy. Enumerate per file.

The earlier guidance "restart when something a service imports has changed" is
necessary and not sufficient, for the same reason: it answers per change, and a
deploy is not one change.

Worth building later and deliberately not built here: `deploy_lock` could derive
that table from the import closure of the two service entrypoints instead of
asking a person to assert it. It is not built because a statically inferred
arming map that is quietly wrong about a dynamic import or a `subprocess` call
built from a string would be believed, and a confident wrong answer here is worse
than the honest table above.

**While the lock does not exist, verify the restart against the HEAD that
actually held when the services came up, not against the SHA you meant to
deploy.** Main moved four times in forty minutes that evening under several
sessions. One session pulled a SHA, somebody fast-forwarded the checkout between
its pull and its restart, and its daemons armed on a commit it had not deployed;
it only knew because it read the reflog rather than its own intent. The third
deploy of the night is the contrast case and the cheap check: it confirmed no
reflog entry fell between its pull at 01:51:39 and its restarts at 01:51:47,
which is the only reason it could say the services came up on what it announced.

One thing the gate list genuinely was missing, and it is a different fault from
the one above. Work running from a *worktree* against production's browsers holds
no production lock and passes every check in the previous section. A live
`browser_mcp_proxy` was found this way during the same window, and it turned out
to be another session's `test_apply_end_to_end` against a real Chrome. So:

```
ps aux | grep -E "run_apply|document_worker|browser_mcp_proxy" | grep -v grep
```

The production `document_worker` appears in that list every time and is not work
in flight. Read each pid's full command line with `ps -o command= -p <pid>` when
the answer depends on a flag: the same evening, a session piped `ps` through
`cut -c1-240`, lost `--use-mock-keychain` past the truncation, and nearly
reported a correctly-launched browser as stale. Truncate for display, query for a
decision, never the same command for both.

## A change is done when the running system consumes it

**A merged file, a written pack, or a deployed bundle that nothing reads is not
shipped. "Demonstrably" means the consumption is observed, not inferred.**

Three instances, one class:

- The watcher kept running code from before a merge, because a long-lived
  Python process does not reload modules. The arming event was the restart.
- `deploy/renderer/` fell behind `scripts/`, so three commits of UI work were
  merged, deployed, and invisible.
- `references/probed-tenants.json` gained 34 verified tenants, merged and
  deployed, and the next sweep moved one job. Nothing read the file except a
  command someone had to remember.

Each looked finished. Each passed its tests. In each the artefact existed and
nothing consumed it, and in each the gap was found by measuring the outcome
rather than by checking the change.

So: name the consumer before calling something done, and observe it consuming.
`sync_vercel_renderer.py` in CI, the pack applied at the start of `ingest.run`,
the restart confirmed against the reflog. Where the consumption can be made
structural rather than a step to remember, make it structural; a rule that
depends on the next person remembering is a rule already broken.

## A clean merge is not a correct merge

**When two branches change the same invariant from different files, git's
success tells you nothing. The merged code has to be re-checked against the
invariant by running the property's tests, not by observing the absence of
conflict markers.**

On 2026-08-26 the Memory Bank branch added a rule to `store.py`: an answer entry
with no value is a question the store is tracking, not an answer to it, so it
must never satisfy a lookup. `find_answer` and `topic_answer` were taught it.
The merge from `main` then brought `acknowledgement()`, `expected_pay()` and
`us_person()` from another branch, three new resolvers each iterating
`answers.values()` and returning the first tagged entry, none of which knew the
rule existed. Git reported a clean merge. It was clean. It was also wrong in
three places, and every test on both sides still passed, because each side's
tests only covered the code that side wrote.

The tell is that a merge conflict is a *textual* overlap and an invariant is a
*semantic* one. Two branches editing the same line conflict. Two branches where
one adds a rule and the other adds code that has to follow it do not, and that
is the more dangerous case precisely because nothing stops to ask.

So, after any merge that touched a file where this branch established or relied
on an invariant: name the invariant, find every site that could violate it in
the merged tree, and run the test that proves the property rather than the test
suite as a whole. Where the invariant can be enforced in one place instead of N,
move it there while you are looking at it: the fix above was to filter once in
`find_answer` and pass the filtered set to every resolver, so the next one
inherits the guard by construction rather than by somebody remembering.

This is the semantic sibling of "a change is done when the running system
consumes it". Both are the same failure: an artefact that exists, passes, and
does not do the thing.

## Substring matching on a semantic category is a bug waiting for a corpus

**Never test category membership with `marker in text`. Use word boundaries.**

This has now cost three sessions an afternoon each, from three directions:

- `"india" in "Indiana"`, which filtered a US state into an India location bucket.
- `"age " in "average "`, `"age " in "manage "`, and `"race" in "embrace"`, which
  flagged "What is your average deal size?", "How do you manage a team?" and "Do
  you embrace ambiguity?" as sensitive compensation and demographic questions.
- The same shape in marker lists that recognise legal topics, where "authoris"
  alone matches both a work-authorisation question and a background-check
  authorisation, which are opposite answers.

It is always the same failure and it is always invisible in review, because the
marker list reads correctly. `("race", "gender", "veteran")` looks like a list of
demographic terms. It is a list of substrings, and the corpus decides what that
means.

Compile a regex with `\b` on both ends, measure it against the real store or the
real pool before trusting it, and report the false positives you found rather
than the fact that it passed. A classifier is not verified by reading it. The
word-bounded version above went from 4 of 4 generic questions wrongly flagged to
0 of 7, measured against the 156 real answers, and that measurement is the only
reason anyone knows it works.

### And sometimes the word boundary is the wrong fix, 2026-09-03

**An exclusion list on a thing's *role* is a small closed set. An exclusion list
standing in for a *name-matching rule* grows forever and is always one company
short.** Which one you have decides whether a boundary test helps or hurts.

`company_logos.website_from_description` accepts a link whose host contains a
token of the company name, `token in compact_host`, and it has the defect above
in principle: `Ford` resolves to `stanfordalumni.com`, `Vast` to
`devastating-labs.io`, `Apex` to `grapexchange.net`. So the obvious repair is a
label-boundary test.

**It was written, run against all 351 stored employer domains, and it lost.** A
plain boundary test refuses `hioscar.com`, `onepeloton.com`, `getmoss.com`,
`getstream.io`, `withpersona.com`, `mybrightwheel.com`, `getrecharge.com` and
`ridewithvia.com`, 188 postings, because `get-`, `my-`, `with-`, `one-`, `hi-`
and `ride-` are a domain convention rather than a collision. An affix-tolerant
version that refuses a trailing word refuses 29 of which about sixteen are
correct: `davies-group`, `redditinc`, `cusointernational`, `flockcover`,
`toasttab`, `surferseo` and `ouraring` all have a word after the name and all
belong to the company, and five more are a public-suffix bug (`tiro.co.uk`,
`mable.com.au`, `paypay.ne.jp`) needing a list this codebase does not carry.
Across all 351 the substring test has produced **one** likely bad row the role
rules do not already catch.

What does work looks at the host's role and never at the name: refuse a careers
or content first label (28 of 351), refuse a general-purpose hosting domain (6
of 351, and these are the ones that draw a stranger's mark -- Dropbox renders
GitHub's octocat), then rank the candidates instead of taking the first.

So the rule is not "always use word boundaries". It is: **the corpus decides,
and a rule that has not met it is a guess.** The substring finding above was
measured into existence and this one was measured out of it, by the same method.
`docs/plans/phase3-inputs.md` section 11 carries the numbers.

### A rule that has never collided has not been tested, 2026-09-03

**205 applications produced 205 distinct document filenames, and that was a
property of the corpus rather than of the rule.** `document_contract.
output_names` builds a name from the person, the company and the role, and
probing it directly found three ways two applications collide: `_slug` drops
punctuation so "Senior PM, AI" and "Senior PM - AI" are one name, the role is
capped at 55 characters so two long titles sharing a prefix are one name, and
the same title twice is trivially one name.

None of those had ever happened. What had happened was the same failure one
generation earlier, before this contract owned naming: **three Glance
applications to three different roles all claim
`Sahil_Sapra_Glance_Resume.pdf`.** KV is keyed by filename and holds one
object, so the board serves the same resume for Product Analyst II, Senior
Product Manager and Product Manager, and nothing anywhere records which
application received it. Datadog, Cohere, Plaid, Innovaccer and Ramp are the
same shape: 14 documents, 6 filenames.

The consequence is not only display. `r2_artifacts` refused to attribute those
files, correctly, so they are the 14 that never reached R2 -- and had the id
tag existed earlier they would all have been attributable. **A latent collision
in a naming rule is a data-loss bug with a delay on it.**

So: a rule whose uniqueness depends on the values it happens to have seen is
not unique, it is lucky, and the fix is to make it unique by construction. Six
characters of the application's own id, and the corpus stops being load-bearing.

This is the sibling of the substring finding above and points the other way.
There, the rule read correctly and the corpus decided what it meant, so the
corpus had to be consulted. Here the corpus said the rule was fine and the
corpus was wrong, because it had not yet contained the case. **Measure against
the corpus to find what a rule does; do not conclude from the corpus that a
rule is safe.**

### A syntax check is not a name check, 2026-09-03

`python3 -m py_compile` passed a call that referenced `application_id` in a
function where the variable is called `aid`. It would have raised `NameError`
on the first real document build.

`py_compile` compiles; it does not resolve names, because Python resolves them
at runtime. So it answers "is this parseable" and reads as "is this correct",
which is the shape this file already records for `migrate.py status` comparing
the migrations table against the directory, and for `GATE_PATHS` naming the
caller rather than the code that writes.

What caught it was reading the function to see which name was bound. What
*would* have caught it is running the thing, which is the rule already written
here twice. When a compile step is the only check between an edit and a commit,
it has told you about syntax and nothing else.

### The same rule, applied in one file and not its neighbour, 2026-09-03

**`dual_write.read_pool` refuses a zero-byte pool and says exactly why.
`jobs_store.load_jobs`, in the same repository, returned `{}` for a pool that
was not there.** One function had the rule written into its docstring; the one
next to it had the same failure open.

```
dual_write.read_pool   "a read that failed and a store that is empty produce
                        the same value, so an outage reads as an empty pool and
                        every count downstream is confidently wrong"
                        -> zero bytes is broken

jobs_store.load_jobs   store.read_json returns the default when the path does
                        not exist
                        -> a deleted jobs.json is {}
```

What it would have cost, measured against the live pool: an absent pool reads
as empty, the next sweep repopulates from the connectors, and the device loses
**16 watched, 100 applied and 11 passed**. Those 127 decisions are in Postgres
and `jobs_store` contains zero SELECTs, so nothing reads them back. Eleven jobs
already passed on would be re-offered, with no error anywhere.

**The plan had written the test for it.** Phase 1's negative tests say "a read
that gets zero rows must be distinguishable from a read that errored", naming
the `json.loads(stdout or "[]")` bug as the shape to design against. That test
was satisfied in `read_pool` and left open in `load_jobs`, in the file
`GATE_PATHS` names.

Two things generalise. **A rule stated in one function's docstring is not a
rule the codebase holds**, and the closer the neighbour the less likely anyone
checks -- `read_pool` and `load_jobs` read the same file for the same reason.
And **a negative test written in a plan is not a negative test that exists**;
this one was written before the code and satisfied in one of the two places it
applied.

The fix is narrow on purpose. A fresh root has neither pool nor companies and
is legitimately empty; a store that has swept has both, so the pool alone being
gone is a deletion. Refusing every missing file would have failed every test
that starts from a temp directory, which is a different bug and one the
mutation test catches.

#### And it recurred one file over, three days later, 2026-09-08

**A connection held across a wait is not a bug that gets fixed. It is a shape
that gets fixed once per file.** `sweep_worker.stage` was changed to take a
`connect` callable on 2026-09-05, with a docstring saying why: the first full
sweep opened a connection at the start and held it while the fetch worked, and
the stage after ashby's twenty-four minutes died with `consuming input failed:
SSL connection has been closed unexpectedly`. Neon closes an idle connection
long before a 795-board sweep is done.

`cloud_sweep.main`, next door, opened one connection, called `start_machine`,
waited **29.5 minutes** on it, and then used it. Same exception, same cause,
three days later, in the function that reads the seam the fixed one writes. The
run reported a failure **after** the container had finished writing all 27
channels: a completed fetch, reported as a failed run.

The rule, because two instances in one feature is a pattern and not an
accident: **anything that waits on a container connects after the wait, never
before it.** A connection opened before a wait is a connection whose idle time
is the wait, which is exactly the thing being waited for. `wait_for_sweep` takes
a callable and opens one per poll; `staged` opens its own afterwards.

And the guard is a test that reads `main`'s source and asserts the ordering --
`jobs_db.connect()` appears nowhere before `wait_for_sweep(`, and does appear
after -- because the fault is invisible in every unit test. Nothing in a suite
is idle for half an hour, so a held connection passes everywhere except
production. Verified by mutation: reinstating the pre-wait connect fails it.

#### A chokepoint premise needs a test, 2026-09-08

**`store.write_private` said it was "the only function that puts bytes in the
store", and that was false six days before the code relying on it was
written.** `ProviderRegistry._write_locked` has had its own atomic write since
2026-08-23; `class_b.py` arrived 2026-08-29 and hung the class B mirror off
`write_private`. So provider state was declared class B in prose, had no row in
the table `write_path_coverage` derives from, and was never mirrored once. The
absence was silent by construction: `dual-write.log` holds 2,771 lines and not
one mentions a provider, because a mirror that broke would have complained
2,771 times and this one never tried.

The guard is a census, not a rule anybody has to remember.
`tests/test_the_store_has_one_write_chokepoint.py` derives every `os.replace`
under `scripts/` from the AST and requires each to be the chokepoint, a
declared class B writer that hands its write to `store.after_private_write`, or
exempt with the reason it writes nothing mirrored. Fourteen today, all
accounted for. **A premise nobody can check is a premise that quietly stops
being true.**

That is the third census this week and they are one move: the aggregator-host
list checked against every connector, `GATE_PATHS` checked against
`backfill`'s whole import closure, and now every atomic writer checked against
the chokepoint. **Enumerate the class from the code, never from a list beside
it.** Each of the three had a list that was correct and incomplete, and in
each case only the enumeration said so.

#### A timestamp in a mirrored row is the source's clock, 2026-09-08

`provider_links.updated_at` read 2026-09-02T03:38Z and that was taken as the
day the mirror stopped. It is `openai.last_checked_at`, a field *value* copied
out of `providers.json` when a hand-run backfill wrote the row at 21:25:38. A
mirrored row's timestamps describe the thing mirrored, not the mirroring; the
only column that dates the write is the one the writer stamps itself, and here
all three rows shared it to the microsecond, which is what one transaction
looks like.


#### A count is not the thing it counts, 2026-09-08

`sweep_runs.unchanged` carried how many boards answered 304, which is the whole
fact for the coverage ratchet it was designed for. The canary asks *which*
board, `cloud_sweep.staged` rebuilt the envelope from the count alone, and an
empty slug list is indistinguishable from "nothing was unchanged" -- so a warm
sweep reported its two strictest sources dead every time it ran. Migration 0022
carries the slugs.


### "Could not look" is never "is not there", 2026-09-03

**A check that cannot read its subject must say so in its own vocabulary. It
must never return the shape of an empty answer.** Absence and inability are
different facts, they lead to different actions, and only one of them is about
the thing being asked about.

Five instances, three of them from one week, and the earlier two are the
expensive ones:

```
ping_health        network error -> None, and every caller ignored it, so a
                   misconfigured alarm was indistinguishable from a healthy
                   system
pool_staleness     raised, a broad except returned None, and None reads as
                   "nothing to say" -- an alarm that could not fire on any
                   machine, shipped as the fix for an outage nothing alerted on
deploy_verify      an unreadable start time or reflog is a failure naming
                   which, not a pass; a file written inside the second `ps`
                   reports is INDETERMINATE, not fresh
/api/artifact      a missing credential is a 503 naming the four variables,
                   never a 404 -- "no such document" for "this deployment
                   cannot reach the bucket" sends the reader hunting a file
                   that is sitting there
storedArtifacts    returns null when the listing fails, never an empty set. An
                   empty set would have made all 104 Submitted rows say their
                   document could not be attributed: a claim about 104
                   applications drawn from a claim about one environment
```

The tell is that the empty answer is always the convenient one. `None`, `[]`,
`{}` and 404 all flow through the rest of the code without special handling,
which is exactly why they get reached for, and the cost is that the failure
becomes invisible at the point where it is cheapest to see.

So when writing anything that reads: **name the third state.** Not found,
found, and could not look. If the return type has no room for it, that is the
bug -- and the caller has to render it differently, because a reader who is
told "not captured" behaves differently from one told "storage could not be
read".

## A scanner's input is text, and text about a thing looks like the thing

**Any check that reads source has to know which regions are code. Comments and
docstrings are the region that always lies, because their entire purpose is to
contain a description of the thing being scanned for.**

Three instances, and the shape only became visible on the third:

- `"india" in "Indiana"` filed a US state under India.
- `("race", "gender", "veteran")` flagged "Do you embrace ambiguity?" as a
  demographic question.
- The v2 control scan, which requires every `<button>` in a lane to carry
  `disabled` while writes are gated, failed on a `<button>` inside a comment
  **explaining that rule**.

The first two are written up above as a word-boundary problem and that is the
right fix for them. The third is not a boundary problem at all: `<button>` was
a whole token, correctly matched, in a region that was never code. Same
symptom, different cause, and a `\b` would not have touched it.

**The general fix is to parse rather than to grep.** An AST never yields a
comment or a docstring, so a call written in prose is not a call and cannot be
mistaken for one; `tests/test_corpus_check.py` derives its module set by
walking `ast.Call` nodes for exactly this reason, and pins the property with
two tests that feed it a skip written inside a comment and inside a docstring.
Where a regex is genuinely the tool -- a scan for a CSS class, a token in
markup -- strip the comment regions first and say in the test that you did,
because the next person will read the pattern and not the preprocessing.

**The mirror-image fault is a scanner that reads code correctly and asserts its
shape rather than its behaviour.** `test_it_is_actually_wired_into_the_dead_man_ping`
found the dead-man composition by `SOURCE.index('stale = "; ".join(')` and
checked the warning appeared before `if message`. Its intent was exactly right
-- a warning function nothing calls is the `activity.json` counter again -- and
it broke the moment that composition was lifted into `unhealthy_reason()` so it
could be called at all. A check that fails on the refactor that improves the
thing it guards is the `GATE_PATHS` equality test in a new costume: its subject
is the arrangement of the code rather than the property the arrangement is for.
It calls the function now, silencing four warnings and asserting the fifth
comes out, and it covers all five rather than the one it was written for.

## A notification commits first; the work itself enqueues first

**Ordering between a database commit and the side effect beside it is decided
by what the side effect *is*, and the two cases point opposite ways.**

`writes.publish` commits first, and its docstring gives the reason: "A
published event with no row behind it is a board showing a change the database
does not have, and it vanishes on the next reconnect, which is worse than a
slow board because the user watched it work." Failing after the commit is the
recoverable direction -- the row is right, the board is briefly stale, the next
fetch corrects it.

**Every clause of that is false of an enqueue.** A lost publish self-corrects
on the next fetch. A lost enqueue means the work never happens, and nothing
ever fetches it back. So an enqueue goes *before* the commit: nothing gets
recorded that will not happen. The failure directions then read

    enqueue ok, commit fails   the daemon does the work and writes the row
                               itself through dual-write, which is exactly
                               what a board click does today
    enqueue fails              the request errors, nothing is written, the
                               user retries

The general form: **a notification must never describe a state the store does
not hold, so it commits first. Work must never be recorded as done when it was
never scheduled, so it enqueues first.** Getting this backwards produces the
worst failure this project has: a board that says it did something, and a
machine that never heard.

Phase 3 needs this, because the reconciliation between a v2 decision and the
device's work queue is exactly a case of it.

## A synthetic record silently satisfies every predicate it has no field for

`backfill.upsert_postings` decides a board-key collision with

```python
if holder is not None and BOARD_KEY_WINNER({"id": holder}) <= BOARD_KEY_WINNER(record):
```

The incumbent is not a record. It is `{"id": holder}`, a stand-in carrying one
field, because `BOARD_KEY_WINNER` reads one field.

**So widening that function silently disables the widening.** Teaching it to
prefer a live posting over a closed one -- `(bool(record.get("closedAt")), id)`
-- makes the synthetic evaluate as `(False, holder)` on every call, because a
dict with one key has no `closedAt` and `.get` returns None. The incumbent is
then permanently "live", a genuinely live arriving record can never displace a
closed stored one, and the change is inert. Every test drawn from the real
pool still passes, because the pool is where the behaviour was measured and
the behaviour did not move.

That is why the fix is four edits and not one: the sort key, the `held` query
gaining `closed_at`, `held`/`seen` carrying it, **and the call site**. Three of
the four are visible in a diff and the fourth is the one that matters.

The class, which is what to carry forward: **a stand-in built to satisfy one
predicate answers every other predicate with a default, and a default is
indistinguishable from a real value that happens to equal it.** Same shape as
`assertNotIn("chrome_drift_warning", ...)` asserting the absence of a name
nothing ever used -- both read as a check and neither can fail. When widening
what a function reads, grep for callers that construct its argument rather than
pass a real one.

## An adversarial pass that corrects the reason is worth more than one that agrees

**A right finding with a wrong reason is how a fix lands in the wrong place.**

A review sweep over the corpus-check branch reported that
`test_the_tick_consults_it_before_any_document_work` was fragile, and gave a
reason: it "survives only because the corpus spawn landed in `main()` rather
than in `tick()`". The verifier that took that finding did not stop at
confirming the verdict. It mutated `tick`'s source in memory, inserted the
spawn at the top of `tick` and again immediately before the ensure call, and
re-ran the four assertions:

    real tick() as on the branch                 PASS
    corpus spawn inserted at the TOP of tick()   PASS
    corpus spawn inserted just before the ensure PASS
    ensure extracted into a helper               FAIL  -1 not greater than -1

The test asserts marker *ordering*, and the spawn block contains none of the
four markers, so putting it inside `tick` could never have broken it. **The
fragility was real and the stated cause was false**, and had the reason been
taken on trust the remedy would have been a rule about where spawns may go --
which protects nothing, because the actual trigger is any extraction or rename.

The verifier then found a second failure mode the finder had not seen at all,
and reproduced it: `inspect.getsource` re-reads from disk and
`linecache.checkcache` invalidates on mtime, so an edit to `watch.py` while the
suite runs makes it walk back from a stale `co_firstlineno` into a different
block. Verified again independently before this was written, by inserting sixty
lines above `tick` in a running interpreter: `getsource` returned a block whose
first line was `# shifted` and the search returned -1. On a checkout several
sessions share, **that is a CI failure indistinguishable from a real break**.

So the rule for a verification pass: confirm or refute the *mechanism*, not
just the verdict. A verifier that only answers "is this real" leaves the
reasoning unchecked, and the reasoning is what the fix gets built on.

## A guard wired through per-clone config, and the mechanism I got wrong

`.githooks/pre-commit` is this repository's third enforcement point for the
image allowlist and the only one for the `JOBBY_ADD` staging rule. It runs
through `core.hooksPath`, which is **git config, per clone**, and a fresh
`git clone` has it unset. Nothing in a repository can set it on the way in;
git runs no repository-supplied code during a clone, deliberately.

Measured 2026-09-04:

    production checkout   core.hooksPath = .githooks    hook active
    ~/dev checkout        core.hooksPath = (unset)      hook inert

So in that checkout `git add -A` had never been gated and nothing said so,
because a hook that is not installed does not fail. It is silent, and that is
the same silence as a hook that passes.

**The first version of this entry gave the wrong reason, and the wrong reason
was the more alarming one.** It said the gap was worktrees -- that the guard was
"installed in the one place it is least needed and absent from the nine places
it is most", since all agent work happens in worktrees. That inference came from
one true observation (a checkout with it unset) and one assumption nobody
checked (that the checkout was a worktree). It was a separate clone.

A linked worktree **shares the repository's config**, so `git worktree add` from
a configured checkout inherits `core.hooksPath` with nothing further to do.
Verified by creating one and reading the value back, which took about ten
seconds and should have come before the write-up rather than after it. The gap
is clones, and only clones.

Same shape as the `activity.json` attribution and the tick-scanner cause: real
evidence, internally consistent, pointing at the wrong mechanism. **Provenance
is the property the artefact cannot answer**, and "which kind of checkout is
this" is a provenance question. `test -f .git` answers it; reasoning does not.

The fix is structural rather than a resolution to remember.
`scripts/install_hooks.py` binds a checkout and reports what changed, and
`tests/test_hooks_are_installed.py` fails in any checkout where the binding is
missing and names that command. It also checks the hooks are executable,
because git skips a non-executable hook without a word.

That test skips under CI, and the reason it is not the usual excuse is worth
stating: the property is "the person committing from this checkout is gated",
and CI does not commit. The subject does not exist there. That is different
from a skip that declines to look at a subject which does.

## A mutation applied to something the test controls proves nothing

**And it reads as verification, which is what makes it worth its own line.**

Checking whether a rewritten test was sensitive to the property it guards, the
mutation was `watch.do_not_start = lambda aid: "the user dropped it"` on the
module, expecting `tick` to short-circuit and the ensure never to be reached.
The suite passed, which was reported as "passed (BAD)" and looked like the test
being insensitive.

The test itself patches `do_not_start`, because it needs a known answer from
every collaborator to record their order. So `mock.patch.object` overwrote the
mutation on entry and restored it on exit. **The mutation never reached the
code under test**, and a mutation that does not reach the code says nothing in
either direction: passing does not mean the test is blind, and failing would
not have meant it was sharp.

The real check was to remove the wiring from `watch.py` -- the thing the test
does *not* control -- which failed, correctly.

So: before believing a mutation, ask what the test patches. Anything on that
list is under the test's control and cannot be mutated from outside it. Mutate
the production source, or mutate the value the test's own patch supplies, and
never the name in between.

## No diagnostic path is ever discarded

### First: a defect uniform across a class does not look like a defect

**It looks like a design.** Nothing is out of place, nothing contradicts
anything, and every member of the class agrees with every other. That is what
makes this the hardest kind to see and the reason it goes at the top rather
than in the incident list.

Four findings in one week, all this shape:

    reed's favicon        2,577 postings drew a job board's mark as the
                          employer's. Every reed card was identically wrong,
                          so no card looked wrong.
    three of six          usajobs.gov and themuse.com were missing from the
    aggregators           same list and nobody had noticed either.
    inflight unbridged    three controls wrote a state and ordered no work,
                          for five days, identically.
    the STATUS_QUEUE      `deepEqual(keys, ['dropped'])` -- a test asserting
    test                  the gap was the intended shape.

**The detection method is the transferable half: measure every member of the
class against the rule, not the rule against its own list.** The reed defect
was invisible while anyone asked "does the logo rule work" -- it does, and its
list was enforced perfectly. It appeared the moment somebody asked "for each
connector, is its host in the list", which is the same question from the other
end and the only one with an answer.

That is a different move from the anti-vacuity floors below, and it composes
with them. A floor asks whether a check is measuring anything. This asks
whether the check's *subject* is the whole class or the part of it somebody
remembered to enrol.

Concretely, and each of these found something: enumerate the connectors and
check the host list, enumerate the controls and check the queue map, enumerate
the mirror's INSERT columns and check the comparison, enumerate the exemptions
and check what each gives up. In every case the list was correct and
incomplete, and only the enumeration said so.

### And a control that renders is not a control that works

**"Open & finish" sat disabled on every Needs You card since cutover.** The
lane exists to hand a blocked application back to a person. A Chainguard run
parked on "enter the 8-character code emailed to you", and there was no way to
reach the form: the slots run `--headless=new`, so nothing was on screen, and
the one control that would have brought a window up posted nothing.

The card rendered perfectly. Every class it emits is one the board emits,
`test_v2_matches_the_board` and `test_v2_styles_are_reachable` both passed, and
the lane could not do its one job.

It is the same family as the Advanced search card, where nine of thirteen
controls had no `name` attribute and the browser omitted them from the
submission entirely. **A visual port carries the markup and not the action**,
and the two are indistinguishable from the markup, which is precisely what
every check in that area reads.

So the question a markup check cannot ask is whether anything *drives* the
control. `tests/lane_actions_are_driven.test.mjs` asks it: a `<button>` with a
literal `disabled` posts nothing and must be declared with a reason, an `<Act>`
must carry a `body`, and the floor counts both kinds -- because after the fix
`Rows.tsx` contains no raw `<button>` at all, so a floor on buttons alone would
have been satisfied by zero. It was, on the first run, and the floor is what
said so.

**The reason the control carried was worse than no reason.** It read "not
something v2 can do... Phase 4 moves the runner and this control with it", and
`watch.py:2432` had been claiming the `focus` queue the whole time. A confident
sentence about why something is impossible stops anybody checking, which is the
same failure as `STATUS_QUEUE`'s docstring one file over. Prefer "not built
yet" to a mechanism nobody has re-read.

#### And wiring it revealed the control had nothing to do, 2026-09-07

**Open & finish reached the device, held its slot, opened the window, and put
an empty form in front of the user. Every piece worked and the feature did
not.** The re-fill it ordered was dead twice over, in two unrelated ways, and
each was invisible from the other side.

The condition was `if not browser.tabs(slot)`, asked immediately after
`browser.focus(url)`, which opens the posting when no tab matches. So it is
never empty and the branch never ran. It reads perfectly: "re-fill when the
slot has nothing in it." It is the wrong question asked one line too late, and
the right one is `focus`'s own `opened`, which also says False on a second
press where the person is already part-way through typing.

Underneath it, the re-fill would have been refused anyway. `do_not_start`
declines a parked application, correctly, and declines one whose latest event
is `submitting`, more correctly still: re-running a form that already reached
Submit is how an application goes in twice under a real name. Chainguard was
both.

**And the slot it held was nobody's.** A parked application has no slot: the
reconciler releases it within a tick and `release_browser_slot` clears
`browserSlot` off the session, so the one application this button exists for is
precisely the one `browser_slot_for_application` answers None about. Chainguard
parked 02:50:55Z, was released 02:54:29Z, and read None from then on.

None then travelled, and both things it reached resolved it silently rather
than refusing it. `lock_path(None)` is `None.lock`: a real file, holding a real
slot's worth of nothing, while every reader goes on seeing the slot as free.
And `browser.port_for` answers the **base port** for anything not in `SLOTS`,
so `show(None)` would have stopped and relaunched apply-1 headed and taken its
pages with it, whoever was filling a form in there. Neither would have raised.
Both are refused at the entry now, and the handler asks
`candidate_apply_slots`, which already encodes "this application's own slot, or
a genuinely free one, or none".

That is the third fault in one control and it is the same shape as the other
two: a value that was reasonable when the code was written -- the slot the
application owned -- and is None by the time the button is pressed, because
something correct happened in between.

**The fix is not to waive the refusal. It is to make the refused thing
impossible.** A fill-only run's MCP server never advertises `pre_submit` or
`record_completed` (`application_mcp.SUBMISSION_TOOLS`), so the guard has
nothing left to guard: a run that cannot submit cannot submit twice. The mode
is argv, set by the trusted runner before any provider starts, and it reaches
`application_mcp.py` on its own command line. **A prompt is advice to one
model; an absent tool is a fact for all of them**, which is the argument
`cover_letter.validate_content` already makes about greetings and the reason
the browser firewall removes navigation rather than asking for it not to be
used. The prompt does say the run cannot submit, and that sentence is a
courtesy so the agent parks usefully instead of spending its turns discovering
the tools are gone.

Two surfaces are narrowed, not one. `tools_for` decides what is advertised and
`tool_names_for` decides what will run, because a client that read `tools/list`
once and kept the answer arrives at `call` with a name that was never
advertised.

**A second `pre_submit` on an unresolved first is a state nobody designed for,
and the guard exists to refuse it.** `finish.pre_submit` mints a nonce, writes
`submitting`, and stores only the hash; `completed` requires that exact nonce
against a latest event that is still `submitting`. Calling `pre_submit` again
while the first is unresolved overwrites the guard and appends a second
write-ahead, so the store then holds two submission attempts and one nonce, and
nothing afterwards can say which click the confirmation belongs to. It is
refused at both ends and both refusals stay: `pre_submit` returns
`proceed: false` for a latest event in `{submitting, completed}`, and
`do_not_start` refuses to start the run at all. Resolving the first attempt is
the only way forward, and there are exactly two honest resolutions -- a
`completed` event, or an `abandoned` one saying nothing was sent. Chainguard
took the second on 2026-09-07, in the shape the Freehand record from
2026-08-19 already had.

**`retry: true` was a full bypass of that gate and nobody had noticed.** The
tick read `apply_head.get("retry") is not True` and skipped the call entirely,
so the Python board's Retry could start a fresh run of a form sitting at
`submitting`. It never cost anything because `pre_submit` refuses from
underneath, which is exactly why it survived: the second layer held and the
first one was never exercised. The modes are passed in now, and a retry waives
the parked refusal and nothing else. Note what the docstring said while this
was true -- "this is the one function every start already passes through" --
which is the class already written up here twice, a confident sentence about a
mechanism that stops anybody looking.

**The refusal a person reads should be the one that names an action.** The
session checks ran before the history checks, so an application that was parked
*and* at `submitting` reported "it is parked in Needs you, waiting on the
user": true, circular for a user who is trying to act, and it hid the sentence
naming Confirm submitted and Stop. Same set of runs refused either way. A
refusal is only worth the action it names, so the terminal-event reasons go
first now.

**Three namespaces, and the guard against stranded controls walked two.**
`status_writes_reach_the_daemon.test.mjs` was written after three controls sent
`status: "inflight"` and reached nothing. It reads the `status` and `action`
props of every `<Act body={{...}}>`. Confirm submitted sends `event:
"completed"`, so the fourth stranded control sat inside that guard's own
subject, invisible because the walk never asked about its key: the route
inserted an `application_events` row and returned 200 while
`history.jsonl` learned nothing, so `dupe_check` would have re-offered the
role and the card would have stayed in Needs You. It walks all three now, and
the keys it must walk are derived from the route's own `Body` type rather than
listed beside it, which is the same correction as deriving parity's comparison
from the mirror's columns.

That mattered more than it looked, because Confirm submitted is the *only*
thing that can close a fill-only handoff. No nonce and no `record_completed`
are involved and that is correct rather than a shortcut: the nonce proves the
agent that clicked Submit is the agent that recorded the confirmation, and here
no agent clicked anything. A person did. `apply_marks.py` writes the event,
`finish.py --completed` stays nonce-gated for the agent path, and the two never
meet.

**And what that record says about itself was a small fabricated audit trail.**
`confirmation` means "the employer's own words on the page", and both boards
render a green `confirmed` chip with that text as the proof. `apply_marks` put
the sentence "marked submitted by the user on the board" in it, so 11 of the
104 completed events on this machine (measured 2026-09-07, against 69 with real
page text and 24 with none) drew the employer's chip over a claim about a
button press. It writes `confirmedBy` now and no `confirmation`, and the chip
has three states, because "a person says so" is neither "the employer said so"
nor "nobody did". Every fill-only handoff lands in the third one.

**`mcp_preflight.REQUIRED["jobby"]` was seven hand-written names against the
nine the server advertises**, missing `story_facts` and `record_answers`, so a
server that had lost the tool writing every application's answer manifest
passed the fence. Found by needing the list to depend on the mode; it is
derived from `application_mcp.tools_for` now. The browser half stays written
out, because `tests/test_apply_capability_contract.py` compares it against
`browser_mcp_proxy._tool_definitions` in both directions, which is the same
guarantee by a different route -- and that test covered only the browser half,
which is why the jobby drift had nowhere to show.

### Two board faults parked with their diagnoses, 2026-09-07

Both are real, both are understood, and neither blocks Phase 3. Recorded here
rather than fixed so the next person starts from the diagnosis instead of the
symptom.

**The Open & finish hold does not survive the run it exists to enable.**
`hold_slot` writes `heldUntil` into the slot lock, and every other writer of
that lock drops it: `reserve` writes `{pid, at, note}` when the re-fill starts
and `park_slot` writes `{pid: 0, parked: true}` when it parks. So by the moment
the form is worth keeping there is no deadline left, `hold_expired` answers True
on a lock with no `heldUntil`, and `reconcile_browser_slots` releases the slot
and `browser.clear_slot` closes the filled form. Observed end to end on
Chainguard `31c8aa9c1e988c56`: hold taken 16:09:11 until 16:24:11, run started
16:12:55, parked with a correct handoff at 16:22:38, lock read
`{"pid": 0, "at": "16:22:35", "parked": true}` with no deadline, and apply-1 had
zero tabs at 16:23:02. The fix is two-sided and neither half is optional: the
deadline has to survive `reserve` and `park_slot` rather than being a field any
writer can silently drop, and **the hold has to be re-armed at the park** rather
than at the press, because the press is before a filled form exists and the park
is the moment a person starts walking. The arming order was backwards from the
start.

**The single KV queue key loses presses to its own readers.** Every queue rides
`jobby:queue` through read-modify-write with no compare-and-swap, and the
Cloudflare KV REST values endpoint offers no conditional write to build one
from. Measured 2026-09-07: four Open & finish presses returned 202 at 16:03:22,
:36, :39 and :48, so KV accepted four writes into `focus`, and the array was
empty at every subsequent poll. The watcher's tick at 16:03:33 ran
`claim_one("apply/")`, which reads the whole key and writes the whole key back,
and a read taken before the presses with a write landing after them replaces
them with a copy of the queue that predates them. This is the 2026-08-28 fault
that ate a watch enqueue, one queue over, and it is available to **every**
button rather than to one. Two candidate fixes, and they are not equivalent: a
version field with reject-on-mismatch and writer-side retry is a real
compare-and-swap and fixes the race within a queue as well as across queues; one
key per queue is cheaper and only stops a claim on one queue eating a press on
another, which is the instance observed here but not the class. The symptom is a
button that does nothing, intermittently, with a 202 in the log saying it
worked, so nobody traces it back.

### The closure sweep has five brakes and the docstring said three, 2026-09-07

**A count that disagrees with its own list is how one of them gets deleted by
somebody tidying up.** `close_missing` said "Three brakes" and enumerated four
for as long as it has existed. They are now numbered, and brake 1 is called out
as the structural one: it has no code of its own, it holds only because a key
absent from `seen` never enters `held`, and it is therefore the one that can
disappear without anybody deleting anything.

**Brake 5 exists because brake 4 is per board and cannot see a correlated
failure.** On 2026-08-30 one sweep closed 1,860 records and tripped
`MAX_CLOSED_SHARE` zero times: 1,358 `oracle_orc` and 435 `greenhouse`, spread
thin enough that no single board crossed 70%. Nobody noticed for four days.
Over this device's 72 recorded sweeps, closed as a share of the pool before that
sweep's own prune runs median 0.269%, p95 0.701%, next 1.052%, and then 9.006%
for that one event. The canyon is what makes a small sample usable: any
threshold from 2% to 10% refuses exactly that sweep and nothing else.

**The denominator is the part to get right and it is not the pool.** What is
held *and could close*: state `new` or `watched`, on an `ats` or `careers`
board, 14,243 of 24,132 records on 2026-09-07. Tombstones, `applied` and
`passed` rows brake 2 can never touch, and aggregator records brake 3 can never
touch would otherwise make the threshold drift with tombstone retention rather
than with closure. It is counted before `ingest.run` prunes, in the same pass,
because the same 1,860 reads 9.01% of the pool before, 11.54% after and 15.26%
of the closeable population -- a 1.7x spread from the denominator alone. When it
fires it refuses **every** closure in the sweep, because a pool-level anomaly is
evidence about the sweep rather than about one board and closing selectively
under it means choosing which boards to trust on no information.

**`sweep_runs` is not about the empty response.** SmartRecruiters returned zero
that night, reported success, and closed nothing, because a channel that returns
nothing contributes no keys to `seen` and brake 1 covers it. Greenhouse returned
**54% and reported success**, and that is the gap: `status: ok` is the
connector's opinion of its own HTTP calls and coverage is a different claim, and
the two were sharing a field. A channel below 70% of its own last believed yield
keeps its postings, which arrived and are real, and loses only the right to have
its absences read as closures -- brake 1 applied deliberately rather than by
accident.

**Only a believed run moves the anchor, and that alone would be wrong.** Writing
a refused run's number makes the ratchet follow a truncation down, so a
genuinely halved channel is refused once and believed for ever. Writing nothing
refuses it for ever. So the anchor decays: two refusals running against the same
anchor re-anchor on the second, because two independent fetches agreeing is
evidence and one is not. The anchor is written after the postings are committed,
and the test for that asserts the property -- a sweep whose `save_jobs` raises
leaves the anchor alone -- rather than which line comes first. The dry-run test
beside it does not cover that, and passed with the update moved before the
commit; it was found by mutating the guard and watching the wrong test stay
green.

**Two flags from that work, both waiting on Phase 3's observability.** The 1,358
`oracle_orc` closures of 2026-08-30 are still unexamined, and the only reason
they look benign is that the channel's total yield held steady while they
happened -- which is exactly the shape brake 4 cannot see. The query worth
running once there is somewhere to run it is how many of the 1,358 are open on
that board again today: if most are, that night closed live postings under a
per-board brake that never fired because the board's total held. And
`closureRefused` still has no reader. It is computed, it carries the board and
the counts, and it sat unread through the one window it had something to say;
the number worth alarming on is a repeat count, the same board refused N sweeps
running, because one refusal is a bad fetch and twenty-two is a question. It
gets its reader when the health machinery moves to the cloud, not before.

### Greenhouse on the record contract, 2026-09-07

**The connector fetches one board per registry token, so the token is the
tenant slug, and the list response carries each job's own id.** Both halves in
hand with nothing parsed out of a URL, which is what the §3 contract said all
along and what no connector was doing.

Measured against the live pool before the change: 7,465 greenhouse records,
4,206 parsing a slug out of their URL and **3,259 not**, keyed on this device's
own pool id, which means nothing off this machine. Those 3,259 are the postings
served from an employer's own domain, where `stripe.com/jobs/search?gh_jid=...`
carries the job id and no slug, and `board_key.py` refuses to guess one because
a wrong slug splits one board in two forever.

Nothing already keyed correctly moves. Of the 4,131 records carrying both a
derived slug and a registry token, the two agree on all 4,131 and disagree on
none, so the change is additive by measurement rather than by hope.

### 54 of 67 errors were a 304, and 54 boards went unread, 2026-09-07

**`get_json` returns `(None, response)` for anything that is not a 200, so an
unchanged board landed in the error branch.** pinpoint and teamtailor had no
`unchanged` arm; lever, ashby, greenhouse, smartrecruiters and the feeds all
do. Measured on the 2026-09-07 sweep: 36 of pinpoint's 39 boards and 18 of
teamtailor's 23, 54 of the run's 67 errors, and both channels still reported
"39 boards" and claimed completeness. Not a closure hazard, because a 304 board
contributes no key to `seen` and brake 1 covers what it does not see. The cost
is that the error list was 80% noise, **which is how a real error stops being
visible** -- the same argument this file already makes about a loud alarm for
the wrong thing.

**Fixing it creates a second problem that has to be fixed in the same commit.**
A 304 board contributes no postings, so `found` drops by exactly what it would
have repeated, and the yield ratchet added a day earlier reads that as
truncation. lever takes 304s by design, so the channels with the best HTTP
manners would have been the ones refused every sweep. The envelope carries an
`unchanged` count now, on all three shapes rather than only `ok`, and the gate
treats it as what it is: a positive statement that nothing moved, which is the
opposite of a shortfall rather than a smaller version of one.

### A missing key was a skip, and a skip is invisible

**`api_key` never consulted `os.environ`.** It resolved every credential
through `toolpath.settings()`, which merges two files. A container handed
`JOBBY_ADZUNA_KEY` as an ordinary environment variable got an empty string,
the connector returned `base.skipped(SOURCE, "no key")`, and `ingest.run` files
a skip under `channelsSkipped` rather than `errors` -- so five of twenty-seven
channels would have gone quiet on the first cloud sweep and the report would
have looked healthy. Eleventh instance in this file of a check whose silence
means two different things.

The environment is read first and an explicit `settings=` argument still wins
over both, because a caller that passed one has already decided. And a keyed
channel with no key now **fails** rather than skipping: a skip is for a channel
this run had no reason to ask, which is reed on a search excluding the UK, and
that distinction is the whole reason the two states exist.

Five connector docstrings said "no key is installed on this machine, so this
connector has never had a real response" while the keys sat in `config.env` and
the sweep was using them. Measured the same day: adzuna 50 found / 22 kept,
careerjet 50/0, reed 100/26, usajobs 100/0, all status ok, and jooble a live
`HTTP 400`. A confident sentence about a mechanism stops anybody checking, and
this one would have had somebody read a real 400 as an endpoint nobody had
tried.

### The registry has fields for board health and has never written them

Measured over all 895 `companies.json` entries on 2026-09-07: `lastFetchAt` is
null on 895 of 895 and `lastStatus` is "unfetched" on 890, written once at seed
time by `seed_companies.py:747`. So the only failure signal is a per-sweep
aggregate naming a count and no board -- "6 token(s) 404 and need
re-resolving", 9 dead boards of 638 registry-driven ones, 54 wasted requests a
day, and no way to say which nine. `0019_boards.sql` and
`packages/schema/board_health.py` are the registry and the breaker, with two
rules. Only a 404 or 410 counts toward it, because a 500 or a timeout is
evidence about the day rather than about the board and counting them retires
every board an ATS serves during one outage. And no pass may retire more than
10% of one source's live boards, because an ATS changing its URL scheme returns
an honest 404 from a healthy server for every board it has -- the correlated
failure brake 5 exists for, one layer down and worse, since a retired board
stops being *asked* rather than merely having its absences believed.

**And the obvious repair for one of them was a `NameError` waiting on a branch
nothing runs.** `dual_write._record_timing` fell back to
`Path("~/.job-application")` when given no root, and both call sites gave it
none, so a class A write logged its timing into the device store whichever
store it was told to use. The fix that suggests itself is `root=root` --
which compiles, passes `py_compile`, passes 3,328 tests, and raises the first
time a file write fails, because `persist` takes `conn, account_id, path,
before, jobs, on_file_failure, batch` and **no `root`**. `path` is the pool
path, `paths(root)["jobs"]`, so its parent is the store root by construction.
Third instance of the entry above about a syntax check not being a name check,
and this one was caught by reading the signature rather than by any test, so
the test now pins both call sites through the AST.

`NoStorePathIsAFrozenDefaultTests` cannot see this class at all: it walks
module-level constants, and this was a fallback inside a function body.

**One finding genuinely parked.** `toolpath.settings` reads `LOCAL_CONFIG` off
`Path.home()` while `worker_key` writes through the same constant in five
places, so making the reader store-relative without the writer splits them. It
does not block the container, which carries no config file at all and reads its
credentials from the environment.

### Two lines about checks that read as correctness, 2026-09-07

**A field that exists and is never written is the design version of the alarm
that never fired.** `companies.json` has carried `lastFetchAt` and `lastStatus`
since it was seeded, and the only two writers are both at seed time: null on
895 of 895 and "unfetched" on 890. Nothing was broken, nothing complained, and
a reader opening the file would reasonably conclude board health was tracked.
The absent writer is invisible in a way an absent field is not.

**`root=root` compiled, passed 3,331 tests, and would have raised on the first
real write**, because `persist` has no `root` parameter. Same class as
`py_compile` passing a `NameError`: a check that reads as correctness and is
not. What separates it from the ordinary case is that the *repair* was the
thing that was wrong, and it was wrong in the one branch the suite does not
reach.

### 27 problems, one problem, 2026-09-07

**The board derived its own problems list with `c.status === "error" ||
c.why`, and every channel carries a `why`.** "353 boards". "1 of 1 countries x
1 terms". Measured over the real `lastIngest`: 27 channels listed, 1 actually
errored, 26 present for having a note. Third signal in a week that existed and
said the wrong thing, after the error list that was 80% 304s and the registry
fields nothing wrote, and the same failure each time -- a check whose subject
is wider than the thing it is named for.

The list is derived on the device now, for two reasons rather than tidiness. A
rule living in the renderer is a rule the device cannot alarm on, and this one
is a reason on the dead-man ping. And the interesting problems need the yield
ledger and the board registry, neither of which the renderer has. A sweep from
before this existed carries no `problems` key, and the board says "problems not
recorded" rather than falling back to the old derivation, which would put the
27 back on the first load after a deploy.

**Every entry says what it is and what it would take**, because a line a reader
cannot act on is a log entry wearing a problem's clothes. A failed channel
names the connector file; a missing key names the variable; a source under its
anchor names the ratio and its usual yield; a retired board names the board.

**Detection is per source, two sweeps, at a different floor from the coverage
gate, and the two floors are different because the questions are.** 70% is "do
not believe this sweep's absences", which is cheap and reversible and should
fire readily. 50% is "tell the user", which costs their attention and should
not. One low sweep is a bad afternoon and the gate already refuses its
closures; two consecutive is a source that has changed, which at the tier-1
interval is eight hours to detection. The 2026-08-30 Greenhouse sweep sits
deliberately between them at 54%: refused, and not an alert on its own.

The counter is moved against the anchor **as it stood before the sweep**, and
that is load-bearing rather than fussy. `sweep_runs.note_refusal` re-anchors
after two refusals, so a counter reading the new anchor would clear itself at
the exact moment it was supposed to fire.

And it is a sixth entry in `PING_REASONS`, not a new alarm. Observed firing on
the real store before it merged: `SOURCING: jooble failed: jooble any '': HTTP
400. read the failure and fix scripts/sources/jooble.py.`

### Two sources get a stricter standard, and one failure no count can see

**48 of 52 submitted applications came through greenhouse and ashby.** "Detected
within two sweeps" is a reasonable guarantee for a source contributing a hundred
postings and too slow for the two carrying the pipeline: eight hours of a dead
greenhouse looks, from the board, exactly like a quiet day. Both are held to one
sweep at 70%, which is the coverage gate's own floor, so for these two the sweep
whose absences stop being believed is the sweep the user is told about.
Refusing to close on a source and not mentioning it is the quiet half of a
fault this file has already been bitten by.

**A source-level number cannot see one board break, so each gets a canary.**
spacex holds 583 greenhouse records, the largest of 353 boards; snowflake holds
148 ashby records and is preferred over the larger airwallex for being the more
durable institution. Largest matters because a partial break shows as a
fraction. The canary is read off the §3 record key rather than asked of the
connector, which is why ashby's contract had to land first, and it is the first
real use of that contract beyond the key itself.

**The 10% tolerance is a specification and not a measurement, and that is said
rather than implied.** Per-board history does not exist -- `ingest-runs.log`
records per-channel totals only -- so there is no distribution to take a
percentile of. What is measurable is arrivals: spacex first-saw a mean 38
postings a day against 583 held and snowflake 9.4 against 148, roughly 1.1% of
the board per four-hour sweep, and gross arrivals bound net movement from above.
So 10% is about nine times the largest observed movement. It is also a lower
bound, because the pool is search-gated and the canary sees the whole response.
The `canaryYield` series is the data to re-derive it from.

**A 304 canary is not a broken one, and getting that wrong would have been the
same fault inside its own fix.** A board that answers 304 returns no postings
legitimately, so the connectors name the tokens that were unchanged rather than
only counting them, and the check excuses exactly those. Found by mutation:
removing `unchanged_slugs=` from greenhouse's `base.ok` left every test green.

#### What could take either down that the alerting would not see in one sweep

Asked directly, and worth writing down because the answer is mostly reassuring
and the exception is not.

    url scheme change      every board 404s      channel_failed, immediately
    auth requirement added every board 401s      channel_failed, immediately
    response shape change  `jobs` empties        anchor at 70% AND canary_silent
    new pagination default count truncates       anchor at 70%
    registry emptied       count goes to zero    anchor, but misnamed as yield
    rate limiting          boards error          channel_failed or the anchor

Every one of those moves a number, and every one is caught inside a sweep.

**A field change moves nothing.** `absolute_url` renamed, `location.name`
becoming `location.text`, `first_published` dropped: the response still parses,
the count is identical, the canary is happy, and the postings enter the pool
with no link, no location or no date -- where the search gate quietly filters
them or the board draws a card that goes nowhere. **The count is right and the
thing the count summarises is not**, which is the sixth place this project has
met that shape.

So the canary checks fields as well as count: the share of its postings
carrying a url, a title and a record key, against thresholds a real board
clears with room. Not 100%, because a real board has the occasional posting
with a blank field and a check at 100% is a check somebody turns off.

The one gap left is honest and stated: a field this list does not name can
still change silently. `CANARY_FIELDS` is the place to add one, and the cost of
adding is one entry.

### The sourcing container, and what running it found, 2026-09-07

**The fetch moves and the merge does not, because the pool cannot.**
`jobs_store` contains zero SELECTs, `run()` reads the whole 98 MB pool file, and
`_persist_pool` derives its Postgres delta by re-reading that same file. Making
the pool live in Postgres is Phase 4. What can move is the half that is already
pure: measured 2026-09-07, every connector takes its device state as plain
arguments and the only device state any of them writes is the HTTP validator
cache. `scripts/sweep_worker.py` runs them and stages the result; the gate, the
merge, the closure and the prune stay where the pool is.

**And the reason to move it is not the laptop's CPU.** `jobs_store.POOL_LOCK`
is the string `"scan"`: the sweep's lock *is* the pool's write lock. Eight
non-sweep callers take it, two on the apply path, and across `watch.log` from
2026-08-26 to 2026-09-07 there are 176 ticks that skipped their board reconcile
with LockBusy on it, 9 to 21 on every one of those 13 days. A sweep runs a
median 26.8 minutes, six times a day, and the fetch is nearly all of it.

**Running it in the container found what reading it had not.**
`jobs_db.connect()` resolves the URL from `config.env` and never from the
environment, which is the `api_key` fault one module over and was invisible
until the image ran. The file still wins -- a launchd service that picked up a
stray exported URL would write to whatever database that names, and that
docstring's reasoning is right -- but the environment is now the fallback,
because a container has no file. It carries `migrate.device_url_refusal`'s own
guard, because an environment read is exactly how a test run reaches
production, and `HOME=$(mktemp -d)` covered that only by making the file
unreadable.

**That refusal is the one a mutation found untested.** Five mutations were run
against the worker and four failed as they should; removing the test-runner
refusal left every test green, on the guard with the highest consequence of the
five. Six tests now cover it.

**And the container's own test tripped the scanner rule while writing it.**
`test_it_is_a_scheduled_machine_and_not_a_server` asserts `[[services]]` is
absent from `fly.toml`, and failed on the comment explaining why there is no
`[[services]]` block. That is the entry above about a scanner reading a comment
as if it were code, hit within minutes of writing the file it scans -- which is
what that entry says: knowing the shape does not prevent repeating it. Comments
are stripped, and a second test proves the strip does not hide a real block.

**The deploy step ends by reading back the digest every machine is running.**
Not the release's digest, which a failed machine replacement leaves untouched,
and not any machine's, because one stale machine still sweeps. Three deploys
have been lost here to a green run that armed nothing -- a Vercel build that
never fired, a pull without a restart, a check pointed at a stale server -- and
a warning is a green run.

#### The image was clean and the context was not, 2026-09-07

**`applications/` reached a remote builder, and no line of the Dockerfile was
wrong.** It copies three paths. The *context* is uploaded whole regardless, and
the first real `flyctl deploy` shipped 118 MB across 11,704 files: `.git` 49 MB,
`.claude` 33 MB, and `applications/` 9.4 MB of real resumes and cover letters
carrying a real name, address, phone number and email.

The distinction is the lesson. **What an image contains and what a build
transfers are different questions**, and every artifact you would inspect to
answer the first one says nothing about the second. Nothing was in the image;
everything was on the builder.

`.dockerignore` denies everything and allows three paths back, rather than
listing what to exclude. A denylist is a list somebody has to remember to
extend and what it forgets is by definition what nobody thought of -- which is
exactly how `applications/` unanchored in `.gitignore` once swallowed an API
route. Verified by asking Docker rather than by reading the patterns: the
surviving context is `docs`, `packages`, `scripts`, 176 files, and
`applications`, `.git` and `.claude` are absent.

**And `fly deploy` created two machines against a config with no service
block.** `--ha` defaults to true, "Create spare machines that increases app
availability", so HA is a deploy-time flag rather than a consequence of
`[[services]]`. Leaving the service block out was correct and did nothing about
it. For this app a standby is a second sweeper running in parallel with the
first, so CI builds with `--build-only --push` and updates the machine that
exists; nothing in it creates one.

**The check that caught the second one was itself the comment fault, twice in
one hour.** A throwaway `'machine run' not in workflow` reported that the
workflow creates a machine, on the error message telling a reader how to create
one. The real test strips comments *and quoted strings*, and has a control
proving the strip does not hide a genuine creation. That is the third instance
this session of a scanner reading text about a thing as the thing, after the
`[[services]]` comment in `fly.toml` and the two before it -- and each was
written by someone who had just read the entry warning about it.

### The cloud sweep was memory-bound, and three wrong causes came first, 2026-09-08

**A cold greenhouse sweep ran 97 minutes on a 512 MB machine, and read from
inside it:**

    MemTotal 459 MB   MemFree 23 MB   MemAvailable 85 MB   loadavg 1.36

`sweep_worker.sweep` holds a whole channel's postings before staging, and
greenhouse cold is ~24,000 postings carrying full descriptions. The kernel was
reclaiming pages continuously and every parse paid for it: 17.5 s per board
where the laptop, cold, on a throwaway root, does the same boards at 1.57 s
(SpaceX's 2,344 jobs in 1.8 s). The container was doing something the laptop
does not: thrashing.

**Three causes were proposed before this one and each was refuted by a
measurement, in order.** Parse-bound: the parse benchmarks at 1,385
postings/sec on this Mac, about 1% of sweep time. CPU-bound on a shared vCPU:
the container's parse would have to be a hundred times slower to explain it.
The datacentre IP being throttled by the remote end: the fetch benchmark from
the laptop found nothing slow to throttle. What settled it was `free -m` over
`flyctl ssh console` into the running machine -- observing rather than
reasoning, which this file says in a dozen places and which took four attempts
here.

**The sweep is single-threaded, so vCPU count is irrelevant, and that stays
true after the cache fix.** `sweep()` loops channels and each connector loops
its boards; a second vCPU sits idle. The two levers are memory and the
validator cache, and anyone sizing a machine for this starts from that premise.

**The validator cache is why every cloud sweep was cold.** `base.Fetcher` keeps
ETag and Last-Modified in `<root>/source-cache.json`, and the container's root
is wiped on every start. ashby cold: 6,855 postings from 193 boards in 717 s.
The laptop's warm run of the same boards: 1,050. `http_validators` is keyed by
URL rather than hung off `boards`, because 814 of the device's entries include
feeds that have no board row and never will, and a column on `boards` would
have covered the registry-driven half and silently dropped the rest.

**The discovery agent named this in as many words and it was not connected.**
"With per-board Fly workers the cache stops being per-run and becomes shared
mutable state... This needs to become a Postgres column on the boards row, not
a file on a volume." The answer was in the corpus, in a report already read,
and the first full run paid 97 minutes to rediscover it.

**Two smaller things from the same run.** `wait_for_sweep` accepted a sweep
with one finished channel because `stage()` commits per channel and "every row
has a finished_at" is trivially true when one row exists; it merged a
twenty-seventh of a sweep as a whole one, with brake 1 the only thing between
that and closures. It waits for the machine to stop now. And Fly's free trial
stops every machine at exactly 300 seconds -- documented, not inferred -- which
cost two runs before the account had a card.

**A guard, alarm or counter is unverified until it has been observed firing on
the production machine. Not tested. Observed.** A green suite is evidence that
the code is internally consistent. It is not evidence that the thing works,
and for this class of code the two have come apart every single time.

The count is now four in one week, and the shape is identical each time: the
mechanism was correct, its tests passed, and it measured nothing.

- The write-path counter sat inside `store.write_private`, which production
  does not reach for class A writes: mirror off, 2 observations; mirror on, 0.
  19 of 27 required paths were invisible to it.
- The firewall's `activity.json` was written at the end of `main()`, which a
  process killed by a signal never reaches. Two months, zero records from any
  run that reached a form.
- `ping_health` returned `None` on any network error and every caller ignored
  it, so a misconfigured alarm was indistinguishable from a healthy system.
- `pool_staleness` called `jobs_store.paths(None)`, which raises; a broad
  `except Exception` returned `None`; and `None` reads as "nothing to say". An
  alarm that could not fire on any machine, shipped as the fix for an outage
  nothing alerted on.

The last one is the sharpest because of what it was for and how it was found.
Every test in its file mocked the function, **including the one named for
testing it**. What caught it was calling it on this Mac after deploying and
reading the number: `pool age: unknown`, beside a pool twenty-seven minutes
old.

**A check that can only fail in one direction is half a check**, and that is
the shape all of these share. `_parses` refuses JavaScript that does not parse,
which catches an extraction taking too *little* and is blind to one taking too
much: a loose end marker swallowed 41 KB of the board's script, the result was
balanced, and it parsed. It would have shipped the entire board's wiring inside
a hero-intro effect. A comparison of production against main printed seven files
as differing when they were merely unreadable, because its helper returned an
error string where a hash belonged and "could not look" and "did not match" both
came out as `DIFFERS`. And `window.__jb_ran_theme` was set to 1 by a wrapper
whose body then bound nothing, so the flag read as "it ran", which was true and
useless.

So when writing one, ask what it says when the subject is absent, unreadable, or
oversized, and not only what it says when the subject is wrong. If those answers
are the same, it is not measuring the thing.

A fourth, and the clearest of them, because the failing check and the sufficient
check sit two lines apart. `.gitignore` carried `applications/` unanchored, to
keep the delivered resumes out of git; unanchored it matches any directory of
that name at any depth, and it swallowed `apps/web/app/api/applications/`. The
write endpoint answered 503 correctly on this machine, where the file is on
disk, and 404'd from the traced bundle in CI, where it had never been committed.
**A rule protecting real personal data ate source code**, which is the worst way
round for both.

The instructive part is what it took to pin. Asserting the endpoint answers 503
rather than 404 catches it once and says nothing about why. Asserting the rule
is anchored catches the rule and not the file. **A route git ignores and a route
nobody wrote are identical from CI**, so the load-bearing test is the third one:
that the file is actually tracked. Each of the three is blind to a different way
of arriving at the same 404.

So, concretely, before calling any of this done: make it fire, or read the
value it is silent about. A silent alarm has two meanings and only one of them
is good news. `docs/health-observed.md` records which of this repository's
guards have actually been seen firing here and which have not, and a guard that
has never fired is not thereby broken, it is unverified, which is a different
claim and the honest one.

**Auditing that list is itself subject to this rule.** Two of the first
readings in it were wrong in the safe-looking direction: `activity.json` is
absent from the disk *by design*, removed with the staged uploads it sits
beside, and `attempts[].browserCalls` lives in the run log rather than the
session file. Both initially read as "never fired" because the instrument was
pointed at the wrong place, which is the same defect as the guards it was
auditing. Grep for the message the code actually emits, in the file it actually
writes to.

**Stderr, health-check results, error branches: none of them may be dropped,
swallowed or routed to `/dev/null`. The failure nobody can reproduce is almost
always the one whose evidence was thrown away at the source.**

Three instances, and they are the same instance:

- `ping_health` returned `None` on any network error and a status every caller
  ignored. An unreachable or misconfigured `HEALTHCHECKS_URL` was therefore
  indistinguishable, from inside the watcher, from a healthy system with a
  working alarm.
- The tick's `STALE BOARD` branch was never reached across a 22-hour outage,
  because the publish raised first and the exception handler ran instead. Hours
  of pings said "tick 4620: publish failed" and not one said the board was a day
  old. `STALE BOARD` appears zero times in `watch.err` for that entire day.
- `browser_mcp_proxy` passed `stderr=subprocess.DEVNULL` to the Playwright
  child. When the backend died during initialize, the client saw only
  "connection closed: initialize response" and the reason ceased to exist in the
  same instant it was produced. Every hand probe afterwards succeeded, so for
  two days the fault was "not reproduced" when it had in fact explained itself
  once and been silenced.

The component whose job is noticing keeps being the one that says nothing, and
each time it took a person reading code to find what an alarm should have said.
So, concretely: a `subprocess` gets its stderr captured and reported, never
`DEVNULL`. A probe returns its outcome and something reads it. An `except` that
ends a story writes down which story it ended. Where a check can fail before a
later check runs, the later check's message is attached to the failure rather
than skipped, because the first thing to go wrong is rarely the important thing.

`tests/test_diagnostics_are_kept.py` enforces the stderr half by walking the AST
of every module in `scripts/` rather than trusting anyone to remember, because a
rule that depends on the next person remembering is a rule already broken. It
counts the launches it inspected, so a matcher that quietly stops matching fails
rather than passes. Measured after this change: 80 subprocess launches, 73
captured, 6 inheriting into a service log, 1 merged, none discarded. `DISCARD_
ALLOWED` exists for a launch that genuinely must throw its child's output away,
and takes the reason with it.

Chrome was the last holdout and is not an exception. It is launched detached to
outlive its runner, so a pipe would have no reader and could fill and block it.
Its output goes to `~/.job-application/logs/chrome-<slot>.log`, truncated at
each launch so it describes the browser now running rather than growing across
restarts.

The cost of this is a few kilobytes a run. The cost of not doing it, measured
three times now, is that the one occurrence able to name the cause is the one
occurrence guaranteed not to.

### The vendor's own log is a diagnostic path, 2026-08-28

**agy writes the reason for a failed run to a file and hands back nothing. So
the fourth source in the failure chain is agy's log, joined by conversation id.**

Five Gemini document jobs failed 10 to 13 seconds after provider start, exit 1,
across two applications between 01:22Z and 03:59Z. The whole recorded account
of each was:

    provider exited 1: event : result , result : conversation id :
    a3d7c777-5cb0-4202-8731-fd37d5ee00ba , status : ERROR , response : , error :

Three sources were consulted and all three were empty. Stderr was empty. The
terminal result carried `status: ERROR` with an empty response *and a genuinely
empty error string*, not one the 140-character cap had cut. The weak
last-line-naming-an-error fallback then quoted that same empty event back, and
spent 36 of its 140 characters on a conversation id.

The reason existed the whole time, in `~/.gemini/antigravity-cli/log/cli-*.log`:

    pubsub: Publish killing slow subscriber "...": subscriber fell behind
    updates, stalled for 5s
    Print mode: conversation update stream failed: subscriber fell behind
    updates, stalled for 5s

**What it is.** An internal race in agy 1.1.22, not a fault of ours. The killed
subscriber is agy's own idle-watcher: goroutine dumps in all four logs show it
created by `WaitForConversationFullyIdle` under `printmode.pollResponse`, while
`processModelStream` blocks publishing the first step to it. The publisher gives
up after 5s, which fails the conversation update stream, which makes print mode
emit that empty ERROR result and exit 1. Our stdout reader is not involved: the
stall begins ~0.4s after the user message is forwarded and before the model has
returned anything to write.

**What it is not, each ruled out by measurement rather than by argument.** Not
size or an agy streaming limit: the identical 634,903-byte stdin line, one
NDJSON line built by the real `build_run_invocation`, succeeded 6 of 6 on
2026-08-28 after the window. Not the bundle: the same Robinhood bundle succeeded
at 01:18:53Z and failed at 03:59Z. Not quota or auth: the logs show
`authenticated via keyring`, no quota error, and the successes bracket the
failures. Not contention between agy processes: across all 142 agy runs of
2026-08-27 the overlap count is zero, every run. Not machine load from form
runs: only 1 of the 4 failures overlapped a live apply slot.

**It did not reproduce and that is reported rather than smoothed over.** 6 of 6
succeeded against the real prompt after the window closed, versus 4 of 5 real
document runs failing inside it. Likeliest cause is a timing-sensitive race
whose trigger has not been identified, not a condition anyone has cleared. If
Gemini document jobs start failing this way again, read the CLI log first.

The failure chain is now four sources, strongest first: stderr, then the first
stream event carrying a *non-empty* error (earliest wins, because the terminal
result's is empty), then agy's log, then the weak line match. UUIDs are stripped
from a reason so the cap buys information.

Two guards on that log read, both load-bearing and both tested by disabling
them. The match is an allowlist of `printmode.go` and `pubsub.go` at severity E,
never a search for "error", and any line containing `@` is refused: the same
file records the signed-in Google account, and with both guards removed the
recovered "reason" is `applyAuthResult: email=...@gmail.com`. And the join is by
conversation id, never by taking the newest file, because runs overlap and a
neighbour's fault attributed to this job is worse than no reason at all. The
scan is bounded by age rather than by count; a count of twelve missed a real
failure log sitting at rank fourteen.

## Checking a peer's number is not the slow path

**The coordination cost is a few messages. The 20.87 MB cost 23 hours.**

Four sessions running at once will each be tempted to take a peer's figure on
trust, because stopping to verify feels like the thing slowing everyone down.
It is not. The board froze for 23 hours because several people each added a
little to the published state and nobody re-measured the total; the withdrawn
India share travelled into a work order through a summary that carried the
claim but not the correction; and a "verified" fence-parsing claim survived
because a session re-read the docstring asserting it.

What works, observed rather than theorised: on 2026-08-27 three sessions
measured the same board state independently within minutes and agreed to the
byte, one corrected its own 4 KB projection to a 21-byte measurement and said
which was which, and each correction came from someone checking a claim about
their *own* area rather than accepting a peer's. Nobody enforced that.

So: check the number that lands in your area, say when you cannot verify
something instead of repeating it, and treat a peer's word as information
rather than authorisation — including when a peer says a window has closed or
a merge is cleared.

## What counts as evidence

**A docstring, a fixture, a test, a commit message or another session's summary
can assert a fact about the world. None of them is evidence for it. Only a
measurement is, and the measurement, not the assertion, is what gets reported.**

Re-reading the claim is not verifying the claim. On 2026-08-25 a session
reported "Claude wraps its JSON in a fence on every run" as verified, having
only re-read the docstring making that claim; a document job from the same
morning had parsed an unfenced Anthropic response with the unmodified parser.
It is the same shape as the Adzuna fixture that invented an employer origin link
and had a passing test sitting on top of the fiction.

This matters more with several sessions running than with one, because claims
propagate faster than corrections and a wrong "verified" gets cited by peers who
cannot see where it came from.

So: quantify the claim and state what was and was not observed. "Intermittent,
observed on X at Y, not reproduced on Z" rather than "every time". Where a fix
addresses a failure you have not reproduced, say "likeliest cause", not "was".

## A test may not write the device's own store

**Every test module that drives store-writing code gets a `setUpModule` calling
`store_isolation.begin()`. Nothing else keeps fixtures out of the real
`~/.job-application`, and nothing about a fixture announces itself once it is
in there.**

Measured 2026-08-28, having gone unnoticed since at least 2026-08-25:
`usage.jsonl` held **3,372 fabricated rows against 29 real ones**, fifteen more
per suite run, every one carrying a plausible application id, a real pinned
model name and a real `taskKind`. `summary` averages over all of them, so the
answer to "what does one application cost" was a statement about the test
suite. Alongside it: `sessions/acme-pm.json`, a fabricated application sitting
in Needs you pending on "Enter the OTP", written by the same store CLI a real
run uses; `acme-pm` filed among 190 real entries in `roles.json`; the live
`browser-mcp-apply-1.json` rewritten; and `providers.json`, the registry the
three services read for connection order and health.

Nothing failed, because none of these paths is supposed to complain.

Four things this class does that make it invisible, each of which cost a
separate round of looking:

**A CI runner's home is disposable, so the whole class is harmless there.** It
only ever damages the machine that also holds the real applications. Do not
expect green checks to say anything about it.

**A store path as a default argument silently defeats any redirect.** Python
binds a default once, when the `def` runs, so `state_file=STATE_FILE` goes on
naming the path the module held at import however the constant is set
afterwards. `ProviderRegistry()` and `_default_registry()` therefore had two
different behaviours, decided by whether the caller typed an argument. Five
sites had it; `NoStorePathIsAFrozenDefaultTests` now walks `scripts/` and fails
on a sixth.

**Redirecting `JOB_APPLICATION_DIR` alone finds only half of it.** The rest are
`Path.home()` constants evaluated at import, which no environment variable can
reach. Sweep with `HOME` redirected, not `JOB_APPLICATION_DIR`.

**A per-module run proves nothing about a suite run.** Four test modules
execute a `scripts/` module by hand and assign it over its `sys.modules` entry,
orphaning the original — and the original is what `run_apply.providers` and
every other importer still points at. Redirecting what `sys.modules` holds
moved the copy nobody calls. `test_provider_runners` was clean on its own and
wrote the real registry in a full run, and the difference is only whether the
swapping module got loaded.

Two mechanisms answer it. `tests/store_isolation.py` moves a module's store,
discovering constants by walking the module graph rather than by holding a
list, so one added tomorrow is covered without anyone remembering. And
`provider_usage.record` refuses to write the device ledger when it detects a
test runner, which is the backstop for the one file whose corruption is silent
by construction; that detection is measured rather than asserted, by importing
the apply path in a fresh interpreter and checking neither `unittest` nor
`pytest` lands in `sys.modules`. Measured with the isolation deliberately
removed from `test_provider_runners`: ten rows written without the guard, zero
with it.

That backstop reports itself with `sys.stderr.write`, not `print`, and the
difference is not style. The tests that reach it are the ones driving
`run_apply.main()`, and every one of them is wrapped in
`mock.patch("builtins.print")` to keep the runner readable. The first version
refused all ten rows and said so zero times — a diagnostic silenced by exactly
the runs it existed to explain, which is this repo's oldest failure shape in a
new place.

**The guarantee is about writes, and reads are a different one.** A test that
reads the device's own `resume-content.json` and passes because of what it found
there is non-hermetic, and no count of files that appeared will ever say so.
`document_worker._base_resume_content` was the one store-root resolution under
`scripts/` that ignored `JOB_APPLICATION_DIR` — thirteen others honour it — and
#107 gave it a third caller inside `_ats_checks`, which is validation. It is
fixed, but that closed an instance rather than the class.

Finding the next one is a sweep, and there is no cheaper way:

```bash
python3 tests/store_isolation.py
```

It is deliberately not in CI, for the reason above.

### The store is not the database, 2026-08-30

**A test run wrote the device's own Postgres for as long as dual-write has
existed, and the precaution that had been covering it was retired on the
strength of a fix that does not cover it.**

Measured, from one unredirected `python3 -m unittest discover -s tests` killed
after sixteen minutes: 39 rows into `applications` between 04:55:24 and
05:11:20 against 197 real ones, 21 into `application_events`, `answer_bank`
emptied from about 180 rows to 0, the single `profiles` row overwritten with
`{}`, two scratch schemas orphaned. Parity went from green to 221 divergences
and the seven-day clock restarted at zero. No production impact: reads still
come from the file store behind the Phase 0 flag, so the damage was confined to
the mirror nothing reads yet.

**The reasoning is the part worth keeping, because every premise in it was
true.** #116 was in the checkout: `tests/store_isolation.py` present, 13
modules calling it. The standing instruction was to keep prefixing suite runs
with `HOME=$(mktemp -d)` until the store fix reached every checkout. Both facts
were checked and both held, and the conclusion drawn from them -- that the
redirect was no longer needed -- was false, because it rested on a link nobody
had stated: **that store isolation implies database isolation.** It does not.
`grep -c "DATABASE_URL\|psycopg\|postgres" tests/store_isolation.py` returns
0. That module moves the file store by walking module constants and has no
opinion about a database at all.

What the `HOME` redirect had actually been doing was incidental and nobody had
written it down. `migrate.CONFIG` and `jobs_db.CONFIG` were
`Path("~/.job-application/config.env").expanduser()`, so with `HOME` moved the
fall-through found no file and no URL. The redirect was covering two unrelated
hazards and was retired for reasons that only addressed one.

This is the fifth check in this file that passed while measuring nothing, and
the first where the check was **reasoning rather than a test**. The others were
code: a fixture with a passing test on top of a fiction, a docstring re-read
instead of a claim verified, a fence-parsing assertion nobody ran, a suite whose
green count hid seven skips. This one had no artefact to inspect. It was three
true statements and an inference, and the inference is where it went. Verifying
every claim in a list is not verifying the conclusion drawn from it -- already
written here, from the `activity.json` near-miss -- and it recurred anyway, to
someone who had read it.

**Two guards, for two different holes, and the second only exists because the
first was measured rather than trusted.**

`migrate.device_url_refusal()` refuses to fall through from an unset
`JOBBY_TEST_DATABASE_URL` to the device's own `JOBBY_DATABASE_URL` when
`unittest` or `pytest` is in `sys.modules`, the same backstop
`provider_usage.record` already uses, writing to `sys.stderr` rather than
`print` for the reason that one learned. `JOBBY_TEST_DATABASE_URL` is honoured
first and never refused, so CI's throwaway container is untouched, and an
explicit `--url` bypasses it entirely, because an operator naming a database is
not a fall-through. There is deliberately no environment variable that switches
it off: a variable set once and forgotten is how a guard stops existing.

**Then the guarded suite was re-run, and it still wrote six rows.** 885
refusals logged, and `application_events` went 234 to 240 anyway. The guard
inspects `sys.modules` in its own process, and `tests/test_brief_finish.py`
spawns `scripts/finish.py` as a subprocess, where `sys.modules` is clean and
nothing looks like a test. A guard that fires 885 times and still leaks is
worse than none, because the count reads as coverage.

The child was not even misconfigured. `finish.py` exports its `--root` as
`JOB_APPLICATION_DIR`, so its store was correctly redirected to a temporary
directory; it read the real `config.env` regardless, because that path was a
`Path.home()` constant computed at import and no environment variable can reach
one. So the second guard is the principled one and it subsumes the accident the
`HOME` redirect was having: **credentials resolve through the store root, on
every call.** A process told to operate on store X does not read the
credentials of store Y. `config_path_default()` in both `jobs_db` and `migrate`.

Neither guard covers the other's case. The first catches a test that connects
in-process; the second catches the work that test causes somewhere else.

**The lesson about the guard is the same one as the guard.** Adding the
in-process refusal and reporting it as done would have been the identical
failure a second time in one afternoon: a true premise (the guard fires, 885
times, verifiably) supporting an unstated conclusion (therefore nothing
reaches the database). What separated them was running the suite again and
diffing the row counts, which cost four minutes.

So the check that ends this is a measurement and not an argument: snapshot every
table, run the full unredirected suite, diff. Anything else is the inference
that started it.

### Interim, and delete only this subsection when it is done

**Everything under this heading expires when the fix is in every checkout.
Nothing below it does.** They are separated because the deletion is the moment
the two get confused, and the confusion runs toward "a redirect is all you
need", which is the dangerous direction. Retire this when a full suite run from
a fresh checkout measurably writes nothing into the real store.

Measured 2026-08-28: nine worktrees plus the production checkout hold ten copies
of the five writing modules, and a copy is per-worktree while an agreement to
hold off is per-session. So a quiet window cannot be granted by asking; it can
only be taken and immediately re-measured. That is also why the production
checkout matters here, since it is a place nobody thinks of as somewhere tests
run.

Until then, the interim instruction for anyone running the suite on this machine
is `HOME=$(mktemp -d)` in front of it. **Give that instruction with its cost
attached: one failure and seven skips, all store-absence.** Both halves matter
and the second is the one people scroll past.

The failure is
`test_resume_build.ShippedTemplateTests.test_the_shipped_template_and_base_render_together`,
which asserts "SAHIL SAPRA" appears in the rendered HTML and gets empty name and
contact divs because the profile lives in the real store. Three sessions hit
exactly that one, independently, at 2124, 2126 and 2134 tests.

The seven skips are the real cost. Measured 2026-08-28: three "no answer store
on this machine", one each for no rendered resumes, no real job store, no
profile, and no profile at the redirected path. Those are the tests that check
against the real corpus, which is precisely the class that exists because a
synthetic fixture could not have caught the thing: `LiveCorpusTests`,
`test_no_pii`'s tracked-file scan, the resume round-trip over every resume ever
written, and the real state's fit under the publish ceiling. A redirected run
reporting 2,124 passed has less coverage than the number implies and says so
only in a skip count. **If seven ever becomes eight, notice.**

### Durable, and none of it expires with the fix above

**A redirected home isolates the store, not the machine.**
`tests/test_apply_end_to_end.py` does not skip under one: `browser.running`
probes the real CDP ports and never consults `HOME`, so it drives a real apply
slot either way, and it reserves nothing while doing so (see the missing-lock
note below). Its *file* writes do follow `HOME`, because `browser.ARTIFACTS`
derives from `Path.home()` in the test process. Chrome's own absolute
`--user-data-dir` governs the browser profile, which is a different path and not
where artifacts land. Measured: a redirected `discover` run put its
`activity.json` in the throwaway home and left the real tree untouched.

**So `watch.py --status` before a full run, always, and this does not expire
with the store fix.** Not because a redirect leaks, but because that module
reaches past the filesystem entirely to a real browser, which no store isolation
can address. #109 stops it taking a slot the watcher holds; two concurrent test
runs choosing the same free slot is still unguarded. While the interim
subsection above is also in force there are two precautions rather than one, and
they answer different hazards; when it is retired this one remains.

**The attribution trap here is worth more than the rule.** Every run of that
module uses `applicationId` "endtoend", so every run on a given slot writes the
same `browser-artifacts/<slot>/uploads/endtoend/activity.json`. Neither the path
nor the timestamp can tell one session's run from another's, and on a machine
with several sessions the obvious inference is wrong. On 2026-08-28 two sessions
independently found that file in the real store, each dated it to their own
redirected run, and concluded the redirect was unsafe. It was neither of theirs:
its contents read

    {"browser_attach_file":1,"browser_find":1,"browser_snapshot":1,"browser_tabs":2}

and at that moment the only test file calling `browser_tabs` was a third
session's unmerged branch, on an authorised unredirected run. Two people checked
that a file existed, at a plausible time, in the right place, and both were
wrong about who wrote it. **Open the file before concluding anything from its
presence in that tree.**

**That discriminator has already expired, which is the real warning.** #115
merged the `browser_tabs` calls into `tests/test_apply_end_to_end.py` on main,
so every session's run now produces the same call mix again and the contents no
longer attribute anything either. Do not grep for the tool name: `browser_tabs`
is an ordinary firewall tool in `browser_mcp_proxy.py` and in
`mcp_preflight.REQUIRED`, and has been throughout, so finding it proves nothing
about which test wrote a record. **There is currently no reliable way to
attribute one of these files between sessions.** The fix is the slot lock noted
below, which would let a run declare what it is using; until then, if you need
to know who wrote one, ask on the machine rather than infer.

**Provenance is the one property you cannot check by reading the artifact**,
because the artifact is identical either way, and it is where careful people
went wrong three times on 2026-08-28. A shared `activity.json` said nothing
about which of ten checkouts wrote it. A mechanism claim ("that file is imported
by the watcher, so a pull without a restart delivers nothing") travelled two
hops from the person who guessed it to a person taking a deploy, gathering
confidence at each hop and never a measurement; it is spawned, and one `grep`
settled it. And a session offered a memory note back to its own author as
independent corroboration, which would have counted one write as three sources
had the author not checked the mtime and found themselves.

None of those was careless, and all three are the same thing: a claim about
where something came from, which the thing itself cannot answer. Ask the source,
`grep` the tree, read the mtime. Do not reason about it.

**And verifying every claim in a list is not verifying the conclusion drawn from
it.** A fourth session was handed three mechanisms behind that wrong rule --
`browser.running` never reads `HOME`, Chrome is launched with an absolute
`--user-data-dir`, the file is present in the real store -- checked all three
against the code, found all three true, and reported the conclusion verified. It
was still wrong, because the conclusion rested on a fourth link nobody had
stated: that the artifact path follows the profile path. It does not.
`browser.ARTIFACTS` derives from `Path.home()` in the test process while
`--user-data-dir` is Chrome's own profile, and the two are unrelated. Checking
the three claims harder would never have found it. What found it was opening the
file.

So when you are asked to check somebody's reasoning, the question is not whether
each stated claim holds. It is what the conclusion actually rests on, and whether
that is in the list at all. This is the same shape as the near-miss where "null
token counts" looked like a fixture tell and was a property of every row in the
store: real evidence, internally consistent, pointing at the wrong thing. Both
times the fix was to go and look at the specific object rather than reason about
the class it belongs to.

`tests/test_apply_end_to_end.py` is excluded from the sweep, and the reason is
not a leak. It drives a real Chrome on a real apply slot, so the firewall
writing `browser-mcp-apply-N.json` and an `activity.json` under
`browser-artifacts` is the behaviour under test. #109 is what stops it reaching
a slot it has no business in: `live_slot` takes its candidates from
`watch.free_apply_slots` rather than from whichever Chrome answers, so a busy or
parked slot is left alone. Before it, the module opened fixture tabs in a browser
that was filling somebody's real application. Both of its failures observed here
on 2026-08-28 were that plus the stale `totalCalls == 2` constant #109 also
fixed, and both are gone on main.

**Slot selection and page selection are two problems and #109 fixed one.**
Playwright picks a page when it connects and stays on it, so a tab an earlier
run abandoned is still current and opening another over CDP does not move off
it. Found on 2026-08-28 in a genuinely free slot: a test drove a leftover
`upload-form.html`, attached to that page's input, and passed, because the
fixture it thought it was on and the one it was actually on both contain the
words "Cover letter". A real assertion about the wrong subject, which is the
same shape as the salvaged-answer join and the Adzuna fixture. Select the tab by
URL rather than trusting whatever the connection landed on.
