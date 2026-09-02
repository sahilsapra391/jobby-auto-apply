// The optimistic ledger: what happens when the acknowledgement does not come.
//
// **The reverting path is the one nobody exercises, so it is the one that
// rots, and its failure looks exactly like success.** A board that shows a
// change and keeps showing it is indistinguishable, from the reader's chair,
// from a board whose write landed. So the negative tests are first and every
// one of them has a control: a ledger that reverted everything would satisfy
// the resilient half of each pair on its own.

import { test } from "node:test";
import assert from "node:assert/strict";
import { Mutations, DEADLINE_MS } from "../apps/web/app/_ui/optimistic.ts";

/** A clock the test moves, so a twelve-second deadline costs no seconds. */
function clock(at = 1_000_000) {
  return { now: () => at, tick(ms) { at += ms; } };
}

/** A ledger with a predictable key, so assertions can name one. */
function ledger(c = clock()) {
  let n = 0;
  const m = new Mutations({ now: c.now, newKey: () => `k${(n += 1)}` });
  return { m, c };
}

/** A board row that can be moved and put back. */
function card(lane = "found") {
  const state = { lane };
  return {
    state,
    move(to) { const was = state.lane; state.lane = to; return () => { state.lane = was; }; },
  };
}

/* ------------------------------------------------------------ negative 1 */

test("a refused write puts the board back, and says why", () => {
  const { m } = ledger();
  const row = card();
  const undo = row.move("watch");
  const { key } = m.begin({ subject: "app-1", body: { status: "watch" }, undo });

  assert.equal(row.state.lane, "watch", "shown before the server agreed");
  const out = m.settle(key, {
    ok: false, reason: "writes are off for this account", retryable: false,
  });

  assert.equal(out.reverted, true);
  assert.equal(row.state.lane, "found", "and put back when it refused");
  assert.equal(m.size, 0);
});

test("control: a write that succeeds is not put back", () => {
  const { m } = ledger();
  const row = card();
  const { key } = m.begin({
    subject: "app-1", body: {}, undo: row.move("watch"),
  });
  const out = m.settle(key, { ok: true });
  assert.equal(out.reverted, false);
  assert.equal(row.state.lane, "watch",
    "a ledger that reverted everything would pass the test above");
});

/* ------------------------------------------------------------ negative 2 */

test("no answer at all reverts on the deadline; hope has one", () => {
  const { m, c } = ledger();
  const row = card();
  m.begin({ subject: "app-1", body: {}, undo: row.move("watch") });

  c.tick(DEADLINE_MS + 1);
  const late = m.sweep();

  assert.deepEqual(late, [{ key: "k1", subject: "app-1" }]);
  assert.equal(row.state.lane, "found",
    "a request nobody answers leaves the change on screen for as long as the " +
    "tab is open, which is indistinguishable from having worked");
  assert.equal(m.size, 0);
});

test("control: inside the deadline the sweep leaves it alone", () => {
  const { m, c } = ledger();
  const row = card();
  m.begin({ subject: "app-1", body: {}, undo: row.move("watch") });

  c.tick(DEADLINE_MS - 1);
  assert.deepEqual(m.sweep(), [], "not late yet");
  assert.equal(row.state.lane, "watch");
  assert.equal(m.size, 1, "a ledger that swept everything would pass above");
});

/* ------------------------------------------------------------ negative 3 */

test("settling a key the ledger does not hold is a fault, not a no-op", () => {
  const { m } = ledger();
  assert.throws(
    () => m.settle("k9", { ok: false, reason: "no", retryable: false }),
    /undo for it is gone/,
    "a settle with no entry behind it is a revert that will never run, and " +
    "the board keeps drawing a change nobody made");
});

test("a swept mutation cannot then be settled by a late answer", () => {
  const { m, c } = ledger();
  const row = card();
  const { key } = m.begin({ subject: "app-1", body: {}, undo: row.move("watch") });
  c.tick(DEADLINE_MS + 1);
  m.sweep();

  // The undo has already run. A late 200 arriving now must not be able to
  // reach an entry that is gone; it raises rather than silently doing nothing.
  assert.throws(() => m.settle(key, { ok: true }), /unknown mutation/);
  assert.equal(row.state.lane, "found", "and the undo ran exactly once");
});

/* --------------------------------------------------------------- retry */

test("a retry sends the same key and the same body", () => {
  const { m, c } = ledger();
  const row = card();
  const first = m.begin({
    subject: "app-1", body: { status: "watch" }, undo: row.move("watch"),
  });
  c.tick(5_000);
  const again = m.retry(first.key);

  assert.equal(again.key, first.key,
    "a fresh key per attempt turns the server's dedupe into decoration, and " +
    "a double-tap becomes two applications under a real name");
  assert.deepEqual(again.body, { status: "watch" });
});

test("a retry restarts the deadline rather than inheriting a spent one", () => {
  const { m, c } = ledger();
  const row = card();
  const { key } = m.begin({ subject: "app-1", body: {}, undo: row.move("watch") });
  c.tick(DEADLINE_MS - 1);
  m.retry(key);
  c.tick(DEADLINE_MS - 1);
  assert.deepEqual(m.sweep(), [], "the clock restarted with the new attempt");
  assert.equal(m.size, 1);
});

/* ------------------------------------------------------------- absorb */

test("an event for a subject this tab already drew is held, not drawn twice", () => {
  const { m } = ledger();
  const row = card();
  const { key } = m.begin({ subject: "app-1", body: {}, undo: row.move("watch") });

  assert.equal(m.absorbs("app-1"), true);
  const out = m.settle(key, { ok: true });
  assert.equal(out.absorbed, 1,
    "settle has to report it: the event was dropped on the floor, so a " +
    "*different* change to the same subject in that window is late, not lost");
});

test("control: an event for a subject with nothing pending is not held", () => {
  const { m } = ledger();
  m.begin({ subject: "app-1", body: {}, undo: () => {} });
  assert.equal(m.absorbs("app-2"), false,
    "a ledger that absorbed everything would make the board deaf while any " +
    "one change was in flight");
});

test("absorbing keys on the subject an event actually names", () => {
  const { m } = ledger();
  const { key } = m.begin({ subject: "app-1", body: {}, undo: () => {} });
  m.absorbs("app-1");
  m.absorbs("app-1");
  assert.equal(m.settle(key, { ok: true }).absorbed, 2, "counted, not flagged");
});
