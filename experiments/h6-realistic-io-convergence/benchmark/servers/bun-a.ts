// H6 Workload A (runtime-dominated) — Bun. No I/O, no DB, no middleware.
const PORT = Number(process.env.PORT || 3100);

Bun.serve({
  port: PORT,
  fetch(req) {
    return new Response(JSON.stringify({ ok: true }), {
      headers: { "content-type": "application/json" },
    });
  },
});

console.log(`bun-a listening on ${PORT}`);
