// Quick pre-run verification: does `await Promise.resolve()` (microtask-only
// suspension) actually leave the response Promise Pending by the time Bun's
// on_response() checks it, or does Bun's eager drain_microtasks() resolve it
// first (meaning it takes the SAME synchronous render path as a fully sync
// handler and never reaches to_async())?
//
// We can't rely on Bun's internal ctx_log!("toAsync") debug logging (it
// dead-strips in release builds, and we're testing the installed release
// binary, not a from-source debug build). Instead we use an external
// behavioral signal: uWS's per-connection Request struct is stack-allocated
// and reused across requests on the SAME connection. If to_async() truly
// copies the URL, then two DIFFERENT concurrent requests on the same
// keep-alive connection (one slow-suspended, one fast) should never see
// cross-talk in their own request's URL, regardless of suspension type -
// this alone doesn't discriminate. So this script instead just reports
// latency-floor behavior for three variants, as a corroborating signal
// alongside the source-level trace (the primary evidence).
const PORT = 3900;
Bun.serve({
  port: PORT,
  async fetch(req) {
    const u = new URL(req.url);
    const variant = u.searchParams.get("v");
    if (variant === "sync") {
      return new Response("ok");
    } else if (variant === "microtask") {
      await Promise.resolve();
      return new Response("ok");
    } else if (variant === "macrotask") {
      await new Promise((resolve) => setImmediate(resolve));
      return new Response("ok");
    }
    return new Response("bad variant", { status: 400 });
  },
});
console.log(`verify server on ${PORT}`);
