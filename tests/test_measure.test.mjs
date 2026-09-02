// The gate's stopwatch: what it records, and what it refuses to record.
//
// **The refusals are the interesting half.** A stopwatch that always produces
// a number produces one for a tab that is not painting, and that number is
// larger than any real one, in the direction that fails the gate for a reason
// nobody would find. Found by running the rig with the browser pane hidden:
// thirty iterations, zero samples, and the drive call hung waiting for a
// frame that a backgrounded page never delivers.
//
// `measure.ts` reads `window.location.search` and `document.hidden`, so this
// file supplies both. Everything else in it is real.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

/**
 * A page that paints on its own, like a real one.
 *
 * **The frame callback has to fire without the test asking**, or `drive` waits
 * for a frame nobody delivers and the whole file hangs: `drive` awaits a frame
 * after each iteration and two more at the end, and a queue only drained from
 * the test is empty at exactly those moments. Costing the suite a millisecond
 * per frame is the price of modelling the thing rather than a convenient
 * version of it.
 */
function stage({ measure = true, hidden = false } = {}) {
  globalThis.window = {
    location: { search: measure ? "?measure=1" : "?lane=found" },
  };
  globalThis.document = { hidden };
  globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
  return {
    /** Wait long enough for anything queued to have run. */
    async painted(frames = 6) {
      for (let i = 0; i < frames; i += 1) {
        await new Promise((r) => setTimeout(r, 1));
      }
    },
    hide() { globalThis.document.hidden = true; },
    show() { globalThis.document.hidden = false; },
  };
}

/** A fresh copy of the module, since it holds the samples in module state. */
async function load() {
  return import(`../apps/web/app/_ui/measure.ts?${Math.random()}`);
}

beforeEach(() => { stage(); });

/* ------------------------------------------------------------- refusals */

test("a tab that is not painting records nothing, and says how many", async () => {
  const page = stage({ hidden: true });
  const m = await load();

  for (let i = 0; i < 3; i += 1) m.mark("apply-event", "found")();
  await page.painted();

  assert.deepEqual(m.taken(), [],
    "a backgrounded page resumes requestAnimationFrame whenever it comes " +
    "back, which could be minutes; recording that as click-to-visible is a " +
    "fabricated number larger than any real one");
  assert.equal(m.droppedWhileHidden(), 3,
    "and the count is kept, or a short summary reads like a fast board");
});

test("control: the same three clicks on a painting tab are recorded", async () => {
  const page = stage({ hidden: false });
  const m = await load();
  for (let i = 0; i < 3; i += 1) m.mark("apply-event", "found")();
  await page.painted();
  assert.equal(m.taken().length, 3);
  assert.equal(m.droppedWhileHidden(), 0);
});

test("with ?measure absent the stopwatch is a no-op", async () => {
  const page = stage({ measure: false });
  const m = await load();
  m.mark("apply-event", "found")();
  await page.painted();
  assert.deepEqual(m.taken(), [],
    "instrumentation nobody asked for is overhead on every reader");
});

test("drive refuses a hidden page rather than hanging on it", async () => {
  stage({ hidden: true });
  const m = await load();
  await assert.rejects(
    () => m.drive({ ids: ["a"], n: 1, warmup: 0, apply: async () => {} }),
    /not painting/);
});

test("drive refuses when the lane has nothing to ask about", async () => {
  stage();
  const m = await load();
  await assert.rejects(() => m.drive({ ids: [], apply: async () => {} }),
                       /nothing on screen/);
});

/* --------------------------------------------------------- one per click */

test("stopping twice records one sample, not two", async () => {
  const page = stage();
  const m = await load();
  const stop = m.mark("apply-event", "found");
  stop(); stop(); stop();
  await page.painted();
  assert.equal(m.taken().length, 1);
});

test("the sample carries the kind and the lane it was started with", async () => {
  const page = stage();
  const m = await load();
  m.mark("write-optimistic", "watch")();
  await page.painted();
  const [got] = m.taken();
  assert.equal(got.kind, "write-optimistic");
  assert.equal(got.lane, "watch");
  assert.equal(typeof got.ms, "number");
});

/* ------------------------------------------------------- cold and warm */

test("drive relabels its warm-up rather than discarding it", async () => {
  const page = stage();
  const m = await load();
  // `apply` is what starts the clock in the real board, so it does here too.
  const apply = async () => { m.mark("apply-event", "found")(); };
  await m.drive({ ids: ["a", "b"], n: 4, warmup: 3, apply });
  await page.painted();

  const kinds = m.taken().map((s) => s.kind);
  assert.equal(kinds.filter((k) => k === "apply-event-cold").length, 3,
    "the warm-up is kept and named, because a reader really does meet it " +
    "once per page load");
  assert.equal(kinds.filter((k) => k === "apply-event").length, 4);
});

test("drive starts no stopwatch of its own", async () => {
  const page = stage();
  const m = await load();
  let started = 0;
  const apply = async () => { started += 1; m.mark("apply-event", "x")(); };
  await m.drive({ ids: ["a"], n: 5, warmup: 0, apply });
  await page.painted();
  assert.equal(started, 5);
  assert.equal(m.taken().length, 5,
    "a second mark around apply would record every iteration twice, from a " +
    "slightly earlier instant, and the two would be indistinguishable");
});

/* ------------------------------------------------------------ summary */

test("a lane is never pooled with another", async () => {
  const m = await load();
  const rows = m.summarise([
    { kind: "apply-event", lane: "flight", ms: 400, at: 0 },
    { kind: "apply-event", lane: "found", ms: 20, at: 0 },
    { kind: "apply-event", lane: "found", ms: 20, at: 0 },
  ]);
  assert.deepEqual(rows.map((r) => [r.kind, r.lane, r.n]),
                   [["apply-event", "flight", 1], ["apply-event", "found", 2]]);
  assert.equal(rows[0].p95, 400, "worst first, and its own number");
});

test("an empty run is NaN rather than zero", async () => {
  const m = await load();
  assert.ok(Number.isNaN(m.percentile([], 95)),
            "zero would pass the gate on a run that measured nothing");
});

test("percentile does not reorder what it is given", async () => {
  const m = await load();
  const values = [3, 1, 2];
  m.percentile(values, 50);
  assert.deepEqual(values, [3, 1, 2]);
});
