// What a Profile-page edit has to look like before it reaches the queue.
//
// **One validator, because there are two boards and one device.** The Python
// board posts these to `api/board.js` and v2 posts them to its own route, and
// both land in the same `answerEdits` / `resumeEdits` arrays that `watch.py`
// claims. A second hand-written copy of these rules is the one that drifts,
// and drift here is an edit the device refuses for a reason the board that
// sent it does not know about.
//
// It plans rather than responds: no `Response`, no status codes beyond the one
// each refusal names, so the same function serves a Vercel handler and a Next
// route without either of them owning the rules.
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
