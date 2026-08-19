// Runs api/board.js locally, so the gate and the rendered board can be checked
// before anything is deployed. Not uploaded (see .vercelignore).
//
//   DASHBOARD_PASSWORD=... node dev-server.mjs [port]

import { createServer } from 'node:http';
import handler from './api/board.js';

const port = Number(process.argv[2] || 3000);

createServer(async (req, res) => {
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
