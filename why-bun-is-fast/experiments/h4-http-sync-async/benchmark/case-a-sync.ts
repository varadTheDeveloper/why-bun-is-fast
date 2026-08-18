// H4 Case A — synchronous handler. Smallest possible: no I/O, no suspension.
const PORT = Number(process.env.PORT || 4100);

Bun.serve({
  port: PORT,
  fetch(req) {
    return new Response("ok");
  },
});

console.log(`case-a (sync) listening on ${PORT}`);
