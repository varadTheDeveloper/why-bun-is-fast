// H6 Workload B (realistic I/O) — Deno. Deno.serve(), `pg` via npm: specifier
// (same package/version as bun-b.ts and node-b.js).
import pkg from "npm:pg@8.13.1";
const { Pool } = pkg;

const PORT = Number(Deno.env.get("PORT") || 3301);

const pool = new Pool({
  host: Deno.env.get("H6_DB_HOST") || "127.0.0.1",
  port: Number(Deno.env.get("H6_DB_PORT") || 5432),
  user: Deno.env.get("H6_DB_USER") || "h6bench",
  password: Deno.env.get("H6_DB_PASSWORD") || "h6bench_pw",  // local benchmark-only DB; override via env var
  database: Deno.env.get("H6_DB_NAME") || "h6bench",
  max: 10,
  idleTimeoutMillis: 30000,
});

Deno.serve({ port: PORT }, async () => {
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
  return new Response(body, { headers: { "content-type": "application/json" } });
});

console.log(`deno-b listening on ${PORT}`);
