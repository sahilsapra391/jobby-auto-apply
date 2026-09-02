// The board's half of the channel protocol, driven by a real `Channel`.
//
// **Both ends are the shipping code.** The events the client sees are minted
// by `apps/worker/src/channel.mjs` and the client is
// `apps/web/app/_ui/live.ts`, connected by a wire this file can break on
// purpose. Testing either against a mock of the other is how two halves that
// are each correct compose into a page that renders nothing, which is already
// written up in CLAUDE.md from the /settings outage.
//
// **The negative tests are first because they were written first.** A client
// that applies every event it is given and loses one on reconnect is worse
// than one that applies none: it shows fiction with full confidence.
//
// Every resilience assertion here has a control beside it. A client that
// resynced on everything, or dropped everything, would satisfy the resilient
// half of each pair on its own.

import { test } from "node:test";
import assert from "node:assert/strict";
import { Channel, RING } from "../apps/worker/src/channel.mjs";
import { LiveSession } from "../apps/web/app/_ui/live.ts";

const event = (kind = "application.changed", payload = {}) => ({ kind, payload });

/**
 * A wire between a channel and a session, which can lose messages.
 *
 * `deliver` is deliberately awaited: the session's resync is async and a test
 * asserting a refetch happened has to be able to wait for it.
 */
function wire(channel, { seq = 0 } = {}) {
  const applied = [];
  const fetches = [];
  let failFetch = false;
  const held = [];
  let dropping = false;

  const session = new LiveSession({
    seq,
    apply: (e) => applied.push(e.seq),
    refetch: async () => {
      fetches.push(session.seq);
      if (failFetch) throw new Error("the lane would not load");
    },
  });

  const socket = {
    send(message) {
      if (dropping) { held.push(message); return; }
      pending.push(session.receive(message));
    },
  };
  const pending = [];
  channel.subscribe(socket);

  return {
    session, applied, fetches, held,
    drop(on) { dropping = on; },
    failFetch(on) { failFetch = on; },
    /** Wait for every message handed over so far to be handled. */
    async settle() { const all = pending.splice(0); return Promise.all(all); },
    /** What the client would send on reconnect, through the real channel. */
    async reconnect() {
      const answer = channel.resume(session.hello().resume_from);
      return session.receive(answer);
    },
  };
}

/* ------------------------------------------------------------ negative 1 */

test("a gap is never applied; the client refetches instead", async () => {
  const channel = new Channel();
  const w = wire(channel);

  channel.publish(event());          // seq 1, delivered
  await w.settle();

  w.drop(true);
  channel.publish(event());          // seq 2, lost on the wire
  w.drop(false);
  channel.publish(event());          // seq 3, arrives with a hole behind it
  const [verdict] = await w.settle();

  assert.equal(verdict, "resynced", "a hole is a resync, not an apply");
  assert.deepEqual(w.applied, [1],
    "the event after the gap must not be applied: every individual update " +
    "correct with the state between them missing is what fiction looks like");
  assert.equal(w.fetches.length, 1, "it went and refetched");
  assert.equal(w.session.seq, 3,
    "and resumed from the gapped event's own seq, because the refetch read a " +
    "database that already holds what it announced");
});

test("control: no gap, so the same client applies and does not refetch", async () => {
  const channel = new Channel();
  const w = wire(channel);

  channel.publish(event());
  channel.publish(event());
  channel.publish(event());
  const verdicts = await w.settle();

  assert.deepEqual(verdicts, ["applied", "applied", "applied"]);
  assert.deepEqual(w.applied, [1, 2, 3]);
  assert.equal(w.fetches.length, 0,
    "a client that resyncs on everything would pass the test above");
});

/* ------------------------------------------------------------ negative 2 */

test("a resync that fails does not advance the counter", async () => {
  const channel = new Channel();
  const w = wire(channel);
  w.failFetch(true);

  w.drop(true);
  channel.publish(event());
  w.drop(false);
  channel.publish(event());
  const [verdict] = await w.settle();

  assert.equal(verdict, "resync-failed");
  assert.equal(w.session.seq, 0,
    "advancing over events it never fetched is the client believing it is " +
    "caught up when it is two behind, and nothing would ever say so");
  assert.deepEqual(w.applied, []);
});

test("control: the same resync, succeeding, does advance it", async () => {
  const channel = new Channel();
  const w = wire(channel);

  w.drop(true);
  channel.publish(event());
  w.drop(false);
  channel.publish(event());
  const [verdict] = await w.settle();

  assert.equal(verdict, "resynced");
  assert.equal(w.session.seq, 2, "caught up, because the refetch worked");
});

/* ------------------------------------------------------------ negative 3 */

test("more than the ring holds is an explicit resync, never a silent gap", async () => {
  const channel = new Channel();
  const w = wire(channel);
  channel.publish(event());
  await w.settle();

  w.drop(true);
  for (let i = 0; i < RING + 50; i += 1) channel.publish(event());
  w.drop(false);

  const verdict = await w.reconnect();
  assert.equal(verdict, "resynced");
  assert.equal(w.fetches.length, 1);
  assert.equal(w.session.seq, channel.seq, "caught up to the channel");
  assert.deepEqual(w.applied, [1],
    "nothing from the overflow is applied piecemeal");
});

test("control: inside the ring it is a replay, applied in order, no refetch", async () => {
  const channel = new Channel();
  const w = wire(channel);
  channel.publish(event());
  await w.settle();

  w.drop(true);
  for (let i = 0; i < 200; i += 1) channel.publish(event());
  w.drop(false);

  const verdict = await w.reconnect();
  assert.equal(verdict, "replayed");
  assert.equal(w.fetches.length, 0, "a replay is not a resync");
  assert.equal(w.applied.length, 201);
  assert.deepEqual(w.applied, w.applied.map((_, i) => i + 1),
    "contiguous and in order, which is the whole promise");
  assert.equal(w.session.seq, channel.seq);
});

/* ------------------------------------------------------------ negative 4 */

test("a channel lying about contiguity is caught, not believed", async () => {
  const channel = new Channel();
  const w = wire(channel);

  // A replay whose events skip one. The real channel does not build these;
  // checking anyway costs a comparison and is the difference between a
  // divergence and a resync.
  const verdict = await w.session.receive({
    kind: "replay", seq: 9,
    events: [
      { seq: 1, kind: "application.changed", payload: {} },
      { seq: 3, kind: "application.changed", payload: {} },
    ],
  });

  assert.equal(verdict, "resynced");
  assert.deepEqual(w.applied, [],
    "nothing from a replay with a hole in it is applied, not even the part " +
    "before the hole");
});

test("control: the same shape, contiguous, is applied", async () => {
  const channel = new Channel();
  const w = wire(channel);
  const verdict = await w.session.receive({
    kind: "replay", seq: 2,
    events: [
      { seq: 1, kind: "application.changed", payload: {} },
      { seq: 2, kind: "application.changed", payload: {} },
    ],
  });
  assert.equal(verdict, "replayed");
  assert.deepEqual(w.applied, [1, 2]);
});

/* ------------------------------------------------------- idempotence */

test("an event already seen is dropped, and the counter does not rewind", async () => {
  const channel = new Channel();
  const w = wire(channel);
  channel.publish(event());
  channel.publish(event());
  await w.settle();

  const again = await w.session.receive(
    { seq: 1, kind: "application.changed", payload: {} });

  assert.equal(again, "duplicate");
  assert.deepEqual(w.applied, [1, 2], "not applied a third time");
  assert.equal(w.session.seq, 2,
    "rewinding to it would re-apply everything after it too");
});

test("control: the next one after a duplicate still applies", async () => {
  const channel = new Channel();
  const w = wire(channel);
  channel.publish(event());
  await w.settle();
  await w.session.receive({ seq: 1, kind: "application.changed", payload: {} });
  channel.publish(event());
  await w.settle();
  assert.deepEqual(w.applied, [1, 2],
    "a client that drops everything would pass the duplicate test");
});

/* ---------------------------------------------------------- malformed */

test("a message that will not parse is not skipped past", async () => {
  const channel = new Channel();
  const w = wire(channel);
  channel.publish(event());
  await w.settle();

  assert.equal(await w.session.receive("{not json"), "malformed");
  assert.equal(await w.session.receive({ seq: "two" }), "malformed");
  assert.equal(w.session.seq, 1,
    "advancing over an unreadable message loses whatever it carried and " +
    "says nothing");
});

/* ------------------------------------------------------- single flight */

test("a burst of gaps refetches once, not once each", async () => {
  const channel = new Channel();
  const w = wire(channel);
  let release;
  const slow = new Promise((r) => { release = r; });
  const session = new LiveSession({
    apply: () => {},
    refetch: async () => { fetches += 1; await slow; },
  });
  let fetches = 0;

  const a = session.receive({ seq: 5, kind: "application.changed", payload: {} });
  const b = session.receive({ seq: 9, kind: "application.changed", payload: {} });
  const c = session.receive({ seq: 12, kind: "application.changed", payload: {} });
  release();
  await Promise.all([a, b, c]);

  assert.equal(fetches, 2,
    "one fetch for the first gap and one more covering the two that arrived " +
    "while it ran; a gap during a refetch is remembered rather than dropped");
});

test("control: gaps that do not overlap each get their own refetch", async () => {
  let fetches = 0;
  const session = new LiveSession({
    apply: () => {}, refetch: async () => { fetches += 1; },
  });
  await session.receive({ seq: 5, kind: "application.changed", payload: {} });
  await session.receive({ seq: 9, kind: "application.changed", payload: {} });
  assert.equal(fetches, 2, "single-flight must not swallow a later gap");
});

/* ------------------------------------------------------------- hello */

test("hello carries what the client actually has, so a reconnect is exact", async () => {
  const channel = new Channel();
  const w = wire(channel, { seq: 0 });
  channel.publish(event());
  channel.publish(event());
  await w.settle();

  assert.deepEqual(w.session.hello(), { resume_from: 2 });
  const answer = channel.resume(w.session.hello().resume_from);
  assert.equal(answer.kind, "replay");
  assert.deepEqual(answer.events, [], "nothing missed, so nothing replayed");
});
