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

OpenAI, Anthropic, and Gemini share one local provider registry in
`~/.job-application/providers.json`. The first connected provider remains
active until the user disconnects it. A failed health check stops work. It does
not trigger provider failover. Do not choose or connect the first provider for
the user.

Every agent task pins an exact model:

- OpenAI: `gpt-5.6-luna`
- Anthropic: `claude-sonnet-5`
- Gemini: `gemini-3.7-flash`

Gemini must report the requested served model through its bounded telemetry
record. A remap or missing record fails closed.

## Runtime shape

`python3 scripts/watch.py --install` owns both launchd services:

- `ai.jobby.watch` runs the queue watcher and form orchestrator.
- `ai.jobby.documents` runs `document_worker.py --loop --local` serially.

Both services use the same provider registry and native CLI credential stores.
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

## Main implementation seams

- `scripts/providers.py`: registry, connection order, health, model pins, CLI commands.
- `scripts/provider_login.py`: native interactive login and status publication.
- `scripts/settings_panel.py`: the three connection cards and local-only copy.
- `scripts/dashboard.py`: self-contained board, centred Home and Settings pills.
- `deploy/api/board.js`: same-origin settings route and local provider actions.
- `scripts/watch.py`: local provider actions, Apply orchestration, both launchd plists, health pings.
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

## Workspace state on 2026-08-23

Both launchd services are installed and running with
`/opt/anaconda3/bin/python3`. The installer now retains that interpreter. The
private Healthchecks URL requested by the user is configured in
`~/.job-application/config.env`, mode 0600. The old Oracle staging directory in
`/private/tmp` was removed, and the Oracle VM itself was not touched.

No provider is connected. This is intentional because the first connection is
a user choice. Connect runs the provider's native browser login: `codex login`,
`claude auth login --claudeai`, or interactive `gemini` with Sign in with
Google. Jobby keeps no OAuth token. It records connection order and health
only. Codex, Claude Code, and Gemini CLI are installed on this Mac. Do not
select a provider for the user.

Local browser verification passed for `/` and `/settings`: the Home and
Settings pills are centered in the top app bar between the logo and theme
control, the active pill follows the route, and Settings shows OpenAI,
Anthropic, and Gemini cards labelled Your Device. No left rail, Back to
applications control, OCI worker, or Cloud Agent appears in the active UI.

The final suite passed with 1,114 Python tests and 44 Node tests, including real
Chrome PDF rendering. Local browser verification measured the Home and Settings
control at the exact horizontal centre of the top app bar and found three
provider cards with no Back to applications control.

Production code deployment is waiting on PR #4:
`https://github.com/sahilsapra391/jobby-auto-apply/pull/4`. Its Vercel preview
for the `jobby-board` project is READY and both Vercel checks pass. Merging the
PR into `main` is the production release action. The Vercel function calls
Cloudflare KV through its REST API. There is no separately deployed Cloudflare
Worker in this request path.

Do not run `python3 scripts/publish.py --deploy` as a substitute for merging
the PR. That command first uploads the live board and application documents to
Cloudflare KV, which is a separate content publication action.

## Worktree discipline

This checkout contains user-owned modified and untracked work unrelated to the
provider change. Inspect diffs by path. Do not delete, rewrite, stage, or fold
those files into this work. In particular, leave sourcing investigations,
captured job pages, and schema experiments alone unless the user names them.
