// H4 Case B — async-suspending handler.
//
// IMPORTANT — this does NOT use `await Promise.resolve()` as originally
// sketched in Stage 11/12. Pre-run source verification (see
// results/README.md "Source-path verification") found that Bun's
// `RequestContext::on_response()` calls `ctx.drain_microtasks()` BEFORE
// checking whether the returned Promise is still pending. A microtask-only
// continuation (`Promise.resolve()`, `process.nextTick()`) is fully resolved
// by that eager drain, so `on_response()` sees an already-Fulfilled promise
// and takes the exact same synchronous `protect_for_body_and_render()` path
// as a directly-returned Response — `to_async()` (the function that performs
// M16's headers+URL copy) is NEVER CALLED. An empirical check confirmed
// this: `await Promise.resolve()` and a fully sync handler were
// statistically indistinguishable in throughput (~17.2k vs ~17.4k req/s at
// concurrency=1across 3 trials), while `await new Promise(r => setImmediate(r))`
// showed a real, repeatable ~11% reduction.
//
// `setTimeout(fn, 0)` was also tested and rejected: it collapsed throughput
// from ~17.2k to ~736 req/s (about a ~1ms artificial floor from timer
// clamping) — an overhead ~15-20x larger than what genuine suspension costs,
// which would swamp M16's real signal with an unrelated timer-scheduling
// confound. `setImmediate` is the minimal-overhead primitive that reliably
// forces a real event-loop tick (and therefore a genuinely-Pending Promise
// at the point `on_response()` checks) without introducing that confound.
//
// This is a documented, source-verified deviation from the original sketch,
// not an arbitrary change — see results/README.md for the full trace.
const PORT = Number(process.env.PORT || 4101);

Bun.serve({
  port: PORT,
  async fetch(req) {
    await new Promise((resolve) => setImmediate(resolve));
    return new Response("ok");
  },
});

console.log(`case-b (async) listening on ${PORT}`);
