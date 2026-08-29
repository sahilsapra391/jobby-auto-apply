# ADR 0005: Two API hosts is the price of Durable Objects

- Status: accepted
- Date: 2026-08-29
- Source: §2, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

Choosing a Durable Object for realtime (ADR 0002) puts a Cloudflare Worker in
the request path. CRUD lives in Vercel route handlers next to the board.

## Decision

Vercel route handlers for CRUD. Workers only for the websocket channel and the
cron fan-out.

## Rejected

**A single API host.** Tidier, and unavailable: Durable Objects only run on
Cloudflare, and moving CRUD there means moving the web app there too, which
ADR 0004 declines during a rebuild that is changing everything else.

## Consequences

The seam is one direction. A Vercel route sets `app.account_id`, writes the row,
commits, then posts the event to the account's DO. Nothing on the Worker side
writes to Postgres. Keeping it one-directional is what stops the two hosts
developing separate opinions about what happened; a DO that could also write
would make "which one is right" a question with no answer.

The ordering matters and is not incidental: the row is committed before the
event is published. A published event that no row backs is a board showing a
change the database does not have, and on reconnect it disappears.

Two hosts means two deploy paths and two rollbacks. §7 gives each one a
one-command rollback, and the migration is the only surface without one.
