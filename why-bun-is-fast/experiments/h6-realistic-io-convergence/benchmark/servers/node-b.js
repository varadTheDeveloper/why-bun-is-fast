// H6 Workload B (realistic I/O) — Node. Raw node:http, `pg` driver (same
// package/version as bun-b.ts and deno-b.ts).
import http from "node:http";
import pg from "pg";
const { Pool } = pg;

const PORT = Number(process.env.PORT || 3201);

const pool = new Pool({
  host: process.env.H6_DB_HOST || "127.0.0.1",
  port: Number(process.env.H6_DB_PORT || 5432),
  user: process.env.H6_DB_USER || "h6bench",
  password: process.env.H6_DB_PASSWORD || "h6bench_pw",  // local benchmark-only DB; override via env var
  database: process.env.H6_DB_NAME || "h6bench",
  max: 10,
  idleTimeoutMillis: 30000,
});

const server = http.createServer(async (req, res) => {
  const r = await pool.query(
    "SELECT id, name, email, balance_cents FROM accounts WHERE id = $1",
    [42],
  );
  const row = r.rows[0];
  const body = JSON.stringify({
    id: row.id,
    name: row.name,
    email: row.email,
    balance_cents: Number(row.balance_cents),
  });
  res.writeHead(200, { "content-type": "application/json" });
  res.end(body);
});

server.listen(PORT, () => console.log(`node-b listening on ${PORT}`));
