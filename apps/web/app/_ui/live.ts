/**
 * The client half of the account channel: what a board does with a `seq`.
 *
 * **The channel promises contiguity. This verifies it rather than trusting
 * it.** A promise checked only by the party making it is not checked, which is
 * the argument `LogoContractTests` already makes about two implementations of
 * one rule. The cost of being wrong here is the failure this repository has
 * seen for real: a board drawing fourteen destroyed requests as in flight,
 * confident and wrong, because nothing told it what it had missed.
 *
 * Plain TypeScript with no React and no DOM, so `tests/test_live_client.test.mjs`
 * runs it against a real `Channel` rather than against a mock of one. Node 24
 * strips the types; the same file the board imports is the file under test.
 */

/** One event off the wire. Ids and a diff, never a rendered row. */
export type Envelope = {
  seq: number;
  account_id: string | null;
  kind: string;
  at: string;
  payload: Record<string, unknown>;
};

/** What the channel answers a `resume_from` with. */
export type Resume =
  | { kind: "replay"; seq: number; events: Envelope[] }
  | { kind: "resync"; seq: number };

/**
 * What `receive` did, so a test can tell the paths apart.
 *
 * `duplicate` and `applied` are the two halves of idempotence and a test
 * asserting one needs the other beside it, or a client that drops everything
 * passes.
 */
export type Verdict =
  | "applied" | "duplicate" | "gap" | "replayed" | "resynced"
  | "resync-failed" | "malformed";

/**
 * The rule that makes a resync safe, stated where it is relied on.
 *
 * A resync refetches, then resumes from the seq the channel reported *before*
 * the refetch ran. Anything published during that window is replayed on top of
 * a fetch that may already contain it, so **every event is applied at least
 * once and possibly twice**.
 *
 * That is survivable only because no event is accumulated. `application.changed`
 * says where a row went; `pool.delta` says the pool moved, not by how much.
 * Applying either twice lands in the same place. **Do not add an event carrying
 * a number to add**: it would be correct in every test and wrong exactly once,
 * during a reconnect, in a direction nobody would think to look.
 */
export class LiveSession {
  seq: number;
  #apply: (event: Envelope) => void;
  #refetch: () => Promise<void>;
  #resyncing = false;
  #resyncAgain = false;

  constructor({ seq = 0, apply, refetch }: {
    seq?: number;
    apply: (event: Envelope) => void;
    refetch: () => Promise<void>;
  }) {
    this.seq = seq;
    this.#apply = apply;
    this.#refetch = refetch;
  }

  /** What to send when the socket opens. */
  hello() {
    return { resume_from: this.seq };
  }

  /** Handle one message, whichever of the three shapes it is. */
  async receive(raw: string | Resume | Envelope): Promise<Verdict> {
    let message: Resume | Envelope;
    try {
      message = typeof raw === "string" ? JSON.parse(raw) : raw;
    } catch {
      // A message we cannot read is not a message we can skip past: advancing
      // over it would lose whatever it carried and say nothing.
      return "malformed";
    }
    if (!message || typeof message !== "object") return "malformed";

    if ((message as Resume).kind === "resync") {
      return this.#resync((message as { seq: number }).seq);
    }
    if ((message as Resume).kind === "replay") {
      return this.#replay(message as Extract<Resume, { kind: "replay" }>);
    }
    return this.#live(message as Envelope);
  }

  /** A fan-out event, which must be exactly the next one. */
  #live(event: Envelope): Promise<Verdict> | Verdict {
    if (typeof event.seq !== "number" || !Number.isFinite(event.seq)) {
      return "malformed";
    }
    if (event.seq <= this.seq) {
      // Already seen. Dropping it is the point of the counter; rewinding to it
      // would re-apply everything after it too.
      return "duplicate";
    }
    if (event.seq > this.seq + 1) {
      // **The gap is never applied.** Applying the event and carrying on is
      // what a board showing fiction looks like from the inside: every
      // individual update correct, the state between them missing.
      //
      // Resume from this event's own seq rather than the one before it. The
      // refetch reads Postgres, which already holds what this event announced,
      // so `- 1` would leave the counter one short and gap again on the very
      // next message, forever.
      return this.#resync(event.seq);
    }
    this.#apply(event);
    this.seq = event.seq;
    return "applied";
  }

  /**
   * A replay, verified against the counter it claims to continue.
   *
   * The channel builds this contiguously. Checking anyway costs one comparison
   * and turns a silent divergence into a resync.
   */
  #replay(message: Extract<Resume, { kind: "replay" }>): Promise<Verdict> | Verdict {
    const events = message.events ?? [];
    let expect = this.seq + 1;
    for (const event of events) {
      if (event.seq !== expect) return this.#resync(message.seq);
      expect += 1;
    }
    for (const event of events) this.#apply(event);
    if (events.length) this.seq = events[events.length - 1].seq;
    return "replayed";
  }

  /**
   * Refetch, and **advance only if the refetch succeeded**.
   *
   * A failed resync that moved the counter anyway is the worst outcome
   * available: the client skips the events it never fetched and believes it is
   * caught up. Holding the old seq means the next message gaps again and tries
   * again, which is the recoverable direction.
   */
  async #resync(_at: number): Promise<Verdict> {
    if (this.#resyncing) {
      // One refetch at a time. A second trigger is remembered rather than
      // dropped, so a burst of gaps ends with one fetch after the last of them
      // rather than none.
      this.#resyncAgain = true;
      return "resynced";
    }
    this.#resyncing = true;
    try {
      do {
        this.#resyncAgain = false;
        await this.#refetch();
      } while (this.#resyncAgain);
    } catch {
      return "resync-failed";
    } finally {
      this.#resyncing = false;
    }
    this.seq = _at;
    return "resynced";
  }
}
