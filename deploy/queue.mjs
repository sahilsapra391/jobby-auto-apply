// Direct queue access using the blob token already on this machine, so local
// tooling never needs the board's password. list | claim <prefix>
import { list, del } from '@vercel/blob';

const [cmd, prefix = 'marked/'] = process.argv.slice(2);
const { blobs } = await list({ prefix });
const sorted = (blobs || []).sort((a, b) => a.pathname.localeCompare(b.pathname));

const parse = (p) => {
  const base = p.slice(prefix.length).replace(/\.json$/, '');
  const sep = base.indexOf('__');
  return sep === -1 ? { applicationId: base }
                    : { markedAt: base.slice(0, sep), applicationId: base.slice(sep + 2) };
};

if (cmd === 'list') {
  console.log(JSON.stringify(sorted.map((b) => parse(b.pathname))));
} else if (cmd === 'claim') {
  const out = sorted.map((b) => parse(b.pathname));
  if (sorted.length) await del(sorted.map((b) => b.url));
  console.log(JSON.stringify(out));
} else {
  console.error('usage: queue.mjs list|claim [prefix]');
  process.exit(2);
}
