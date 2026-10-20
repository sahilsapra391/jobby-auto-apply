# Jobby

Jobby is an agent that finds jobs for you and applies to them on your behalf.
The agent work runs on your own Mac: it tailors a resume and cover letter for
each posting, opens the application in a browser it controls, fills the form,
and submits it. A web board shows what it found, what it is working on, what it
needs from you, and what went out.

It is built for one person running it for themselves. Applications go out
under your real name and cannot be recalled, so most of the code is about
refusing to guess.

## How it works

**Sourcing.** A sweep pulls postings from applicant-tracking systems
(Greenhouse, Lever, Ashby, Workday, SmartRecruiters and others) and from job
feeds and search APIs. Connectors live in `scripts/sources/`. Each sweep is
gated before anything is believed: a source that suddenly returns half its usual
volume keeps its postings but loses the right to have its absences read as
closures, so one bad fetch cannot close an employer's whole catalogue.

**The board.** Postings land in five lanes. Found holds new matches with a
score breakdown. Watching is your shortlist. In flight shows applications the
agent is preparing or filling. Needs you holds the ones that stopped on a
question only you can answer, a verification code, or a form the agent could
not finish. Submitted is the record of what went out, with the documents and
the answers each employer received. Dropped is an archive off to the side.

**Documents.** For each application, a provider model proposes changes to your
base resume and drafts a cover letter. Trusted Python then checks the result
before anything renders: no duplicated or near-duplicate bullets, no changed
numbers, dates or employers, no invented claims, no doubled greeting or
signature in a letter. Rendering, PDF output and page-geometry checks all run
locally.

**Filling forms.** The form agent never gets a raw browser. A local MCP
firewall (`scripts/browser_mcp_proxy.py`) gives it a small fixed tool set with
no navigation, JavaScript, shell or network tools, blocks Enter-to-submit, and
lets it upload only the two documents staged for that application, checked by
path, inode, size and SHA-256. Questions are answered from your own answer
bank; anything it cannot answer from there goes to Needs you.

**Submitting.** Submission is a two-step handshake. The agent asks for a
one-use nonce before it clicks Submit, which writes a `submitting` event, and
can only record the application as complete by presenting that nonce along
with the confirmation text the employer's page showed. A crash mid-submit
leaves a visible record rather than a silent duplicate.

**Providers.** OpenAI (through Codex), Anthropic and Gemini share one local
registry. Each task pins an exact model. Jobby tries your connected accounts in
the order you set and moves new work to the next healthy one when a login,
quota or model check fails. API keys stay in owner-only files on your Mac and
never reach the board or any cloud service.

## Repository layout

    apps/web/          the board: Next.js on Vercel, reading Postgres
    apps/worker/       Cloudflare Worker and Durable Object for live updates
    scripts/           everything that runs on your Mac: the watcher, document
                       worker, form runner, browser firewall, answer store and
                       sourcing connectors
    packages/schema/   Postgres migrations, the migration runner, feature flags
    deploy/            device helpers (KV queue, document jobs, status keys, R2),
                       the sourcing container image, the OCI recovery path
    templates/         resume and cover-letter templates, with fictional
                       example content
    references/        data the scripts read: question taxonomy, model prices,
                       the source registry
    docs/              architecture decisions and the migration policy
    tests/             Python and Node test suites

`CLAUDE.md` is the contributor guide. It covers the rules that have to hold
(worktree discipline, the firewall, the nonce, the document gates, test
isolation) and is worth reading before changing anything.

## Setting it up

You need macOS, Python 3.12, Node 24, Google Chrome, a Postgres database (the
project uses Neon), and at least one of the Codex CLI, an Anthropic API key or a
Gemini API key.

The device keeps its state in `~/.job-application/`. Create it and bind the
repository's commit hook:

```bash
python3 scripts/store.py init
python3 scripts/install_hooks.py
```

Private settings go in `~/.job-application/config.env`, which nothing in the
repository overwrites. Set the sensitive ones through prompts that never echo,
so a password never lands on a command line:

```bash
python3 scripts/worker_key.py ensure              # the local document-queue key
python3 scripts/worker_key.py set-database        # JOBBY_DATABASE_URL
python3 scripts/worker_key.py set-r2              # R2 bucket credentials
python3 scripts/worker_key.py set-healthchecks <ping-url>   # optional
```

The device reaches the work queue in Cloudflare KV with the same
`CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID` and `CF_API_TOKEN` the board uses (see
below). Put them in `config.env` too; `deploy/.env.local` is also read, for an
older setup.

Apply the schema, then install the three launchd services that do the work:

```bash
python3 packages/schema/migrate.py apply
python3 scripts/watch.py --install
python3 scripts/watch.py --status
```

If macOS privacy settings deny a new Python interpreter access to a repository
under `~/Documents`, pass one that already has access:
`python3 scripts/watch.py --install --service-python /absolute/path/to/python3`.

Sign the agent's browser in to the sites you apply through, once:

```bash
python3 scripts/browser.py --login https://www.linkedin.com
```

Your profile, base resume and standing answers are set up through the intake
described in `SKILL.md`. Your base resume wording lives in
`~/.job-application/resume-content.json`; the copy in `templates/` is a
fictional example. Answers to work-authorisation and sponsorship questions are
read from `~/.job-application/work-authorization.json`, and with no such file
those questions always come to you.

### The board

`apps/web` deploys to Vercel. It reads these environment variables:

    JOBBY_DATABASE_URL            Postgres connection string
    JOBBY_USER_ID                 the R2 key prefix for documents (default account-1)
    CF_ACCOUNT_ID                 Cloudflare account, for the work queue in KV
    CF_KV_NAMESPACE_ID            the KV namespace holding the queue
    CF_API_TOKEN                  a token scoped to Workers KV edit only
    R2_ACCOUNT_ID                 R2 account for generated documents
    R2_BUCKET                     the private bucket name
    R2_ACCESS_KEY_ID              R2 credentials
    R2_SECRET_ACCESS_KEY
    JOBBY_CHANNEL_URL             the Worker that carries live updates
    JOBBY_CHANNEL_TOKEN           the shared secret the board posts events with
    NEXT_PUBLIC_JOBBY_CHANNEL_WS  the WebSocket URL browsers subscribe to

Then connect a provider from the board's Settings page. OpenAI runs
`codex login`; Anthropic and Gemini ask for an API key in a private terminal
prompt and check the pinned model before the card shows Connected.

## Tests

```bash
HOME=$(mktemp -d) PYTHONPYCACHEPREFIX=/private/tmp/jobby-pycache \
  python3 -m unittest discover -s tests
node --test tests/*.test.mjs
cd apps/web && npm ci && npx tsc --noEmit
```

Redirecting `HOME` keeps the suite away from a real `~/.job-application`.
Database tests use `JOBBY_TEST_DATABASE_URL` and skip without it; they refuse to
run against the database in your `config.env`. A few tests check a real
profile and skip on a machine that has none. `tests/test_apply_end_to_end.py`
drives a real Chrome and skips unless an apply slot is free.

## Safety and privacy

Everything Jobby submits goes out under your name, and a submitted application
cannot be taken back. Start with the practice form in
`tests/fixtures/practice-form.html`, keep the daily cap low, and read Needs you
often.

The board has no login of its own. Reads are public by default and only writes
are gated (same-origin, JSON, and a request header). The board shows the
companies you applied to, your match scores and your profile, and the document
route serves rendered resumes that carry your contact details. Think about that
before deploying it with real data, and put an access gate in front of it if
anyone else could find the URL.

Generated documents, screenshots and anything under `applications/` are
gitignored, and the commit hook refuses images outside the brand directories.
`tests/test_no_pii.py` scans every tracked file for values taken from the local
profile.

## License

MIT. See `LICENSE`.
