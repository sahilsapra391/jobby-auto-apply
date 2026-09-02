/**
 * The gate's stopwatch: click to the frame that paints the change.
 *
 * > Measured p95 click-to-visible under 250 ms across all lanes, on a real
 * > device over a real network, recorded.
 *
 * **Naming the measurement point is the whole of it.** Server-side execution
 * for a Found page is 0.6 ms by date and 39.6 ms by score; a client on this
 * Mac sees 250 to 550 ms for the same queries, almost all round trip. 0.6 ms
 * says the query is fine and says nothing about whether the board feels live.
 * So the clock starts in the handler and stops at a paint, on the deployed
 * board, over the network the reader actually has. Anything measured on the
 * server or on localhost is a different quantity wearing the same units.
 *
 * **What "the frame that painted" means here, precisely.** A `requestAnimation
 * Frame` callback runs *before* the paint it belongs to, so a single one is
 * too early. The second runs after that frame has been presented, which is the
 * closest thing a page can observe without `element` timing on every changed
 * node. It over-reports by up to one frame, about 16 ms at 60 Hz, and that is
 * stated rather than corrected for: a stopwatch that quietly subtracts a
 * constant is a stopwatch nobody can check.
 */

export type Sample = { kind: string; lane: string; ms: number; at: number };

/** Everything measured this session, in arrival order. */
const samples: Sample[] = [];

/** Off unless the URL asks. Instrumentation nobody asked for is overhead. */
export function measuring(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).has("measure");
}

/**
 * Start the clock. The returned function stops it at the next painted frame.
 *
 * Safe to call when measurement is off: it returns a no-op, so a caller never
 * has to branch and a measurement never depends on remembering to.
 */
export function mark(kind: string, lane: string): () => void {
  if (!measuring()) return () => {};
  const t0 = performance.now();
  let stopped = false;
  return () => {
    if (stopped) return;   // one sample per click, whatever calls back twice
    stopped = true;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      // **A hidden tab does not paint, so there is nothing to time.** Browsers
      // pause `requestAnimationFrame` for a backgrounded page, and it resumes
      // whenever the tab comes back, which could be minutes. Recording that as
      // click-to-visible would be a fabricated number, larger than any real
      // one, in the direction that fails the gate for the wrong reason.
      //
      // Found by running the rig with the browser pane hidden: 30 iterations,
      // zero samples, and the drive call hung waiting for a frame. Dropping it
      // is right, and `drive` says so rather than returning an empty summary
      // that reads like a fast board.
      if (typeof document !== "undefined" && document.hidden) {
        dropped += 1;
        return;
      }
      samples.push({ kind, lane, ms: performance.now() - t0, at: Date.now() });
    }));
  };
}

/** Samples thrown away because the tab was not painting. */
let dropped = 0;
export const droppedWhileHidden = () => dropped;

/**
 * The percentile, by the nearest-rank method, on a copy.
 *
 * **`(p * n) / 100`, never `(p / 100) * n`.** The second form disagrees with
 * `scripts/gate_latency.percentile` on 27 of the 19,800 (p, n) pairs below 200
 * samples, because `0.07 * 100` is 7.000000000000001 and `Math.ceil` takes it
 * to 8. Multiplying first keeps an exact integer, and dividing an exact
 * multiple of 100 by 100 is exact in IEEE754.
 *
 * `tests/test_gate_latency.py` enumerates all 19,800 rather than restating the
 * rule, which is how the disagreement was found rather than argued about.
 */
export function percentile(values: number[], p: number): number {
  if (!values.length) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil((p * sorted.length) / 100);
  return sorted[Math.min(sorted.length - 1, Math.max(0, rank - 1))];
}

export type Summary = {
  kind: string; lane: string; n: number;
  p50: number; p95: number; max: number;
};

/**
 * One row per kind and lane.
 *
 * **Never pooled across lanes.** The gate reads "across all lanes", which is a
 * requirement on the worst of them and not on their average: a Found lane at
 * 400 ms hidden behind five empty lanes at 20 ms would report 90 ms and pass.
 */
export function summarise(rows: Sample[] = samples): Summary[] {
  // **A separator neither half can contain**, written as an escape rather
  // than as the byte: a literal NUL in a source file is invisible in an
  // editor, makes the file binary to `grep` and `git diff`, and survives
  // review because there is nothing to see.
  const SEP = "\u0000";
  const groups = new Map<string, Sample[]>();
  for (const s of rows) {
    const key = `${s.kind}${SEP}${s.lane}`;
    const held = groups.get(key);
    if (held) held.push(s); else groups.set(key, [s]);
  }
  return [...groups.entries()].map(([key, group]) => {
    const [kind, lane] = key.split(SEP);
    const ms = group.map((s) => s.ms);
    return {
      kind, lane, n: group.length,
      p50: percentile(ms, 50), p95: percentile(ms, 95), max: Math.max(...ms),
    };
  }).sort((a, b) => b.p95 - a.p95);
}

export const taken = () => samples.slice();
export const forget = () => { samples.length = 0; };

/**
 * Rename everything recorded since `from`.
 *
 * **A cold board and a warm one are different measurements and both are
 * real.** The first click after a page load competes with hydration, image
 * loads and a cold route handler; the fiftieth does not. Pooling them makes a
 * p95 that describes neither: measured at 40 iterations on Found, the series
 * ran 735, 44, 154, 248, 115, 228, 238, 243, 244, 250, 141, 131, 133 and then
 * a flat 33 for the remaining twenty-seven. Reported as one number that is
 * p95 244; reported as two it is a cold board around 250 and a warm one at 33.
 *
 * So the warm-up samples are kept and relabelled rather than discarded, and
 * the gate is read against the warm kind with the cold one beside it.
 */
function relabel(from: number, kind: string) {
  for (let i = from; i < samples.length; i += 1) samples[i].kind = kind;
}

declare global {
  interface Window {
    __jobbyMeasure?: {
      samples: () => Sample[];
      summary: () => Summary[];
      forget: () => void;
      drive: (options?: { n?: number }) => Promise<Summary[]>;
      /** The resync path: refetch page one and repaint it. */
      reload: () => Promise<void>;
    };
  }
}

/**
 * Run the apply path N times and hand back the summary.
 *
 * **This drives the real path and writes nothing.** Applying an event is: ask
 * `/api/lane/row` whether the lane still holds a subject, then paint the
 * answer. That is the same round trip and the same render a real event causes,
 * over the same network, and it is repeatable because it changes nothing.
 *
 * The write path cannot be measured until `live_board` is on. That number is
 * missing from any run taken before then, and the recorder says so rather than
 * reporting the reads and letting the table look complete.
 */
export async function drive(
  { lane = "found", n = 20, warmup = 15, ids = [] as string[], apply }:
  { lane?: string; n?: number; warmup?: number; ids?: string[];
    apply: (id: string) => Promise<void> },
): Promise<Summary[]> {
  if (!ids.length) throw new Error("nothing on screen to ask about");
  if (typeof document !== "undefined" && document.hidden) {
    throw new Error(
      "this tab is not painting, so there is nothing to time. A backgrounded " +
      "page pauses requestAnimationFrame and resumes it whenever it comes " +
      "back; bring the window to the front and run this again.");
  }
  // **No stopwatch here.** `apply` starts its own, at the point a real event
  // starts one. A second `mark` around it would record every iteration twice,
  // from a slightly earlier instant, and the two would be indistinguishable in
  // the summary: n would double and p95 would drift toward the wrapper's.
  void lane;
  const settle = () => new Promise((r) =>
    requestAnimationFrame(() => requestAnimationFrame(() => r(null))));
  const before = samples.length;
  for (let i = 0; i < warmup + n; i += 1) {
    await apply(ids[i % ids.length]);
    // One frame between iterations, so the paint being timed is this one.
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    if (i === warmup - 1) {
      // Let the last warm-up sample land before renaming the block.
      await settle();
      relabel(before, "apply-event-cold");
    }
  }
  // The last sample is still inside its own double-rAF. Two more frames, or
  // the summary is short by one and nothing says so.
  await new Promise((r) => requestAnimationFrame(
    () => requestAnimationFrame(() => r(null))));
  if (dropped) {
    console.warn(`${dropped} samples were dropped because the tab stopped ` +
                 `painting mid-run. The summary below is short by that many.`);
  }
  return summarise();
}
