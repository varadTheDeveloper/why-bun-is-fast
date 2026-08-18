// H6 Workload A (runtime-dominated) — Node. Raw node:http, no framework.
import http from "node:http";

const PORT = Number(process.env.PORT || 3200);

const body = JSON.stringify({ ok: true });

const server = http.createServer((req, res) => {
  res.writeHead(200, { "content-type": "application/json" });
  res.end(body);
});

server.listen(PORT, () => console.log(`node-a listening on ${PORT}`));
