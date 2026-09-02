/**
 * The event kinds, one spelling shared by the route and the channel.
 *
 * Mirrors `apps/worker/src/channel.mjs`, and `tests/test_event_kinds.test.mjs`
 * compares the two rather than trusting either: two lists of strings in two
 * languages is what `LogoContractTests` exists to prevent.
 *
 * `job_decision.changed` is not here. It was the name in the original design
 * and it keyed the realtime layer on a projection that was never finished.
 */
export const KINDS = new Set([
  "application.changed",
  "application.phase",
  "application.completed",
  "application.needs_you",
  "posting.decided",
  "pool.delta",
  "device.status",
  "runner.update_required",
]);
