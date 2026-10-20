#!/usr/bin/env node
// Publish the resume wording for the owner-only editor, from this machine.
//
//   write <payload.json>   put it under OWNER_WORDING_KEY, unless the copy
//                          already there carries the same digest
//   digest                 print the digest of the copy in KV, or null
//
// `scripts/wording_publish.py` builds the payload and calls this. The payload
// is `{schemaVersion, publishedAt, digest, content, render}`; nothing else is
// accepted, so a caller cannot widen what lands under the key.
//
// One read before the write, so an unchanged wording costs no KV write: the
// free tier allows 1,000 writes a day and the queue already spends most of
// them.

import { readFileSync } from 'node:fs';
import { kvGet, kvPut } from './kv.mjs';
import { OWNER_WORDING_KEY, shapeOwnerWording } from './device_keys.mjs';

const [command, path] = process.argv.slice(2);
const MAX_BYTES = 512 * 1024;

function die(message, code = 2) {
  console.error(message);
  process.exit(code);
}

async function held() {
  const raw = await kvGet(process.env, OWNER_WORDING_KEY);
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    // A copy that does not parse is replaced, not trusted.
    return {};
  }
}

if (command === 'digest') {
  const current = await held();
  console.log(JSON.stringify({ digest: current && current.digest ? current.digest : null }));
} else if (command === 'write' && path) {
  let payload;
  try {
    payload = shapeOwnerWording(JSON.parse(readFileSync(path, 'utf8')));
  } catch (err) {
    die(`owner-wording: ${err.message}`);
  }
  const body = JSON.stringify(payload);
  if (body.length > MAX_BYTES) die(`owner-wording: payload is ${body.length} bytes; the cap is ${MAX_BYTES}`);
  const current = await held();
  if (current && current.digest === payload.digest) {
    console.log(JSON.stringify({ state: 'unchanged', digest: payload.digest }));
  } else {
    await kvPut(process.env, OWNER_WORDING_KEY, body);
    console.log(JSON.stringify({ state: 'published', digest: payload.digest, bytes: body.length }));
  }
} else {
  die('usage: owner-wording.mjs write <payload.json> | digest');
}
