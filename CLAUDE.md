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
replay the reveal.

Job cards answer hover with their outline, not with movement. Found, In flight,
and Needs you cards rest on `--hair` and hover to `--hair-strong` with no
transform. Their controls still lift; the card underneath does not. The only
scan action in the UI is Scan now inside the Found status banner.

Company identity is centralized in `scripts/company_logos.py`. Job feeds may
store a trusted `logoUrl`, and promoted roles carry that value into Watching.
Source ingestion also records an employer-owned website when a job description
links to a domain matching the company name. The board otherwise derives a
real employer domain from direct employer posting URLs. Never use an ATS or job
aggregator favicon as an employer mark.
The authenticated `/api/company-logo` route in `deploy/api/board.js` validates
the request, fetches the image server-side, limits response size and type, and
returns a private cached response. The browser must only load same-origin logo
URLs. Monograms are the required fallback.

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
- Gemini: `gemini-3.7-flash`

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
- `deploy/api/page.py`: password-gated server render using sanitized KV state and deployed code.
- `deploy/api/board.js`: queue, document, logo, health, and provider endpoints.
- `deploy/renderer/`: generated deployment copy of the renderer. Never edit it directly.
- `scripts/watch.py`: Apply orchestration, three launchd plists, health pings.
- `scripts/document_job.py`: encrypted document-job client and local installation.
- `scripts/document_worker.py`: serial local document generator and fixed gates.
- `scripts/browser.py`: trusted Chrome lifecycle and per-run MCP configuration.
- `scripts/browser_mcp_proxy.py`: application browser firewall.
- `scripts/run_apply.py`: form-only provider task and staged upload binding.
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
