// H6 Workload A (runtime-dominated) — Deno. Deno.serve(), no I/O.
const PORT = Number(Deno.env.get("PORT") || 3300);

const body = JSON.stringify({ ok: true });

Deno.serve({ port: PORT }, () => {
  return new Response(body, { headers: { "content-type": "application/json" } });
});

console.log(`deno-a listening on ${PORT}`);
