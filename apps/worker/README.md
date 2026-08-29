# apps/worker — Cloudflare Workers and Durable Objects (Phase 2)

Empty until Phase 2. What lands here:

- `TenantChannel`, one Durable Object per account: connect, subscribe, publish,
  a 500-event ring buffer, the monotonic per-account `seq`, and resume/resync.
- The cron trigger and queue producer for the sourcing sweep (Phase 3).
- The service-token endpoint Vercel route handlers post events to.

It is a second API host, and §2 says so plainly: "A single API host would be
tidier. Two hosts is the price of DOs. Keep the seam narrow and documented."
The seam is one direction only — Vercel writes to Postgres, then posts the
event here; nothing here writes to Postgres. Widening it is how the two hosts
start disagreeing about what happened.

The device socket terminates here too, and it is the only inbound path to the
user's laptop. Every rule in §6 and §8.1 about signed leases, a pinned cloud
key and the runner's own account-id check is about this file.
