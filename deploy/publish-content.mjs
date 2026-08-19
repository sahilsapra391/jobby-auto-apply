// Push the board page and the application documents into KV.
//
// This is what "publishing" means now. It used to mean redeploying the whole
// site, which is why Vercel's hundred-deployments-a-day ran out in an
// afternoon, and why a build from GitHub had nothing to serve: the board was
// generated into an untracked module that only ever existed on this machine.
//
//   node publish-content.mjs <board.html> <documents.json>
//
// documents.json is {name: base64}. Only documents whose contents changed are
// written, because KV allows a thousand writes a day and republishing forty
// unchanged PDFs on every board refresh would spend that by lunchtime.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { putBoard, putDoc, docKey } from './kvcontent.mjs';
import { kvGet } from './kv.mjs';

const [htmlPath, docsPath] = process.argv.slice(2);
if (!htmlPath) {
  console.error('usage: publish-content.mjs <board.html> [documents.json]');
  process.exit(2);
}

const env = process.env;
const html = readFileSync(htmlPath, 'utf8');
const generatedAt = new Date().toISOString();

await putBoard(env, html, generatedAt);

const out = { board: { bytes: html.length, generatedAt }, documents: { written: 0, skipped: 0 } };

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
