# ADR 0004: The web app stays on Vercel, git-deployed

- Status: accepted, with a correction to the plan's reasoning
- Date: 2026-08-29
- Source: §2, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

The board is served from Vercel and deploys when `main` merges.

## Decision

Keep Vercel and keep git-deploy. Build the Phase 2 board as Next.js.

## Rejected

**Moving the web app to Cloudflare, alongside the Workers and Durable Objects.**
It would collapse the two API hosts of ADR 0005 into one. Turned down because a
rebuild that is already replacing the store, the transport, the sourcing and the
device process should not also move the surface the user looks at. The seam
described in ADR 0005 is narrow and one-directional, which is a smaller cost
than a fourth simultaneous migration.

**Keeping `deploy/api/page.py` and evolving it.** The Python function renders a
snapshot server-side and is well matched to a board that is a snapshot. Phase 2
retires the snapshot: keyset pagination per lane, server-side filters, a
websocket applying events, and a virtualised list that never receives 500,000
rows. That is a client-side application, and building it inside a function whose
whole design is "render the state and return HTML" would mean writing a
framework rather than using one.

## The correction

§2 justifies this with "already works, already deploys on merge. Do not move it
during a rebuild that is changing everything else." The host and the deploy
trigger are indeed already true. **Next.js is not.** The board today is
`deploy/api/page.py`, a Python serverless function rendering sanitised KV state
with a bundled copy of the renderer, alongside `deploy/api/board.js` for the
queue, document, logo, health and provider routes. There is no Next.js in the
repository.

So this decision is a rewrite of the web surface wearing the clothes of
continuity. The reading that holds is: the host and the deploy trigger stay,
the framework is new in Phase 2. Any other reading has "do not move it" also
meaning "build it in a framework it is not written in".

This is recorded rather than treated as a reason to stop, because the decision
is fine and only its stated justification is wrong. It matters because whoever
picks up Phase 2 will otherwise budget for a port and find a rewrite.

## Consequences

`scripts/sync_vercel_renderer.py` keeps `deploy/renderer/` and `deploy/assets/`
in step with `scripts/`, and CI fails a PR when they drift. That check exists
because three commits of UI work once sat merged and invisible while the
deployment served an older bundle, and because the renderer once read a favicon
from `deploy/public/`, which every laptop has and the serverless bundle does
not. Whatever replaces `page.py` inherits both hazards and needs its own version
of that check, or the outages come back.

Verifying a deploy is not the same as merging. A merge to `main` here has
produced no Vercel deployment at all, silently, twice in one night. The check
is `GET /healthz` and comparing `rendererCommit` to `origin/main`.
