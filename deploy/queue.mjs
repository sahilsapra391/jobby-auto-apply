// Direct queue access using the blob token already on this machine, so local
// tooling never needs the board's password.
//
//   list <prefix>        what is waiting, oldest first, no side effects
//   claim-one <prefix>   take exactly the oldest request and delete only that one
//   claim <prefix>       take every request and delete all of them
//
// claim-one exists because `claim` once destroyed fourteen of the user's Apply
// clicks: run_apply read the queue, ran pending[0], then called claim, which
// deleted the whole queue including the thirteen requests it had never touched.
// A worker that processes one item must delete one item.
import { list, del, put } from '@vercel/blob';

const [cmd, prefix = 'marked/'] = process.argv.slice(2);
const { blobs } = await list({ prefix });
const sorted = (blobs || []).sort((a, b) => a.pathname.localeCompare(b.pathname));

const parse = (p) => {
  const base = p.slice(prefix.length).replace(/\.json$/, '');
  const sep = base.indexOf('__');
  return sep === -1 ? { applicationId: base }
                    : { markedAt: base.slice(0, sep), applicationId: base.slice(sep + 2) };
};

const parse2 = (p, pre) => {
  const base = p.slice(pre.length).replace(/\.json$/, '');
  const sep = base.indexOf('__');
  return sep === -1 ? { applicationId: base }
                    : { markedAt: base.slice(0, sep), applicationId: base.slice(sep + 2) };
};

if (cmd === 'enqueue') {
  // Put a request into a queue from this machine. Used to restore requests lost
  // to a bad claim, and to exercise the watcher end to end without the board.
  // Deduped by application id, matching the board's own endpoint.
  const id = process.argv[4];
  if (!id || !/^[A-Za-z0-9._-]+$/.test(id)) {
    console.error('usage: queue.mjs enqueue <prefix> <applicationId>');
    process.exit(2);
  }
  const dup = sorted.some((b) => parse(b.pathname).applicationId === id);
  if (dup) {
    console.log(JSON.stringify({ queued: id, deduped: true }));
  } else {
    const at = new Date().toISOString().replace(/[:.]/g, '-');
    await put(`${prefix}${at}__${id}.json`, JSON.stringify({ applicationId: id }),
      { access: 'private', contentType: 'application/json' });
    console.log(JSON.stringify({ queued: id }));
  }
} else if (cmd === 'poll') {
  // Every queue in one network call. The watcher ticks every couple of seconds
  // so that a click is acted on almost immediately; three separate list calls
  // per tick would triple that traffic for no benefit.
  const out = { marked: [], scan: [], apply: [] };
  const bucket = { 'marked/': 'marked', 'scan/': 'scan', 'apply/': 'apply' };
  const all = await list({});
  for (const b of (all.blobs || []).sort((a, b) => a.pathname.localeCompare(b.pathname))) {
    for (const [pre, key] of Object.entries(bucket)) {
      if (b.pathname.startsWith(pre)) out[key].push(parse2(b.pathname, pre));
    }
  }
  console.log(JSON.stringify(out));
} else if (cmd === 'list') {
  console.log(JSON.stringify(sorted.map((b) => parse(b.pathname))));
} else if (cmd === 'claim-one') {
  if (!sorted.length) {
    console.log(JSON.stringify([]));
  } else {
    const head = sorted[0];
    await del(head.url);
    console.log(JSON.stringify([parse(head.pathname)]));
  }
} else if (cmd === 'claim') {
  const out = sorted.map((b) => parse(b.pathname));
  if (sorted.length) await del(sorted.map((b) => b.url));
  console.log(JSON.stringify(out));
} else {
  console.error('usage: queue.mjs poll|list|enqueue|claim-one|claim [prefix] [id]');
  process.exit(2);
}
