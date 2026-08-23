#!/usr/bin/env node
// Fixed command-line bridge for encrypted document jobs.
//
// Large plaintext values travel through owner-only files. They never appear in
// argv, process titles, KV metadata, or error output.

import { readFileSync } from 'node:fs';
import {
  enqueueDocumentJob,
  claimDocumentJob,
  heartbeatDocumentJob,
  completeDocumentJob,
  failDocumentJob,
  retrieveDocumentResult,
  getDocumentStatus,
  deleteDocumentJob,
  publicErrorCode,
} from './kvjobs.mjs';

const [command, ...args] = process.argv.slice(2);
const env = process.env;

function readObject(path) {
  const value = JSON.parse(readFileSync(path, 'utf8'));
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('input_invalid');
  }
  return value;
}

function usage() {
  console.error(
    'usage: document-jobs.mjs enqueue <bundle.json> [job-id] | status <job-id> | ' +
    'claim [job-id] | heartbeat <job-id> <claim-token> | complete <job-id> ' +
    '<claim-token> <result.json> | fail <job-id> <claim-token> <error-code> | ' +
    'retrieve <job-id> | delete <job-id>'
  );
  process.exit(2);
}

async function main() {
  if (command === 'enqueue' && args[0]) {
    return enqueueDocumentJob(env, readObject(args[0]), args[1] ? { jobId: args[1] } : {});
  }
  if (command === 'status' && args.length === 1) {
    return getDocumentStatus(env, args[0]);
  }
  if (command === 'claim' && args.length <= 1) {
    return claimDocumentJob(env, args[0] ? { jobId: args[0] } : {});
  }
  if (command === 'heartbeat' && args.length === 2) {
    return heartbeatDocumentJob(env, args[0], args[1]);
  }
  if (command === 'complete' && args.length === 3) {
    return completeDocumentJob(env, args[0], args[1], readObject(args[2]));
  }
  if (command === 'fail' && args.length === 3) {
    return failDocumentJob(env, args[0], args[1], args[2]);
  }
  if (command === 'retrieve' && args.length === 1) {
    return retrieveDocumentResult(env, args[0]);
  }
  if (command === 'delete' && args.length === 1) {
    return deleteDocumentJob(env, args[0]);
  }
  usage();
}

try {
  const result = await main();
  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  const code = publicErrorCode(error);
  process.stderr.write(`${JSON.stringify({ ok: false, error: code })}\n`);
  process.exit(2);
}
