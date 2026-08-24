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
logo and the theme control: Home and Settings. Home contains the full Found,
Needs you, Submitted, Watching, In flight, and Dropped dashboard. Settings
contains provider connections. There is no left rail and no Back to
applications button. A future Profile pill can join the centred control later.

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
- `deploy/api/board.js`: same-origin settings route and local provider actions.
- `scripts/watch.py`: Apply orchestration, three launchd plists, health pings.
- `scripts/document_job.py`: encrypted document-job client and local installation.
- `scripts/document_worker.py`: serial local document generator and fixed gates.
- `scripts/browser.py`: trusted Chrome lifecycle and per-run MCP configuration.
- `scripts/browser_mcp_proxy.py`: application browser firewall.
- `scripts/run_apply.py`: form-only provider task and staged upload binding.
- `scripts/finish.py` and `scripts/application_mcp.py`: submission nonce state machine.
- `references/oci-worker.md`: retired historical recovery path, not current setup.

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

Current branch verification ran 1,137 Python tests. The 1,133 non-Chrome tests
passed; four Chrome render cases could not create a PDF inside the Codex
filesystem sandbox. The ten Chrome render cases pass when run at the host
level. The installed launchd service supplied a second real render proof
through the successful IFS job. All 44 Node tests pass. Local browser
verification measured the Home and Settings control at the exact horizontal
centre of the top app bar and found three provider cards with no Back to
applications control.

The Found location picker was also verified in the real dashboard at desktop
and phone widths in dark mode. It rendered 199 complete location names across
six continents with no picker-level horizontal overflow or console errors.
Clear all produced zero matches, selecting North America checked all 23 child
countries, Canada's `Only` action left one country, and Worldwide restored all
199 locations. Long names wrap instead of truncating.

The exact Luna model probe and the real Luna document and form-agent paths
passed. Anthropic and Gemini have no API keys on this Mac, so their exact live
model calls remain intentionally unverified until the user connects each key
in Settings. Their connector, environment isolation, model pinning, schema,
health, and failover paths are covered by automated tests. Do not report a live
Claude or Gemini pass without connecting a real key and running both contracts.

PR #5 is the production release for the provider health, failover, dedicated
control service, secure API-key connectors, browser slot fix, and resume
integrity gate. Its Vercel preview passed before merge. The Vercel function
calls Cloudflare KV through its REST API. There is no separately deployed
Cloudflare Worker in this request path.

Do not run `python3 scripts/publish.py --deploy` as a substitute for merging
the PR. That command first uploads the live board and application documents to
Cloudflare KV, which is a separate content publication action.

## Worktree discipline

This checkout contains user-owned modified and untracked work unrelated to the
provider change. Inspect diffs by path. Do not delete, rewrite, stage, or fold
those files into this work. In particular, leave sourcing investigations,
captured job pages, and schema experiments alone unless the user names them.
