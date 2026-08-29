# apps/

Deployable surfaces. One directory per thing that runs somewhere.

| Directory | What runs there | Filled in |
|---|---|---|
| `web/` | Next.js on Vercel: the board, the API route handlers | Phase 2 |
| `worker/` | Cloudflare Workers and the `TenantChannel` Durable Object | Phase 2 |
| `scanner/` | The Python connector container on Fly.io | Phase 3 |

Each is a scaffold today. The plan is `docs/plans/jobby-v2-rebuild-plan.md`;
the phase that fills a directory is named in its own README, and none of them
is a place to put code early.

## What is deliberately not here

`scripts/` and `deploy/` are the running product and stay where they are until
the phase that replaces them. The three launchd services execute
`/Users/sahilsapra/Documents/Development.nosync/jobby-auto-apply/scripts/*.py`
**by absolute path**, so moving that directory stops the daemon on the user's
machine, and `deploy/` is the Vercel project root. A repo layout is worth
nothing measured against a production outage, and Phase 0's job is to make the
later phases safe to attempt, not to start them.
