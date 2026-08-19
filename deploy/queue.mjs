// Direct queue access from this machine, using the Cloudflare credentials in
// deploy/.env.local so local tooling never needs the board's password.
//
//   poll                 every queue in one read, no side effects
//   list <prefix>        what is waiting in one queue, oldest first
//   enqueue <prefix> <id>  add a request, deduped by application id
//   claim-one <prefix>   take exactly the oldest request
//   claim <prefix>       take every request
//
// The prefix argument (apply/, scan/, marked/) is kept from the Vercel Blob
// era so the Python callers did not have to change when the backend did.
import { readQueue, enqueue, claimOne, claimAll, QUEUES } from './kvqueue.mjs';

const [cmd, prefix = 'marked/', id] = process.argv.slice(2);
const queue = prefix.replace(/\/$/, '');
const env = process.env;

const die = (msg) => { console.error(msg); process.exit(2); };

if (cmd !== 'poll' && !QUEUES.includes(queue)) {
  die(`unknown queue "${queue}"; expected one of ${QUEUES.join(', ')}`);
}

try {
  if (cmd === 'poll') {
    console.log(JSON.stringify(await readQueue(env)));
  } else if (cmd === 'list') {
    console.log(JSON.stringify((await readQueue(env))[queue] || []));
  } else if (cmd === 'enqueue') {
    if (!id || !/^[A-Za-z0-9._-]+$/.test(id)) die('usage: queue.mjs enqueue <prefix> <applicationId>');
    console.log(JSON.stringify(await enqueue(env, queue, id)));
  } else if (cmd === 'claim-one') {
    console.log(JSON.stringify(await claimOne(env, queue)));
  } else if (cmd === 'claim') {
    console.log(JSON.stringify(await claimAll(env, queue)));
  } else {
    die('usage: queue.mjs poll|list|enqueue|claim-one|claim [prefix] [id]');
  }
} catch (err) {
  // Never exit 0 on failure: the watcher cannot tell a crashed read from an
  // empty queue any other way, and that confusion once hid a multi-hour outage.
  die(String(err.message || err));
}
