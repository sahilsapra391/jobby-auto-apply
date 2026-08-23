# Retired OCI document worker

This deployment path is no longer part of the active Jobby architecture. As of
2026-08-23, resume tailoring, cover-letter creation, scans, and form filling all
run on Your Device. `python3 scripts/watch.py --install` installs both the watcher
and `scripts/document_worker.py --loop --local` as launchd services.

Keep the material below only as historical recovery guidance. Do not deploy or
connect an OCI worker unless the user explicitly chooses to restore remote
document execution. The existing Oracle VM is outside the active pipeline.

The OCI instance builds resumes and cover letters. It does not open application sites, hold the
signed-in job browser, fill forms, or submit applications. Those actions stay on the Mac.

The current target is Canonical Ubuntu 24.04 on `VM.Standard.E2.1.Micro`, with 1 OCPU and 1 GB of
memory. The worker runs one job at a time. The installer adds 2 GB of swap and starts Google Chrome
with a low-memory flag set. This size is usable for serial work, but an agent CLI and Chrome can still
create short memory spikes. Check the journal for an out-of-memory kill before debugging a model
or document error. Claude Code and Gemini CLI both publish a 4 GB recommended minimum, so this
1 GB shape runs below their supported comfort range. Increase the VM memory if those kills recur.

## Data path

The Mac creates a minimal document bundle containing the posting, selected base resume, reduced
contact identity, confirmed story facts, and writing samples. Street address and application-form
answers stay on the Mac. The bundle is encrypted with AES-256-GCM before Cloudflare KV receives it.
The result is encrypted the same way on its return trip.

KV metadata contains an opaque UUID, timestamps, queue state, and a fixed error code. Provider
tokens never enter Jobby's queue. OpenAI, Anthropic, and Gemini credentials stay in each vendor
CLI's account store on the host where that CLI runs.

Each document provider runs inside Bubblewrap with a new process and mount namespace. Its model
receives the document inputs inline and has no shell, filesystem, browser, MCP, plugin, hook, skill,
or subagent tools. The process sees an empty read-only workspace, required system files, the
selected CLI package, and an ephemeral home containing only a copy of that provider's login. It
cannot see the decrypted task files, document scripts, templates, worker queue config, `~/.ssh`,
other provider logins, or unrelated home data. It returns bounded structured JSON through stdout.
Trusted Python renders and verifies the files after the provider exits. The copied login, CLI cache,
and session data are scrubbed with the task. The installer stops if Bubblewrap cannot create the
required sandbox.

## Network rules

Allow inbound SSH from your own IP. No Jobby application port needs to be opened. The worker makes
outbound HTTPS connections to Cloudflare and the selected model provider.

## Install

Clone this repository on the VM, then run:

```bash
cd ~/jobby-auto-apply
bash deploy/oci/setup.sh "$PWD"
```

The script installs the document toolchain, creates a private Python environment, prepares the
systemd user service, and adds swap only when the host does not already have it.
It installs Node.js 22 for current provider CLI support and a non-Snap Google Chrome build that
works with the service hardening policy. It also installs all three provider CLIs and verifies the
Bubblewrap boundary before creating the service unit.

Fill `~/.job-application/config.env` on both the Mac and OCI host:

```text
CF_ACCOUNT_ID=...
CF_KV_NAMESPACE_ID=...
CF_API_TOKEN=...
JOBBY_WORKER_KEY=...
```

Create or retain the shared key once on the Mac with:

```bash
python3 scripts/worker_key.py ensure
```

The deployment helper stages only the four worker settings in a mode-600 temporary file, then
copies that file through SSH. Do not put the key in Git, the Vercel project, a dashboard request,
or a command-line argument.

Authenticate each provider you plan to use while logged in as the same Linux user that runs the
service:

```bash
codex login
claude auth login --claudeai
gemini
```

Keep each generated credential JSON file owner-only. The worker refuses unsafe credential files.
For a connected provider, check the relevant path after login:

```bash
chmod 600 ~/.codex/auth.json
chmod 600 ~/.claude/.credentials.json
chmod 600 ~/.gemini/oauth_creds.json ~/.gemini/google_accounts.json
```

Codex device authentication prints a URL and short code that can be completed in a browser on
another device. Claude Code and Gemini CLI may also hand off to a browser. If their remote login
cannot complete automatically, use an SSH session for that one-time step.

One Connect click on the board settings writes the same provider action and sortable order key to
both workers. The first connection becomes active on both hosts even if they claim their separate
control queues in a different order. Each CLI still needs its own one-time host login. A document
request names the selected provider, and the OCI worker refuses the job if that CLI is not healthy
there.

Jobby pins the model on every invocation: OpenAI uses `gpt-5.6-luna`, Anthropic uses
`claude-sonnet-5`, and Gemini uses `gemini-3.7-flash`. The worker does not inherit a CLI default.

Start the service:

```bash
sudo loginctl enable-linger "$USER"
systemctl --user enable --now jobby-documents
systemctl --user status jobby-documents
```

Useful checks:

```bash
journalctl --user -u jobby-documents -n 100 --no-pager
python3 scripts/document_worker.py --once
python3 scripts/toolpath.py
```

The settings page reports the OCI worker as offline until it publishes its first sanitized status.
It then shows installed, connected, active, or authentication-required state for each provider.

## Updating

Pull or copy a reviewed revision, rerun `deploy/oci/setup.sh`, then restart the service:

```bash
systemctl --user restart jobby-documents
journalctl --user -u jobby-documents -n 50 --no-pager
```

The worker deletes each decrypted task directory after it returns a result or records a fixed
failure. Encrypted request and result records expire from KV, and the Mac deletes a completed job
after verified installation.
