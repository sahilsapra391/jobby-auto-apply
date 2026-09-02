// The account channel's state machine: seq, the ring, replay and resync.
//
// **The negative tests are first in this file because they were written
// first.** A channel that fans out correctly and loses an event on reconnect
// is worse than one that does not fan out at all: the board shows fiction with
// full confidence, which is what a queue wipe did once already while the board
// still drew fourteen destroyed requests as in flight.
//
// The state machine is plain JavaScript with no Workers API in it, so this runs
// under `node --test` against the real thing rather than a mock of it. The
// Durable Object is a shell around this; what has to be correct is here.

import { test } from "node:test";
import assert from "node:assert/strict";
import { Channel, RING } from "../apps/worker/src/channel.mjs";

const event = (kind, payload = {}) => ({ kind, payload });

/** A subscriber that records what it was sent, and can be killed. */
function tab() {
  const got = [];
  const socket = {
    got,
    alive: true,
    send(message) {
      if (!socket.alive) throw new Error("send on a dead socket");
      got.push(JSON.parse(message));
    },
    kill() { socket.alive = false; },
  };
  return socket;
}

/* ------------------------------------------------------------ negative 1 */

test("a socket killed mid-session resyncs rather than diverging", () => {
  const channel = new Channel();
  const a = tab();
  channel.subscribe(a);

  channel.publish(event("application.changed"));
  const lastSeen = a.got.at(-1).seq;

  // The tab goes away without saying goodbye, and the world moves on.
  a.kill();
  channel.publish(event("application.phase"));
  channel.publish(event("application.completed"));

  // It comes back and asks to continue from what it had.
  const answer = channel.resume(lastSeen);
  assert.equal(answer.kind, "replay",
    "two events inside the ring is a replay, not a resync");
  assert.deepEqual(answer.events.map((e) => e.seq), [lastSeen + 1, lastSeen + 2],
    "the replay is exactly what it missed, in order, with no gap");
});

test("a dead socket does not stop the others being served", () => {
  const channel = new Channel();
  const dead = tab();
  const live = tab();
  channel.subscribe(dead);
  channel.subscribe(live);
  dead.kill();

  channel.publish(event("application.changed"));

  assert.equal(live.got.length, 1,
    "one broken subscriber must not cost every other tab its events");
  assert.equal(channel.subscribers.size, 1, "the dead socket is dropped");
});

/* ------------------------------------------------------------ negative 2 */

test("more events than the ring holds gives an explicit resync, never a gap", () => {
  const channel = new Channel();
  const a = tab();
  channel.subscribe(a);
  channel.publish(event("application.changed"));
  const lastSeen = a.got.at(-1).seq;
  a.kill();
  channel.subscribers.delete(a);

  for (let i = 0; i < RING + 50; i += 1) channel.publish(event("pool.delta"));

  const answer = channel.resume(lastSeen);
  assert.equal(answer.kind, "resync",
    "what the ring no longer covers must be named, not silently skipped");
  assert.equal(answer.events, undefined, "a resync carries no partial replay");
});

test("200 events while disconnected is a full replay, because the ring holds it", () => {
  const channel = new Channel();
  const a = tab();
  channel.subscribe(a);
  channel.publish(event("application.changed"));
  const lastSeen = a.got.at(-1).seq;
  a.kill();
  channel.subscribers.delete(a);

  for (let i = 0; i < 200; i += 1) channel.publish(event("pool.delta"));

  const answer = channel.resume(lastSeen);
  assert.equal(answer.kind, "replay");
  assert.equal(answer.events.length, 200, "every one of them, not a sample");
  const seqs = answer.events.map((e) => e.seq);
  assert.deepEqual(seqs, seqs.map((_, i) => lastSeen + 1 + i),
    "contiguous: a replay with a hole in it is the gap this test exists for");
});

test("resume from a seq the channel has never issued is a resync", () => {
  const channel = new Channel();
  channel.publish(event("application.changed"));
  assert.equal(channel.resume(9999).kind, "resync",
    "a client ahead of the channel is a client whose state cannot be trusted");
});

/* ------------------------------------------------------------------ seq */

test("seq is monotonic and starts at one", () => {
  const channel = new Channel();
  const seqs = [1, 2, 3].map(() => channel.publish(event("pool.delta")).seq);
  assert.deepEqual(seqs, [1, 2, 3]);
});

test("seq survives a rehydrate, because a Durable Object hibernates", () => {
  const channel = new Channel();
  channel.publish(event("pool.delta"));
  channel.publish(event("pool.delta"));
  const saved = channel.snapshot();

  const woken = Channel.restore(saved);
  assert.equal(woken.publish(event("pool.delta")).seq, 3,
    "a hibernated channel that restarts seq would replay ids it already used");
});

/* ------------------------------------------------------------- envelope */

test("an event carries ids and never a rendered row", () => {
  const channel = new Channel();
  const published = channel.publish({
    kind: "application.changed",
    payload: { application_id: "abc", to: "dropped" },
  });
  assert.deepEqual(Object.keys(published).sort(),
    ["account_id", "at", "kind", "payload", "seq"]);
  assert.equal(published.payload.application_id, "abc");
});

test("Found has no from, so leaving it is its own kind", () => {
  const channel = new Channel();
  assert.throws(() => channel.publish({
    kind: "application.changed",
    payload: { application_id: "abc", from: "found", to: "dropped" },
  }), /found/i,
    "Found is the absence of a decision row and has no from; a posting " +
    "entering the exclusion set is posting.decided");
});

test("an unknown kind is refused rather than fanned out", () => {
  const channel = new Channel();
  assert.throws(() => channel.publish({ kind: "job_decision.changed" }),
    /kind/i,
    "job_decision.changed is the superseded name and must not be accepted");
});

/* ------------------------------------------------------------- fan-out */

test("every open socket gets it, once", () => {
  const channel = new Channel();
  const a = tab(), b = tab();
  channel.subscribe(a);
  channel.subscribe(b);
  channel.publish(event("application.changed"));
  assert.equal(a.got.length, 1);
  assert.equal(b.got.length, 1);
  assert.equal(a.got[0].seq, b.got[0].seq, "one seq per fact, not per socket");
});

test("the ring never grows past its bound", () => {
  const channel = new Channel();
  for (let i = 0; i < RING * 2; i += 1) channel.publish(event("pool.delta"));
  assert.equal(channel.ring.length, RING,
    "an unbounded ring is a memory leak with a hibernation bill attached");
});
