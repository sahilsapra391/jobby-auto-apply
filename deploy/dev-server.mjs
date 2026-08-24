// Runs the API handler plus a locally rendered page, so the gate and board can
// be checked before anything is deployed. Not uploaded (see .vercelignore).
//
//   DASHBOARD_PASSWORD=... node dev-server.mjs [port]

import { createServer } from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import handler from './api/board.js';

const port = Number(process.argv[2] || 3000);
const pageFile = process.env.JOBBY_LOCAL_BOARD_FILE;

function authorized(req) {
  const header = req.headers.authorization || '';
  const [scheme, encoded] = header.split(' ');
  if (!encoded || scheme.toLowerCase() !== 'basic') return false;
  try {
    const decoded = Buffer.from(encoded, 'base64').toString('utf8');
    const supplied = decoded.includes(':') ? decoded.slice(decoded.indexOf(':') + 1) : '';
    const digest = (value) => createHash('sha256').update(value).digest();
    return timingSafeEqual(digest(supplied), digest(process.env.DASHBOARD_PASSWORD || ''));
  } catch {
    return false;
  }
}

function servePage(req, res) {
  if (!authorized(req)) {
    res.writeHead(401, {
      'content-type': 'text/plain; charset=utf-8',
      'www-authenticate': 'Basic realm="Application board", charset="UTF-8"',
    });
    res.end('Authentication required.\n');
    return;
  }
  const page = readFileSync(pageFile);
  res.writeHead(200, {
    'content-type': 'text/html; charset=utf-8',
    'cache-control': 'no-store, max-age=0',
    'content-length': String(page.byteLength),
  });
  res.end(req.method === 'HEAD' ? undefined : page);
}

createServer(async (req, res) => {
  const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
  if (pageFile && (pathname === '/' || pathname === '/settings')
      && (req.method === 'GET' || req.method === 'HEAD')) {
    servePage(req, res);
    return;
  }
  // Forward the body, or POST handlers see an empty request and every local
  // test of them passes for the wrong reason.
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(new URL(req.url, `http://${req.headers.host}`), {
    method: req.method,
    headers: req.headers,
    ...(body && req.method !== 'GET' && req.method !== 'HEAD' ? { body } : {}),
  });
  const response = await handler.fetch(request);
  res.writeHead(response.status, Object.fromEntries(response.headers));
  res.end(Buffer.from(await response.arrayBuffer()));
}).listen(port, () => console.log(`board on http://localhost:${port}`));
