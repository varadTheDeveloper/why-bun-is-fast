# H4 Results — `Bun.serve()` Sync vs. Async Handler

**Status: EXECUTED. Second Stage 13 result. Pending your review before H5 begins.**

## Experiment

Bun-internal mechanism-isolation test of M16 (the sync/async headers+URL copy in `RequestContext.rs`'s request path). NOT a Bun-vs-Node-vs-Deno experiment.

## Hypothesis (H4, unmodified from Stage 11)

> An otherwise identical synchronous `Bun.serve()` handler should outperform the async-suspending handler because the async path forces the M16 headers + URL copy.

## Source-path verification (mandatory pre-run step — read this first, it changed the benchmark design)

**The literal test case sketched in Stage 11/12 (`async fetch(req) { await Promise.resolve(); return new Response("ok"); }`) does NOT exercise the M16 code path.** This was verified, not assumed, before any timed data was collected.

**Trace, from the project's pinned source clone (`oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4`, `src/runtime/server/RequestContext.rs`):**

1. `RequestContext::on_response()` (line 2545) is called with the JS handler's return value. Its **first line is `ctx.drain_microtasks()`** — it eagerly drains the JS microtask queue *before* checking whether the returned value is a pending Promise.
2. It then checks `as_response(response_value)` (direct `Response` object — the sync case) and, failing that, `response_value.as_any_promise()` followed by `promise.unwrap(...)`, which returns one of `PromiseResult::Pending`, `::Fulfilled(value)`, or `::Rejected`.
3. Only the `Pending` branch registers `.then_with_value(..., ON_RESOLVE, ON_REJECT)` and returns without rendering — this is the branch that requires the request to survive past the current stack frame, and it is the caller (`mod.rs`'s `on_request`/`handle_request`, lines ~1035-1046 and ~1096-1101) that then calls `ctx_ref.to_async(...)` — the function that performs M16's headers+URL copy — immediately afterward, gated on `!should_deinit_context.get() && !ctx_ref.should_render_missing()`.
4. The `Fulfilled` branch runs `ctx.protect_for_body_and_render(...)` and returns — **the exact same synchronous render path the direct-`Response` (sync) case takes.** If that render completes synchronously (true for a 2-byte body with no backpressure — confirmed for this workload), `deinit()` runs re-entrantly, `should_deinit_context` becomes true, and the caller's dispatch code returns *before* ever reaching `to_async()`.
5. **`await Promise.resolve()` is a microtask-only continuation.** `Promise.resolve()` yields an already-fulfilled promise, and `await` on it schedules its continuation as a microtask — which step 1's `drain_microtasks()` call fully processes, in the same synchronous call, before step 2's pending-check ever runs. By the time the check happens, the outer promise is already `Fulfilled`, not `Pending`. **So `await Promise.resolve()` takes the exact same code path as a fully synchronous handler and never calls `to_async()`.**

**Empirical corroboration against the actual benchmarked binary (release 1.3.13 — internal `ctx_log!` debug logging dead-strips in release builds per this project's own `CLAUDE.md`, so log-based confirmation wasn't available; used a behavioral check instead):**

| Variant (concurrency=1, 3 trials) | req/s (trial 1 / 2 / 3) |
|---|---|
| fully sync | 17,255 / 18,491 / 17,378 |
| `await Promise.resolve()` (microtask) | 17,429 / 17,925 / 18,338 |
| `await process.nextTick()` (Node-compat microtask-tier queue) | ~17,176 (single check) |
| `await new Promise(r => setImmediate(r))` (macrotask) | 16,186 / 15,763 / 16,758 |
| `await new Promise(r => setTimeout(r, 0))` (macrotask, timer-heap) | **736** (single check — collapsed) |

Sync and microtask-only suspension are statistically indistinguishable across 3 independent trials each — direct behavioral confirmation of the source trace. `setImmediate` shows a real, repeatable, moderate reduction (consistent with genuinely engaging the async/`to_async()` path). `setTimeout(0)` collapses throughput by ~95% — an artificial ~1ms timer-clamping floor (a well-known JS-engine/libuv timer-heap minimum-delay behavior) that has nothing to do with M16 and would have completely invalidated the experiment had it been used.

**Design decision: Case B uses `await new Promise(resolve => setImmediate(resolve))`, not `await Promise.resolve()`.** This is a documented, source-verified, empirically-corroborated deviation from the original sketch — not an arbitrary change. Full reasoning is also in `benchmark/case-b-async.ts`'s own code comment.

**Important interpretive consequence, stated up front:** because *any* genuine trigger of `to_async()` requires a real event-loop suspension (there is no way to force a `Pending` promise without one), Case B's measured cost is **"genuine async completion via the minimal-overhead available primitive," not M16's headers+URL copy in isolation.** It includes the `setImmediate` scheduling/resumption machinery's own cost *plus* M16's copy. This experiment cannot cleanly separate those two costs — a real, structural limitation of testing M16 via aggregate throughput, not a flaw specific to this run. See Confounders.

## Test cases (as run)

**Case A — synchronous:**
```js
Bun.serve({ fetch(req) { return new Response("ok"); } });
```

**Case B — async, corrected per the verification above:**
```js
Bun.serve({ async fetch(req) {
  await new Promise((resolve) => setImmediate(resolve));
  return new Response("ok");
} });
```

No database, timer-with-delay, filesystem I/O, external requests, crypto, JSON parsing, extra headers, or middleware were added, per the protocol. Response body is byte-identical (`"ok"`, `Content-Length: 2`) between both cases.

## Response validation (completed before timing)

- ✅ HTTP status identical (200 for both).
- ✅ Response body byte-identical (`diff` confirmed).
- ✅ Headers identical except `Date` (expected — request-time-dependent).
- ✅ Keep-alive/connection-reuse behavior identical (both reused the connection for a second sequential request in a quick check).
- Same client (`curl` for the validation check; `autocannon` for all timed measurement) used for both cases.

## Environment

Full record: `results/metadata.json`. Same 2-vCPU shared KVM sandbox used for H6 (Intel Xeon @ 2.80GHz, Ubuntu 24.04.4, kernel 6.18.5). CPU steal spot-checked post-run at ~0.006% (negligible at that instant, not continuously monitored). Load generator (autocannon) ran on the same machine as the server — not a separate host, same structural limitation as H6.

**As instructed: this environment does not meet the quality bar for authoritative, dedicated-hardware measurement, and H4 is more sensitive to host noise than H6 was (M16's expected effect is much smaller than H6's I/O step). This is treated as a first-order concern in the analysis below, not a footnote.**

## Bun version / commit

- **Benchmarked binary:** Bun 1.3.13 (release, pre-installed in sandbox).
- **Source used for mechanism verification:** the project's pinned clone at `8326d1bd39a96f1f298c3de195aad15972d4f3b4` — used only to read the control-flow logic, not built or benchmarked. This is a documented gap (see `metadata.json`): we did not independently confirm 1.3.13's compiled behavior matches this exact commit's source line-for-line. The empirical corroboration above (sync ≈ microtask ≠ macrotask, measured against the actual 1.3.13 binary) is the evidence that the same architectural pattern holds in the binary actually under test.

## Load-generator configuration

autocannon 8.0.0, same tool/version/command structure for both cases, primary concurrency 50 (**run exactly as specified — empirically verified pre-run to produce zero errors/timeouts on this handler**, unlike H6 which had to reduce concurrency due to database contention), secondary concurrency 1 (removes queueing, closer to per-request cost, per protocol). Documented deviation: 10s timed / 5s warmup instead of 60s/30s (session wall-clock constraint, same reasoning as H6).

## Warmup

5s untimed autocannon run per server before each timed run, at the same concurrency as the timed run that follows it. Not independently verified via a flat-trend check (a protocol gap, same as H6) — but the pre-run verification runs (3 trials each, consistent results) provide indirect evidence that steady state is reached quickly for a handler this simple.

## Repetitions

10 independent runs per (case × concurrency) combination — 4 combinations × 10 runs = **40 total runs, fresh server process every time.** Zero failed runs, zero errors, zero timeouts across all 40.

## CPU contention check

Server-process CPU% sampled every 0.5s during each timed window via `psutil` (lightweight, non-blocking, not a rigorous perf-based measurement — stated as a limitation, not hidden). Results (median across 10 runs):

| Combo | Server CPU% (median) |
|---|---|
| Case A, c=1 | 45.8% |
| Case B, c=1 | 49.5% |
| Case A, c=50 | 64.4% |
| Case B, c=50 | 78.0% |

**Case B consistently used more CPU than Case A at both concurrency levels** — consistent with genuinely doing more work (scheduling + resuming a suspended handler costs real CPU cycles beyond a synchronous return). Load-generator CPU was not independently sampled (autocannon runs as a separate process; sampling it would have required tracking its PID through subprocess creation, not implemented this run — a real gap, noted rather than hidden, per the protocol's explicit allowance: "If CPU saturation cannot be measured reliably: record: CPU contention could not be independently verified for the load generator specifically.").

## Raw data

40/40 runs succeeded. Full unaggregated autocannon JSON per run: `raw/case<A|B>-c<concurrency>-run<NN>.json`. Per-run CPU samples and full metadata: `raw/run_index.json`. No data trimmed or discarded.

## Statistical summary

Per `notes/12-experiment-design.md` Section 5: median primary, mean/stddev/CV secondary, 95% CI (n=10, t=2.262).

| Combo | Throughput median (req/s) | 95% CI | CV | Server CPU% (median) |
|---|---|---|---|---|
| Case A (sync), c=1 | **19,289** | (18,855 – 19,527) | 2.5% | 45.8% |
| Case B (async), c=1 | 17,455 | (16,912 – 17,653) | 3.0% | 49.5% |
| Case A (sync), c=50 | 69,969 | (68,767 – 71,984) | 3.2% | 64.4% |
| Case B (async), c=50 | **71,742** | (68,990 – 74,321) | 5.2% | 78.0% |

Mean latency (autocannon's finer-grained field, ms):

| Combo | Latency mean (median across runs) |
|---|---|
| Case A, c=1 | 0.01 (autocannon's rounding floor — no discriminating signal at this concurrency/tool resolution) |
| Case B, c=1 | 0.01 (same floor) |
| Case A, c=50 | 0.153 |
| Case B, c=50 | 0.127 |

## Throughput difference (predeclared metric: (sync − async) ÷ async)

- **c=1 (secondary, low-queueing):** (19,289 − 17,455) / 17,455 = **+10.5%** — sync faster. 95% CIs **do not overlap** (18,855–19,527 vs. 16,912–17,653) — a statistically distinguishable difference.
- **c=50 (primary, as specified):** (69,969 − 71,742) / 71,742 = **−2.5%** — async *faster* (opposite direction from the hypothesis). 95% CIs **substantially overlap** (68,767–71,984 vs. 68,990–74,321) — not statistically distinguishable from noise at this concurrency.

## Latency results

At c=1, autocannon's latency reporting bottoms out at its measurement floor for both cases (median and mean both round to 0.01ms) — **no discriminating latency signal available at this concurrency with this tool**, despite a real throughput difference. At c=50, mean latency is lower for Case B (0.127ms) than Case A (0.153ms) — consistent with, not contradicting, the throughput reversal at this concurrency (both point the same direction: async "wins" at c=50 in this environment).

## Falsification status

**Predefined falsifier:** "No meaningful throughput or latency difference between Case A and Case B under controlled conditions."

**Verdict: SUPPORTED — but narrowly, and only at the secondary (c=1) concurrency. NOT reproduced at the primary (c=50) concurrency specified by the protocol.**

I am giving one required top-line word (SUPPORTED) because a real, statistically clean, reproducible effect in the hypothesis's predicted direction exists at c=1 (non-overlapping 95% CIs, low CV, consistent with three independent pre-run verification trials using the same suspension mechanism). But this is not an unqualified confirmation: at c=50 — the concurrency the protocol specified as primary — the difference is not statistically distinguishable from noise, and the point estimate runs in the *opposite* direction. Both results are reported in full above; neither is cherry-picked or omitted.

## What the result supports

- **FACT:** at concurrency=1, Case A (sync) achieved measurably, repeatably higher throughput than Case B (async) — a statistically distinguishable ~10.5% difference across 10 independent runs each.
- **FACT:** at concurrency=50, no statistically distinguishable throughput or latency difference was found; the point estimate favored Case B (async) by a small margin.
- **FACT:** Case B consistently used more server CPU than Case A at both concurrency levels — consistent with genuine async-suspension overhead being paid, distinct from (and probably larger than) M16's copy alone.
- **INFERENCE:** the c=1 result is consistent with M16 (and/or the broader cost of async completion, which necessarily includes M16) having a real, small, measurable per-request cost that is visible when requests aren't queued behind each other, but gets swamped by other effects (queueing, scheduling, host contention) once many requests compete for the same 2 cores.

## What the result does NOT support

- Does **not** isolate M16's copy cost specifically from the general cost of genuine async suspension (`setImmediate`'s own scheduling overhead) — see the interpretive consequence noted in Source-path verification above. The ~10.5% figure at c=1 is an upper bound on M16's contribution, not a clean measurement of it alone.
- Does **not** establish that Bun's sync `Bun.serve()` handler is faster than its async handler "in general" or "at production request rates" — the c=50 result directly contradicts that generalization in this environment.
- Does **not** confirm the exact `to_async()` path ran on the literal, currently-installed 1.3.13 binary via internal instrumentation (release builds strip the relevant debug logs) — confirmed instead via source trace against the pinned commit plus behavioral corroboration, a slightly weaker form of evidence than a direct instrumented confirmation would be.
- Does **not** establish anything about M16's behavior on non-shared, multi-core, production-representative hardware.

## Surprising findings

1. **The originally-sketched benchmark design (`await Promise.resolve()`) does not test M16 at all** — a finding independent of anything about magnitude, purely about methodology, and directly validates why the pre-run verification requirement mattered. This is a genuinely article-worthy, non-obvious fact about how Bun's request-completion logic interacts with JS's microtask/macrotask distinction.
2. **The effect reverses direction at higher concurrency** (async became *faster*, not just "not slower," at c=50) — not a simple "effect shrinks and disappears," but a directional flip. The most plausible explanation, given Case B's consistently higher CPU utilization at c=50 (77.9% vs. 64.4%), is that the async path's yielding behavior interacts with how this specific, heavily shared 2-core environment schedules the server process against the co-located load generator — but this is a **hypothesis about why**, not something this experiment independently confirmed, and it should not be stated as fact in any future write-up without a dedicated-hardware re-run to test it.
3. Neither environment discriminated any latency signal at c=1 despite a clear throughput signal — a tooling-resolution limitation, not a substantive finding, but worth flagging for anyone designing a follow-up (autocannon's stated latency floor is too coarse for sub-millisecond, high-throughput microbenchmarking; a higher-resolution tool like `mitata` would be needed for a cleaner isolation of M16 specifically, consistent with H1's protocol already specifying `mitata` for exactly this reason).

## Counter-evidence

The c=50 result is itself counter-evidence to a naive, unscoped reading of "M16 makes Bun's sync handler faster" — it does not, at least not detectably, under load on this hardware. This is reported in full, not minimized, consistent with the project's standing rule that a result weakening the current narrative is not a failed experiment.

## Confounders / limitations

- **2-vCPU shared sandbox**, same as H6 — the single largest limitation, and more dangerous here than for H6 because M16's expected effect size is much smaller than H6's I/O-step effect, making it proportionally far more vulnerable to host-level noise and CPU contention with the co-located load generator.
- **Cannot isolate M16's copy cost from `setImmediate`'s own scheduling cost** — a structural limitation of testing this mechanism via any genuine-suspension design, not specific to this run's execution quality.
- **No independent confirmation that the exact `to_async()` path ran on the benchmarked 1.3.13 binary** via internal instrumentation — relies on source trace (pinned commit) plus behavioral corroboration (actual 1.3.13 binary), not a perfectly closed loop.
- **Load-generator CPU usage not independently sampled** — only the server process was monitored; if autocannon itself became differentially CPU-starved between Case A and Case B (plausible, given the server's own CPU usage differed), that could itself explain part of the c=50 reversal, and this experiment cannot rule that out.
- **Documented duration/warmup deviations** (10s/5s vs. 60s/30s) — same reasoning as H6.
- **Autocannon's latency-percentile resolution** was too coarse to discriminate at c=1 despite a real throughput signal.

## Data-quality assessment

Classification per the protocol's three-tier system: **PILOT / LIMITED.**

Not INCONCLUSIVE — the measurements themselves are clean (zero errors, low CV, real statistically-distinguishable differences where they exist) and the source-path verification work substantially raises confidence that Case B is now correctly designed. Not AUTHORITATIVE — the 2-vCPU shared sandbox, the CPU-contention confound at c=50, the inability to isolate M16 specifically from general suspension cost, and the unconfirmed 1.3.13-vs-pinned-commit behavioral match all fall short of what "authoritative" should mean for a result this small in magnitude. This experiment should be re-run on dedicated, non-shared hardware with a higher-resolution timing tool before its numbers are used in the final article as anything more than "an experiment we ran and what it found, with these specific caveats."

## Evidence Map impact (targeted — M16 only, per instruction)

Recommend, once reviewed and approved, attaching to M16 in `evidence/evidence-map.md`:

- Benchmark result classification: **mixed — "NO MATERIAL EFFECT DETECTED at aggregate/high-concurrency conditions; a real, small effect detected at low concurrency, with the caveat that the low-concurrency effect measures 'cost of genuine async suspension' more broadly than M16's copy specifically."** Not a clean "SUPPORTS MAGNITUDE" (the effect isn't isolated finely enough, and doesn't hold at the primary tested concurrency), and not a clean "NO MATERIAL EFFECT DETECTED" either (a real effect was found at c=1).
- The methodological finding (that `await Promise.resolve()` doesn't trigger `to_async()`) is worth recording independent of the magnitude question — it's a durable, source-verified correction to how this mechanism should be tested in any future work.
- **Do NOT** modify M2, M9, M15, M17, M18, or M22 based on this experiment — H4 is scoped to M16 only, as instructed.

**`evidence/evidence-map.md` has not been edited.** Waiting for your review before making this targeted change.

## Recommendation for H5

Proceed to H5 (Buffer pool asymmetry) as planned once this report is reviewed. Two carry-forward notes:
1. **H5 has no HTTP server or load generator** — it's a pure JS allocation loop — so it should be largely immune to the CPU-contention-with-load-generator confound that complicated H4's c=50 result. This is a meaningful advantage for H5's data quality on this same hardware.
2. **Consider using a higher-resolution timing approach for H5** than raw autocannon-style wall-clock sampling, given H4 surfaced a real tooling-resolution limit at small effect sizes — H5's protocol already specifies per-size-class allocation throughput over a fixed iteration count, which is less exposed to this issue than H4's latency-percentile measurement was, but it's worth keeping in mind if H5's effect also turns out to be small.

---

*Every number in this document is a real, measured result from the 40 runs described above, or a value directly read from the project's pinned Bun source. No number was invented, adjusted, or selected after the fact. Raw data: `../raw/`. Full statistical summary: `summary.json`. Benchmark source: `../benchmark/`.*
