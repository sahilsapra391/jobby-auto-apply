/**
 * Optimistic changes, and the acknowledgement each one is waiting for.
 *
 * **The board may show a change before the server has agreed to it. It may not
 * keep showing one the server never agreed to.** Those are different rules and
 * only the second is hard: the reverting path is the one nobody exercises, so
 * it is the one that rots, and its failure looks exactly like success.
 *
 * The acknowledgement is the POST's own answer, not the channel event. The
 * commit precedes the publish and a publish may fail after a commit that
 * worked, so a board waiting for the event would revert a change that actually
 * happened. The event is how *other* tabs find out.
 *
 * Plain TypeScript, no React, no DOM: `tests/test_optimistic.test.mjs` runs
 * this file rather than something shaped like it.
 */

export type Outcome =
  | { ok: true }
  | { ok: false; reason: string; retryable: boolean };

/** How long the board waits before it stops believing in an answer. */
export const DEADLINE_MS = 12_000;

type Entry = {
  key: string;
  subject: string;
  body: unknown;
  undo: () => void;
  at: number;
  undone: boolean;
  settled: boolean;
  /** Channel events for this subject that arrived while it was pending. */
  absorbed: number;
};

/**
 * The pending ledger.
 *
 * A subject is whatever the change is about, an application id here. It is the
 * key the ledger dedupes and absorbs on, so it has to be the thing an event
 * also names, or absorbing silently stops working while every test passes.
 */
export class Mutations {
  #pending = new Map<string, Entry>();
  #now: () => number;
  #newKey: () => string;

  constructor({ now = Date.now, newKey = () => crypto.randomUUID() } = {}) {
    this.#now = now;
    this.#newKey = newKey;
  }

  get size() { return this.#pending.size; }

  /**
   * Apply a change locally and start waiting for the server.
   *
   * **The key is minted here and kept**, so a retry sends the same one. That
   * is the half of idempotence the server cannot supply: a client that mints a
   * fresh key per attempt turns the server's dedupe into decoration, and a
   * double-tap becomes two applications under a real name.
   */
  begin({ subject, body, undo }: {
    subject: string; body: unknown; undo: () => void;
  }): { key: string; body: unknown } {
    const key = this.#newKey();
    this.#pending.set(key, {
      key, subject, body, undo, at: this.#now(),
      undone: false, settled: false, absorbed: 0,
    });
    return { key, body };
  }

  /** The same key and the same body, so the server recognises the retry. */
  retry(key: string): { key: string; body: unknown } {
    const entry = this.#pending.get(key);
    if (!entry) {
      throw new Error(
        `retry of an unknown mutation ${key}: a key that has left the ledger ` +
        `has already been settled, and re-sending it under a new one would ` +
        `defeat the server's dedupe`);
    }
    entry.at = this.#now();
    entry.settled = false;
    return { key, body: entry.body };
  }

  /**
   * The server answered. Confirm, or put the board back.
   *
   * Returns whether events for this subject were absorbed while it was in
   * flight, because those were dropped on the floor and the caller has to go
   * and refetch the subject rather than assume its own change was the only one.
   */
  settle(key: string, outcome: Outcome): { reverted: boolean; absorbed: number } {
    const entry = this.#pending.get(key);
    if (!entry) {
      // Not a no-op. A settle with no entry behind it is a revert that will
      // never run, and the board keeps drawing a change nobody made.
      throw new Error(
        `settled an unknown mutation ${key}: the undo for it is gone and the ` +
        `board is now showing a change with nothing behind it`);
    }
    this.#pending.delete(key);
    if (!outcome.ok && !entry.undone) {
      entry.undone = true;
      entry.undo();
    }
    return { reverted: !outcome.ok, absorbed: entry.absorbed };
  }

  /**
   * Mutations whose deadline has passed, reverted.
   *
   * **This is what "a real ack rather than hope" costs.** Without it a request
   * that is never answered leaves the optimistic change on screen for as long
   * as the tab is open, which is indistinguishable from having worked.
   */
  sweep(): Array<{ key: string; subject: string }> {
    const late: Array<{ key: string; subject: string }> = [];
    const cutoff = this.#now() - DEADLINE_MS;
    for (const entry of [...this.#pending.values()]) {
      if (entry.at > cutoff) continue;
      late.push({ key: entry.key, subject: entry.subject });
      this.settle(entry.key, {
        ok: false,
        reason: "the server did not answer, so the board put this back",
        retryable: true,
      });
    }
    return late;
  }

  /**
   * Is a change to this subject waiting for an answer? **Reads nothing else.**
   *
   * Separate from `absorbs` on purpose: that one counts what it held, so it
   * has a side effect and calling it to decide whether to grey a button would
   * inflate the count from the render loop, which runs whenever React feels
   * like it.
   */
  pending(subject: string): boolean {
    for (const entry of this.#pending.values()) {
      if (entry.subject === subject) return true;
    }
    return false;
  }

  /**
   * Should a channel event for this subject be held rather than applied?
   *
   * This tab has already drawn its own change; applying the event that
   * announces it would draw it a second time. Counting what was held is what
   * lets `settle` say the subject needs a refetch, so a *different* change to
   * the same subject arriving in the window is not lost, only late.
   */
  absorbs(subject: string): boolean {
    let held = false;
    for (const entry of this.#pending.values()) {
      if (entry.subject !== subject) continue;
      entry.absorbed += 1;
      held = true;
    }
    return held;
  }
}
