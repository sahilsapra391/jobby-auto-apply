# ADR 0003: Scan workers are Python containers, not rewritten Workers

- Status: accepted
- Date: 2026-08-29
- Source: §2 and §5, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

Sourcing runs on the laptop. Moving it to the cloud is the point of Phase 3.
Cloudflare Workers would put it next to the Durable Objects, at the cost of
rewriting every connector in TypeScript.

## Decision

Containerise the existing Python and run it on Fly.io. Cron fires, the
scheduler enqueues one message per ATS board into Cloudflare Queues, Fly workers
consume and upsert into Postgres.

## Rejected

**Rewriting the connectors for Workers.** A large rewrite of code that works,
with no behavioural gain. §13 states the plan does not do this.

**Railway and Render.** Equivalent. Fly is the default for scale-to-zero and
cheap scheduled machines. §12 leaves this open with Fly as the default and it
blocks nothing before Phase 3.

## Consequences

Concurrency is capped per ATS host rather than globally. A global cap still
allows 400 parallel requests to one Greenhouse tenant, which is how an IP gets
blocked for everyone.

Not every connector may move. Public board JSON APIs are safe to centralise:
they are documented, intended for consumption, and are not IP-sensitive the way
a careers page with a bot check is. Anything that works by rendering a page in a
browser stays on the device, which has the residential IP and the logged-in
profile. Phase 3 audits each connector and writes the list down.

The failure to design against is a connector that errors being read as a board
returning nothing, which closes an employer's whole catalogue in one sweep.
`sweep_runs` with a status, and closure reading only from complete sweeps, is
the guard. It is the highest-consequence failure in the system: it can empty
the pool.
