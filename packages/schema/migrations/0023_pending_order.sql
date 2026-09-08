-- Which work order is outstanding on an application, and since when.
--
-- **Not "who wrote this row".** The obvious marker is a provenance flag saying
-- v2 wrote it, and a provenance flag is permanent: it would exempt every v2
-- row from parity for ever, and the one v2 finding that exists today is
-- precisely a row nothing ever acted on. Scale Media 49dac26ae2c5c6ef holds
-- `inflight` in Postgres against a session file that has said `watch` since
-- 2026-09-05T21:36:21Z. v2 wrote it at 2026-09-07T00:18:36Z and #206, which
-- added the enqueue, landed at 02:20:48Z -- two hours later. The click
-- recorded a state and ordered no work, which is the failure `route.ts`'s own
-- comment names, and a "v2 wrote this" exemption would have hidden it.
--
-- So the excusable state is not "v2 touched this" but "the device has not
-- answered yet", and that one ends by itself. `route.ts` sets these in the
-- same transaction as the status it writes, naming the queue it enqueued to.
-- The device clears them when it answers, in `class_b`. A row whose marker is
-- still set is a row the device never answered, and past the age bound that is
-- the finding rather than an excuse.
--
-- Measured reconciliation on the four v2 writes that completed: 83s, 130s,
-- 138s, 284s. The bound in `verify_parity` is a specification rather than a
-- percentile and says so; a queued apply additionally waits for a free slot.
--
-- Additive. Both columns are exempt in `mirror_fields` with their reasons,
-- because they are the mirror's own bookkeeping and the device file has no
-- equivalent to compare against.
alter table applications
  add column if not exists pending_order    text,
  add column if not exists pending_order_at timestamptz;

-- Partial: the overwhelming majority of rows have no order outstanding, and
-- the only query is "which are still marked".
create index if not exists applications_pending_order_idx
  on applications (pending_order_at)
  where pending_order is not null;
