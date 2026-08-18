# H4 — `Bun.serve()` sync vs. async handler

**Status: protocol only. Not yet executed. Classification: MUST RUN.**

## Purpose

Test whether the source-verified sync/async header+URL copy in `Bun.serve()`'s request path (M16) is measurable in aggregate throughput/latency, or whether it's real-but-below-the-noise-floor at realistic request rates.

## Hypothesis (H4, from Stage 11 — unmodified)

Bun's sync HTTP handler outperforms an equivalent async handler at high request rates.

## Mechanism

M16 — on sync→async handler suspension, `RequestContext.rs`'s `to_async()`/`to_async_without_abort_handler()` copies headers (guarded, at most once) and URL, because the underlying `uWS::Request` is a stack-allocated, per-connection-reused C++ struct.

## Runtime versions / commits

- **Track used: primarily source-controlled**, since this tests an exact, line-traced code path. Bun: `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4`. A release-build cross-check is run secondarily to confirm the current shipped behavior matches (the mechanism could in principle have changed since the pin).
- This experiment is Bun-internal (sync handler vs. async handler, both on Bun) — Node and Deno are not part of this comparison; it isolates a within-Bun code-path difference, not a cross-runtime one.

## Hardware / environment

Per Section 4. Requires a load generator capable of sustained high request rates without itself becoming the bottleneck — record load-generator tool/version/machine placement (same machine as server, or a separate machine, must be stated and held constant between Case A and Case B).

## Setup

1. Implement Case A: `Bun.serve({ fetch(req) { return new Response("ok"); } })`.
2. Implement Case B: `Bun.serve({ async fetch(req) { await Promise.resolve(); return new Response("ok"); } })` — the *only* difference from Case A is the `async`/`await Promise.resolve()`. No database, timer, network call, or additional allocation is introduced.
3. Verify via source/behavior inspection that Case B actually triggers the `to_async()` path (i.e., that a single microtask-queue suspension is sufficient to force the code path M16 describes, not just any `async` keyword) before treating the comparison as valid.

## Commands

```sh
# placeholder — finalized at Stage 13; sketch using a load generator such as `oha` or `wrk2`:
bun run case-a.ts &
oha -z 30s -c 50 http://127.0.0.1:PORT/
bun run case-b.ts &
oha -z 30s -c 50 http://127.0.0.1:PORT/
```

## Warmup

30 seconds of untimed load at the target concurrency precedes each timed window, sufficient for JIT tiering to stabilize on this simple a handler (verified by checking that per-second throughput has stopped trending during the last 25% of warmup).

## Repetitions

- Timed window: 60 seconds per run (chosen to average out short-term scheduling noise while keeping total experiment time reasonable across many independent runs).
- Independent runs: minimum 10 per case (fresh server process per run).
- Concurrency: primary run at concurrency = 50 (moderate, realistic-ish load); secondary run at concurrency = 1 (to isolate per-request cost from queueing effects) — both reported, clearly labeled.
- Outliers: not discarded by default.

## Metrics

- **Primary:** requests/sec.
- **Secondary:** median/p95/p99 latency; CPU utilization if the load generator or OS-level monitoring exposes it cleanly.

## Statistical method

Mean/median throughput across ≥10 independent runs with a CI (n≥5 satisfied); median/p95/p99 latency per Section 5.

## Expected result (directional only)

If M16's mechanism is measurable at this scale, Case A (sync) should show higher throughput / lower latency than Case B (async), attributable to the avoided copy. **A null result — no meaningful difference — is an explicitly anticipated, valid, and useful outcome per Stage 11/12's own framing ("technically real optimization ≠ practically important optimization")**, not a failed experiment requiring redesign.

## Falsifier

No meaningful throughput/latency difference between Case A and Case B under controlled conditions.

## Confounders / risks

- Must verify Case B actually exercises the exact code path M16 describes (see Setup step 3) — an `async` function that never actually suspends before Bun's synchronous fast path is checked would not test the mechanism at all.
- Load-generator overhead itself must be ruled out as a bottleneck (verify the load generator isn't CPU-saturated at the target concurrency before trusting a result).
