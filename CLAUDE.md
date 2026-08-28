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
once before the loop. It costs about 1.3 seconds against a 45-minute run. And a
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
excluded by it too, for a better reason than tidiness, since a parked slot
keeps its page state precisely so its application can retry into it. The skip
message names which case it is, because "no browser at all" and "every browser
is busy" stop the suite identically and mean opposite things.

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
  so the arming event is the restart. `scripts/brief.py` is the exception: the
  watcher spawns it as a subprocess, so edits to it are live on save.
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
1. announce it, and say which services are going down
2. pull the production checkout, staying on main
3. restart all three services, dependencies before the orchestrator
4. verify each service's start time postdates the reflog entry for the new SHA
```

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

## No diagnostic path is ever discarded

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
