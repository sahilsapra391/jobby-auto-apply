// Push sanitized board state and application documents into KV.
//
// This is what "publishing" means now. It used to mean redeploying the whole
// site, which is why Vercel's hundred-deployments-a-day ran out in an
// afternoon, and why a build from GitHub had nothing to serve: the board was
// generated into an untracked module that only ever existed on this machine.
//
//   node publish-content.mjs <board.html> <documents.json> <providers.json> \
//        <board-state.json> <renderer-fingerprint> <doc-index.json>
//
// documents.json is {name: base64}. Only documents whose contents changed are
// written, because KV allows a thousand writes a day and republishing forty
// unchanged PDFs on every board refresh would spend that by lunchtime. Once R2
// holds the artifacts, publish.py stops sending documents here at all and sends
// a doc-index instead: filename -> R2 key, which is what lets /doc/<filename>
// links that are already published keep working against a layout keyed by
// application id. The KV copies stay readable until the retirement commit.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { putBoard, putBoardState, putDoc, putDocIndex, docKey, DOC_INDEX_KEY } from './kvcontent.mjs';
import { kvGet } from './kv.mjs';

const [htmlPath, docsPath, providersPath, statePath] = process.argv.slice(2);
if (!htmlPath || !statePath) {
  console.error('usage: publish-content.mjs <board.html> [documents.json] [providers.json] <board-state.json>');
  process.exit(2);
}

const env = process.env;
const html = readFileSync(htmlPath, 'utf8');
const state = readFileSync(statePath, 'utf8');
const generatedAt = new Date().toISOString();
const providers = providersPath ? JSON.parse(readFileSync(providersPath, 'utf8')) : null;

await putBoardState(env, state, generatedAt, process.argv[6] || '');

// Rolling migration only. A deployed renderer no longer reads this value, but
// the current production deployment still does until the PR is merged. Preview
// publication can set JOBBY_SKIP_LEGACY_HTML=1 and leave production untouched.
if (env.JOBBY_SKIP_LEGACY_HTML !== '1') {
  await putBoard(env, html, generatedAt, providers);
}

const storedState = await import('node:zlib').then(({ gzipSync }) =>
  gzipSync(Buffer.from(state, 'utf8')).byteLength);
const out = {
  state: { bytes: storedState, generatedAt },
  legacyBoard: env.JOBBY_SKIP_LEGACY_HTML === '1' ? null : { bytes: html.length, generatedAt },
  documents: { written: 0, skipped: 0 },
  docIndex: { entries: 0, written: false },
};

// The filename index for artifacts served out of R2. Written only when it
// changes, for the same reason the documents are: a thousand KV writes a day
// is not many when the board republishes dozens of times.
const indexPath = process.argv[7];
if (indexPath) {
  const docs = JSON.parse(readFileSync(indexPath, 'utf8'));
  out.docIndex.entries = Object.keys(docs).length;
  const existing = JSON.parse((await kvGet(env, DOC_INDEX_KEY)) || 'null');
  if (!existing || JSON.stringify(existing.docs) !== JSON.stringify(docs)) {
    await putDocIndex(env, docs, generatedAt);
    out.docIndex.written = true;
  }
}

if (docsPath) {
  const docs = JSON.parse(readFileSync(docsPath, 'utf8'));
  // A digest of what is already stored, so an unchanged PDF costs one read
  // instead of one write. Reads are a hundred times cheaper here.
  const stamps = JSON.parse((await kvGet(env, 'jobby:doc-stamps')) || '{}');
  const next = {};
  for (const [name, b64] of Object.entries(docs)) {
    const digest = createHash('sha256').update(b64).digest('hex').slice(0, 16);
    next[name] = digest;
    if (stamps[name] === digest && (await kvGet(env, docKey(name))) !== null) {
      out.documents.skipped += 1;
      continue;
    }
    await putDoc(env, name, b64);
    out.documents.written += 1;
  }
  if (JSON.stringify(stamps) !== JSON.stringify(next)) {
    const { kvPut } = await import('./kv.mjs');
    await kvPut(env, 'jobby:doc-stamps', JSON.stringify(next));
  }
}

console.log(JSON.stringify(out));
