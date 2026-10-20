// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.
//
// Copied from deploy/profile_edits.mjs. Change that file.
//
// Verbatim. The board writes the queue and the device claims it,
// so the rules an edit has to satisfy are written once.
// What a Profile-page edit has to look like before it reaches the queue.
//
// **One validator, written once.** v2 posts these to its own route, and they
// land in the `answerEdits` / `resumeEdits` arrays that `watch.py` claims. A
// second hand-written copy of these rules is the one that drifts, and drift
// here is an edit the device refuses for a reason the board that sent it does
// not know about.
//
// It plans rather than responds: no `Response`, no status codes beyond the one
// each refusal names, so the route that calls it does not own the rules.
//
// Everything is bounded because every one of these rides the single KV key the
// watcher polls every few seconds, and an unbounded field there is a way to
// make the whole board slow for everyone.

export const LIMITS = {
  editId: 120, key: 120, question: 600, value: 4000, state: 20,
};

/** The largest resume wording the queue will carry, in bytes of JSON. */
export const MAX_CONTENT_BYTES = 64 * 1024;

export function boundedString(value, limit) {
  if (typeof value !== 'string') return null;
  return value.length <= limit ? value : null;
}

// `dismiss` and `restore` carry the key and the version, the same shape as
// `delete`, and are refusing a question rather than removing an answer.
export const ANSWER_ACTIONS = new Set(['save', 'delete', 'dismiss', 'restore']);
const ANSWER_KEYED = new Set(['delete', 'dismiss', 'restore']);
export const RESUME_ACTIONS = new Set(['default', 'content', 'remove', 'add', 'update']);

const ID = /^[A-Za-z0-9._-]+$/;
const bad = (error, status = 400) => ({ error, status });

/**
 * The queue entry one answer edit becomes, or a refusal naming what is wrong.
 *
 * The version travels with the edit and is what makes a replay free and a
 * collision loud: `store.cmd_answer_edit` applies it when the version matches,
 * reports `already applied` for the same `editId` twice, and refuses with
 * `conflict` when something else moved first.
 */
export function planAnswerEdit(body) {
  body = body || {};
  const editId = boundedString(body.editId, LIMITS.editId);
  if (!editId || !ID.test(editId)) return bad('bad editId');
  const action = String(body.action || 'save');
  if (!ANSWER_ACTIONS.has(action)) return bad('unknown action');
  const version = Number.isInteger(body.version) && body.version >= 0 ? body.version : 0;
  const extra = { action, version };
  const key = boundedString(body.key, LIMITS.key);
  if (key !== null && key !== '') extra.key = key;
  if (action === 'save') {
    const question = boundedString(body.question, LIMITS.question);
    const value = boundedString(body.value, LIMITS.value);
    if (!question || !question.trim()) return bad('the question is required');
    if (value === null) return bad('that answer is too long');
    extra.question = question;
    extra.value = value;
    const state = boundedString(body.state, LIMITS.state);
    if (state) extra.state = state;
    if (Array.isArray(body.tags)) {
      extra.tags = body.tags.filter((t) => typeof t === 'string').slice(0, 20)
        .map((t) => t.slice(0, 60));
    }
  } else if (ANSWER_KEYED.has(action) && !extra.key) {
    return bad(`${action} needs the key`);
  }
  return { queue: 'answerEdits', editId, extra };
}

/**
 * The queue entry one base-resume or wording edit becomes, or a refusal.
 *
 * `content` is the wording and is checked here only for size. The slot counts
 * it has to match belong to the template, which lives on the device, and
 * `resume_build.render` is the check that holds it; validating a shape here
 * would be a second declaration of the template kept in step by hand.
 */
export function planResumeEdit(body) {
  body = body || {};
  const editId = boundedString(body.editId, LIMITS.editId);
  if (!editId || !ID.test(editId)) return bad('bad editId');
  const action = String(body.action || '');
  if (!RESUME_ACTIONS.has(action)) return bad('unknown action');
  const extra = { action };
  if (action === 'add' || action === 'update') {
    const key = boundedString(body.key, LIMITS.key);
    if (!key || !ID.test(key)) return bad('bad base resume key');
    extra.key = key;
    const label = boundedString(body.label, LIMITS.key);
    if (label) extra.label = label;
    // Two-letter codes only. The store validates again; this keeps anything
    // unbounded out of the queue every poll has to read.
    const countries = Array.isArray(body.countries) ? body.countries : [];
    const codes = countries.filter((c) => typeof c === 'string' && /^[A-Za-z]{2}$/.test(c))
      .slice(0, 64).map((c) => c.toUpperCase());
    if (action === 'add' && !codes.length) {
      return bad('a new base resume needs at least one country');
    }
    if (codes.length) extra.countries = codes;
    const copyFrom = boundedString(body.copyFrom, LIMITS.key);
    if (copyFrom) extra.copyFrom = copyFrom;
    // The name of the key holding an uploaded document, never the document.
    const uploadId = boundedString(body.uploadId, LIMITS.editId);
    if (uploadId && ID.test(uploadId)) extra.uploadId = uploadId;
    if (Number.isInteger(body.priority)) extra.priority = body.priority;
  } else if (action === 'default' || action === 'remove') {
    const key = boundedString(body.key, LIMITS.key);
    if (!key || !ID.test(key)) return bad('bad base resume key');
    extra.key = key;
  } else {
    const content = body.content;
    if (!content || typeof content !== 'object' || Array.isArray(content)) {
      return bad('resume content must be an object');
    }
    if (JSON.stringify(content).length > MAX_CONTENT_BYTES) {
      return bad('that resume content is too large');
    }
    extra.content = content;
  }
  return { queue: 'resumeEdits', editId, extra };
}

// ------------------------------------------------------------ the upload

/**
 * Two megabytes, the same ceiling `watch.MAX_UPLOAD_BYTES` enforces when it
 * takes the document. Not the 12 MB `document_contract` accepts: that is for
 * anything a document job might carry, and a resume that does not fit in 2 MB
 * is a scan of a resume rather than a resume.
 */
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;
export const PDF_MAGIC = '%PDF-';

/**
 * An uploaded base resume, checked before it is parked for the device.
 *
 * Returns `{ uploadId, encoded, bytes }` or a refusal naming what is wrong.
 * The caller writes `encoded` to `UPLOAD_KEY_PREFIX + uploadId` in
 * `device_keys.mjs` and nothing else; the resume edit that names the upload
 * goes through `planResumeEdit` as `uploadId` on an `add`.
 *
 * The device checks size and magic again when it takes the key
 * (`watch.install_uploaded_resume`): a board that let a bad file through still
 * cannot install one.
 *
 * **Checked by content, never by the name the browser reported.** A file
 * called resume.pdf that is not a PDF would otherwise fail on the device, in
 * the middle of building a real application.
 */
export function planResumeUpload(body) {
  body = body || {};
  const uploadId = boundedString(body.uploadId, LIMITS.editId);
  if (!uploadId || !ID.test(uploadId)) return bad('bad uploadId');
  const encoded = typeof body.pdf === 'string' ? body.pdf : '';
  if (!encoded) return bad('no file');
  // Strict base64. `Buffer.from` silently skips characters it does not know,
  // so a body that is not base64 at all would decode to a few stray bytes and
  // be refused for the wrong reason; the device's own decode is `validate=True`.
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(encoded) || encoded.length % 4 !== 0) {
    return bad('that file could not be read');
  }
  // Bounded before decoding, so an enormous body costs nothing to refuse.
  if (encoded.length > Math.ceil(MAX_UPLOAD_BYTES / 3) * 4) {
    return bad('that file is over the 2 MB limit', 413);
  }
  const bytes = Buffer.from(encoded, 'base64');
  if (!bytes.length) return bad('that file is empty');
  // Decoded length, not the base64 length: the cap is about the document.
  if (bytes.length > MAX_UPLOAD_BYTES) {
    return bad(`that file is ${Math.round(bytes.length / 1024)} KB; the limit is 2 MB`, 413);
  }
  if (bytes.subarray(0, PDF_MAGIC.length).toString('latin1') !== PDF_MAGIC) {
    return bad('that file is not a PDF', 415);
  }
  return { uploadId, encoded, bytes: bytes.length };
}
