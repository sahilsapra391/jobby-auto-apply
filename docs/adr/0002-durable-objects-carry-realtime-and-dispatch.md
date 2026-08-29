# ADR 0002: A Durable Object carries realtime and device dispatch

- Status: accepted
- Date: 2026-08-29
- Source: §2 and §4, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

A click takes up to 15 minutes to appear. The board is a snapshot rebuilt from
the whole pool on a floor and written into one KV key. Two separate problems
need a push channel: the browser, because polling a live board defeats the
point, and the laptop, which sits behind NAT and can never be dialled.

## Decision

One Cloudflare Durable Object per account, named by account id, holding the
websockets. It is the only inbound path to the device.

## Rejected

**Supabase Realtime.** Less code, and it works. It forces a Postgres migration
away from Neon, and it solves only half the problem: it gives the browser push
and does nothing about reaching the laptop, which is needed regardless.

**Polling.** This is the current bug, not an alternative to it.

## Consequences

A Durable Object is single-threaded per instance, so the per-account `seq` is a
real total order rather than a best effort. That is what makes optimistic UI
honest: the client can apply a change locally and reconcile against an ordered
replay, instead of hoping.

Naming the instance by account id makes tenant isolation structural. It is not a
filter someone can forget to apply, which is the difference between an
invariant and a convention.

The cost is a second API host, recorded separately in ADR 0005.

The reconnect path is where this earns its place or fails: the client sends
`resume_from`, and the DO either replays from its ring buffer or answers
`resync`. A silent gap is the one outcome that must be impossible, because the
failure it produces is the current one, a board that draws work as in flight
after the queue that held it was wiped.
