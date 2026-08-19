// Runs api/board.js locally, so the gate and the rendered board can be checked
// before anything is deployed. Not uploaded (see .vercelignore).
//
//   DASHBOARD_PASSWORD=... node dev-server.mjs [port]

import { createServer } from 'node:http';
import handler from './api/board.js';

const port = Number(process.argv[2] || 3000);

createServer(async (req, res) => {
  const request = new Request(new URL(req.url, `http://${req.headers.host}`), {
    method: req.method,
    headers: req.headers,
  });
  const response = await handler.fetch(request);
  res.writeHead(response.status, Object.fromEntries(response.headers));
  res.end(Buffer.from(await response.arrayBuffer()));
}).listen(port, () => console.log(`board on http://localhost:${port}`));
