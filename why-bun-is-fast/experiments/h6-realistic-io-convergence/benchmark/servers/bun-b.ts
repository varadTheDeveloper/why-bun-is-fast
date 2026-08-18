// H6 Workload B (realistic I/O) — Bun. Fixed parameterized query against
// the same accounts table/row (id=42) used identically by node-b/deno-b.
// Uses the `pg` npm package (not Bun's native Bun.sql) deliberately, so the
// database-driver layer is held identical across all three runtimes and the
// experiment isolates the HTTP/runtime path, not driver-implementation
// differences. This is a documented deviation from "use each runtime's own
// native client" toward "remove an entire confound axis" — see this
// experiment's results README for the rationale.
import pg from "pg";
const { Pool } = pg;

const PORT = Number(process.env.PORT || 3101);

const pool = new Pool({
  host: process.env.H6_DB_HOST || "127.0.0.1",
  port: Number(process.env.H6_DB_PORT || 5432),
  user: process.env.H6_DB_USER || "h6bench",
  password: process.env.H6_DB_PASSWORD || "h6bench_pw",  // local benchmark-only DB; override via env var
  database: process.env.H6_DB_NAME || "h6bench",
  max: 10,
  idleTimeoutMillis: 30000,
});

Bun.serve({
  port: PORT,
  async fetch(req) {
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
    return new Response(body, {
      headers: { "content-type": "application/json" },
    });
  },
});

console.log(`bun-b listening on ${PORT}`);
