// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.
//
// Copied from deploy/device_keys.mjs. Change that file.
//
// Verbatim. The device writes these keys and v2 reads them, so the
// names are written once.
// The KV keys the device writes for a board to read, and the rule for when a
// status it wrote is too old to believe.
//
// **One file, because there are two readers and one writer.** The device
// writes these through `local-status.mjs` and `owner-wording.mjs`; the retired
// Python board read them through `kvcontent.mjs`, and v2 reads them through a
// generated copy of this file (`apps/web/app/_ui/deviceKeys.generated.mjs`).
// A key spelled twice is a key that drifts, and a drifted key reads as "never
// written", which is the one answer that looks like a quiet device rather
// than a broken board.
//
// No imports, so the generator copies it verbatim.

/** Provider connection status, as `provider_status.public_payload` shapes it. */
export const LOCAL_STATUS_KEY = 'jobby:worker:local:status';

/**
 * The resume wording and its rendered preview, for the owner-only editor.
 *
 * **Not on any public surface, and the key name says whose it is.** It is the
 * person's employment history in their own words. `class_b._PROFILE_EXCLUDED`
 * keeps it out of Postgres, and v2's public pages read Postgres; this key is
 * read by exactly one route, `/api/profile/wording`, and that route answers
 * only a signed-in owner. The device is its only writer and the file on the
 * device always wins: this is a projection of `resume-content.json`, stamped
 * with the digest it was derived from, never a second place the wording lives.
 */
export const OWNER_WORDING_KEY = 'jobby:owner:resume-wording';

/**
 * The one shape that may land under `OWNER_WORDING_KEY`, whatever a caller
 * hands `owner-wording.mjs`. Throws naming the field that is wrong.
 */
export function shapeOwnerWording(input, now = new Date()) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('payload must be an object');
  }
  if (!input.content || typeof input.content !== 'object' || Array.isArray(input.content)) {
    throw new Error('payload.content must be an object');
  }
  if (typeof input.digest !== 'string' || !/^[0-9a-f]{64}$/.test(input.digest)) {
    throw new Error('payload.digest must be a sha256 hex digest');
  }
  return {
    schemaVersion: 1,
    publishedAt: now.toISOString(),
    digest: input.digest,
    content: input.content,
    render: typeof input.render === 'string' ? input.render : '',
  };
}

/**
 * An uploaded base resume waits under this prefix plus its upload id.
 *
 * `queue.mjs upload-take` reads and deletes it; a board writes it. Its own key
 * rather than the queue, because `jobby:queue` is read whole on every poll.
 */
export const UPLOAD_KEY_PREFIX = 'jobby:upload:';

/**
 * How old a published provider status may be before a board stops believing
 * it. Fifteen minutes, the retired board's `WORKER_STALE_MS`: the watcher's
 * heartbeat republishes well inside that, so a status this old means the
 * device stopped talking, not that nothing changed.
 */
export const STATUS_STALE_MS = 15 * 60 * 1000;

/** The status, or null when it is missing, unreadable or stale. */
export function freshStatus(value, now = Date.now()) {
  if (!value || typeof value !== 'object') return null;
  const checked = Date.parse(value.checkedAt || '');
  if (!Number.isFinite(checked) || now - checked > STATUS_STALE_MS) return null;
  return value;
}
