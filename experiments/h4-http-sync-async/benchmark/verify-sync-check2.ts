const PORT = 3901;
Bun.serve({
  port: PORT,
  async fetch(req) {
    const u = new URL(req.url);
    const variant = u.searchParams.get("v");
    if (variant === "sync") return new Response("ok");
    if (variant === "settimeout0") { await new Promise((r) => setTimeout(r, 0)); return new Response("ok"); }
    if (variant === "setimmediate") { await new Promise((r) => setImmediate(r)); return new Response("ok"); }
    if (variant === "nexttick") { await new Promise((r) => process.nextTick(r)); return new Response("ok"); }
    return new Response("bad", { status: 400 });
  },
});
console.log(`server on ${PORT}`);
