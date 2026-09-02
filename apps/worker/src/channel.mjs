/**
 * One account's event channel: seq, the ring, fan-out, replay or resync.
 *
 * **Plain JavaScript with no Workers API in it**, so the part that has to be
 * correct is tested against the real thing under `node --test` rather than
 * against a mock of a Durable Object. `TenantChannel` is a shell around this.
 *
 * It does not write to Postgres and cannot: the seam is one-directional.
 * Vercel writes the row, commits, then posts the event here. A channel that
 * could also write makes "which one is right" a question with no answer.
 */

/**
 * How many events the ring holds.
 *
 * **Resync is the designed path, not the failure path.** An account generating
 * more than this while a tab is closed gets an explicit resync, which is
 * correct and cheap. Do not grow the ring to avoid resyncs; grow it only if
 * profiling says the resyncs themselves cost something.
 */
export const RING = 500;

/**
 * The kinds this channel will carry, and nothing else.
 *
 * **One kind per fact.** `answer_record.py` records five distinct claims about
 * a question and refuses to let any two share a field, because blending two
 * costs the ability to tell which you are looking at. A `phase` and a
 * `completed` are different events even when they arrive together.
 *
 * `posting.decided` is separate from `application.changed` on purpose: a
 * posting entering the Found exclusion set is a `job_decisions` insert with no
 * application behind it yet. It is not an `application.changed` with a `from`.
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

export class Channel {
  constructor({ accountId = null, seq = 0, ring = [] } = {}) {
    this.accountId = accountId;
    this.seq = seq;
    this.ring = ring;
    this.subscribers = new Set();
  }

  /** Rebuild from what hibernation kept. */
  static restore(saved) {
    return new Channel(saved);
  }

  /** What has to survive hibernation: the counter and the ring, not sockets. */
  snapshot() {
    return { accountId: this.accountId, seq: this.seq, ring: this.ring };
  }

  subscribe(socket) {
    this.subscribers.add(socket);
    return () => this.subscribers.delete(socket);
  }

  /**
   * Assign the next seq, ring it, fan it out. Returns the envelope.
   *
   * **An event carries ids and a diff, never a rendered row.** A rendered row
   * is a snapshot again in smaller pieces, and it puts presentation on the
   * write path.
   */
  publish({ kind, payload = {} } = {}) {
    if (!KINDS.has(kind)) {
      throw new Error(
        `unknown event kind ${kind}; the kinds are ${[...KINDS].join(", ")}`);
    }
    if (payload && "from" in payload) {
      // Found is the absence of a decision row, so nothing transitions *out*
      // of it in a field. An earlier draft of the design showed
      // {from: "found", to: "dropped"} and contradicted its own lane section.
      throw new Error(
        "an event carries no `from`: Found is an absence, and a posting " +
        "entering the exclusion set is posting.decided");
    }
    this.seq += 1;
    const envelope = {
      seq: this.seq,
      account_id: this.accountId,
      kind,
      at: new Date().toISOString(),
      payload,
    };
    this.ring.push(envelope);
    if (this.ring.length > RING) this.ring.splice(0, this.ring.length - RING);
    this.#fanOut(envelope);
    return envelope;
  }

  /**
   * What a client that last saw `from` should be given.
   *
   * **Never a silent gap.** Either every event after `from`, contiguous, or an
   * explicit resync telling the client to refetch. A client that misses events
   * and does not know it is a client showing fiction with full confidence.
   */
  resume(from) {
    const after = Number(from);
    if (!Number.isFinite(after) || after < 0 || after > this.seq) {
      // Ahead of the channel, or nonsense. Either way its state cannot be
      // trusted and there is nothing honest to replay.
      return { kind: "resync", seq: this.seq };
    }
    const oldest = this.ring.length ? this.ring[0].seq : this.seq + 1;
    if (after + 1 < oldest) {
      return { kind: "resync", seq: this.seq };
    }
    return {
      kind: "replay",
      seq: this.seq,
      events: this.ring.filter((e) => e.seq > after),
    };
  }

  /**
   * Send to every subscriber, dropping the ones that fail.
   *
   * A broken socket must not cost every other tab its events, and it must not
   * stay in the set: an unbounded set of dead sockets is the same leak as an
   * unbounded ring.
   */
  #fanOut(envelope) {
    const message = JSON.stringify(envelope);
    for (const socket of [...this.subscribers]) {
      try {
        socket.send(message);
      } catch {
        this.subscribers.delete(socket);
      }
    }
  }
}
