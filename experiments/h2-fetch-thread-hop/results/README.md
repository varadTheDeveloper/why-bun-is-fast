# H2 — fetch() Thread-Hop Overhead

## Experiment

Tests M9: does Bun's dedicated `HTTPThread` (client-side, i.e. `fetch()`)
introduce measurable per-request latency overhead for tiny local requests,
relative to Node's and Deno's same-thread dispatch? This is an HTTP
**client** experiment — explicitly not `Bun.serve()`, not server
throughput, not M16, not database I/O (kept fully separate from H4/H6).

## Hypothesis (H2)

> Bun's dedicated fetch HTTP thread introduces measurable overhead for
> tiny local requests.

**Prediction:** Bun may show *higher* steady-state latency than Node/Deno,
because the request must cross the JS-thread → HTTP-thread →
completion/wakeup path. No exact magnitude was predicted in advance.

**Falsifier (declared before running):** No meaningful latency difference
after the protocol controls are satisfied.

## Source mechanism

**Bun (`src/http/HTTPThread.rs`, `src/runtime/webcore/fetch.rs`,
`src/runtime/webcore/fetch/FetchTasklet.rs`,
`src/event_loop/ConcurrentTask.rs`, `src/uws_sys/Loop.rs`, pinned commit
`8326d1bd...`):** `fetch()` hands the request to a second thread running
its own independent event loop that owns socket I/O; completion is
signaled back via a `ConcurrentTask` posted to an unbounded queue, then
`Loop::wakeup()` → `us_wakeup_loop()`, incrementing an atomic counter
checked around the JS thread's blocking epoll/kqueue call.

**Node:** libuv's documented design keeps "OS socket readiness → JS
continuation" on a single thread (`StreamListener::OnStreamRead` →
`MakeCallback`) — no queue, no cross-thread wake syscall.

**Deno:** its `current_thread` Tokio runtime keeps JS/V8 on the same
thread that polls the event loop (`deno_core::JsRuntime::poll_event_loop`)
— same single-thread pattern as Node for this specific path.

Per the existing Evidence Map note (M9/M10): Bun is the *only one of the
three runtimes* that adds a thread hop for `fetch()` specifically — this
is a real, previously source-verified architectural asymmetry, not a
guess. **The explicit warning carried into this experiment (Section 3):**
do not translate "Node has no Bun-style HTTP thread" into "Node therefore
must be faster" — that is precisely the question H2 was designed to
answer, not assume.

## Environment

Same shared 2-vCPU cloud sandbox as H3/H4/H5/H6 (not dedicated hardware)
— classified LIMITED/PILOT for that reason alone, per the standing Stage
13 instruction. Server, client, and orchestrator all run on the same
machine over loopback (127.0.0.1). Full detail in `metadata.json`.

## Runtime versions

| Runtime | Version | Track |
|---|---|---|
| Bun | 1.3.13 | release |
| Node | v22.22.2 | release |
| Deno | 2.9.5 (stable) | release |

Release builds for all three, as the H2 protocol itself specified
(primary track = release, consistent across all three — no
source-controlled builds implied or used here).

## Server configuration

A single, fixed Python 3 stdlib `http.server` implementation (**not**
implemented in any of the three tested runtimes, so the server can never
become a hidden variable), bound to `127.0.0.1:8765` (IPv4 explicit, no
DNS), HTTP/1.1. One server **process** was started once and used for the
smoke test plus the entire cold and keep-alive phases for all three
runtimes — the literal same running process, the strictest possible
reading of "identical server for every client comparison." Two routes:
`/` (fixed 11-byte JSON body `{"ok":true}`, default persistent
connection) and `/cold` (identical body, but sends `Connection: close`,
forcing a fresh TCP connection via standard HTTP/1.1 semantics — not a
client-side hack). `TCP_NODELAY` is set on every accepted server socket
(disclosed fix, see below).

## Client configuration

`fetch-bench.js`, the identical script executed under all three
runtimes (no runtime-specific branches except the final version-report
field). Sequential requests only (concurrency = 1 — the cleanest
isolation of per-request dispatch overhead, per Section 14). Each request
is verified for `status === 200`, exact body match, and exact
`Content-Length` match.

## Smoke-test result

5 keep-alive + 5 cold requests per runtime, run first (Section 18). Two
real problems were caught and fixed **before** the official run, both
disclosed rather than silently patched:

1. **Nagle/delayed-ACK artifact:** initial smoke-testing (before
   `TCP_NODELAY`) showed latencies with large (tens-of-ms) spikes on small
   request/response packets — a classic Nagle's-algorithm/delayed-ACK
   interaction. Fixed by setting `TCP_NODELAY` on the server socket
   (applied identically to all three client comparisons, since it's
   server-side).
2. **Node-specific warmup insufficiency:** even after the Nagle fix,
   Node's fetch (undici) needed far more warmup (8,000-16,000+ requests)
   than Bun (2,000-4,000) or Deno (4,000-8,000) before its per-request
   latency stabilized. A fixed, small, equal warmup budget would have
   silently made Node look several times slower than its true
   steady-state behavior — a warmup artifact, not a real signal. Fixed by
   a chunked, predefined warmup-stability check requiring **two
   consecutive passing attempts** (a single-attempt check was found to be
   fooled by an intermediate JIT-tiering plateau specific to Node's
   fetch), finalized before the official 10-run data collection and not
   modified afterward. Full detail and reasoning preserved as code
   comments in `fetch-bench.js`.

After both fixes: smoke test **PASSED** for all three runtimes — status
200 / exact body / exact Content-Length for every request, keep-alive
connection reuse confirmed (<5 connections opened across warmup+timed
requests), cold fresh-connection behavior confirmed (connections opened
within 2% of expected total requests).

## Cold-connection methodology

For each runtime: 10 independent fresh-process runs × 10 timed cold
requests (each hitting `/cold`, guaranteed a fresh TCP connection by the
server's `Connection: close` response) = **100 cold latency samples per
runtime**. Each run does a fixed 300-request untimed warmup first (JIT/
process warmup only — cold mode has no persistent connection to warm by
definition; see "Smoke-test result" for why the escalating stability
check was not used here).

## Keep-alive methodology

For each runtime: 10 independent fresh-process runs × 10,000 timed
sequential requests (each hitting `/`, over one reused persistent
connection) = **100,000 timed latency samples per runtime**. Warmup and
timed measurement run inside the **same process/same connection** (an
earlier two-process design was caught during smoke-testing to silently
discard warmup between phases — documented and fixed, see
`fetch-bench.js` header comment). Warmup uses the chunked,
stability-confirmed design described above, extended (bounded, up to
128,000 iterations) until two consecutive attempts pass.

## Warmup

Summarized per runtime across the 10 official keep-alive runs (final
stable iteration count varied naturally by runtime, not tuned):

| Runtime | Warmup iterations (range across 10 runs) |
|---|---|
| Bun | 2,000 – 4,000 |
| Deno | 4,000 – 8,000 |
| Node | 16,000 – 32,000 |

All 10 runs reached `STABLE` for all three runtimes — no run hit the
bounded extension ceiling.

## Repetitions

10 cold runs × 10 timed requests + 10 keep-alive runs × 10,000 timed
requests, per runtime = 60 total process runs, 0 failures. Fresh process
per run throughout.

## Raw-data integrity

Every run preserved individually as
`raw/<runtime>-<mode>-run<NN>.json` (60 files, each with its full
`latenciesNs` array — nothing was pre-aggregated away), plus
`raw/run_index.json` (summary metadata for all 60 runs) and
`raw/smoke_test_results.json`.

## Latency results

**PRIMARY METRIC** — pooled across all runs per combo (100 samples for
cold, 100,000 for keep-alive):

| Runtime | State | Median latency | p95 | p99 | Throughput (median, c=1) |
|---|---|---:|---:|---:|---:|
| Bun | Cold | 400.6 μs | 572.2 μs | 619.6 μs | — |
| Node | Cold | 1,143.3 μs | 1,984.6 μs | 2,438.4 μs | — |
| Deno | Cold | 639.3 μs | 852.1 μs | 1,040.7 μs | — |
| Bun | Keep-alive | 155.1 μs | 225.1 μs | 376.6 μs | 5,984 req/s |
| Node | Keep-alive | 191.0 μs | 341.0 μs | 699.0 μs | 4,174 req/s |
| Deno | Keep-alive | 164.1 μs | 250.6 μs | 423.2 μs | 5,338 req/s |

**Result direction is the opposite of H2's prediction.** The hypothesis
predicted Bun might show *higher* latency due to the thread-hop. The
measured result shows **Bun with the lowest latency of the three
runtimes, in both cold and keep-alive conditions** — not higher.

## Throughput results

**SECONDARY METRIC**, concurrency = 1 (cleanest isolation of per-request
dispatch overhead, per Section 14): Bun 5,984 req/s > Deno 5,338 req/s >
Node 4,174 req/s (all medians across 10 independent runs). Consistent
with the latency ordering above (throughput and latency are simply
inverses of each other at concurrency = 1 for a sequential workload, so
this is not an independent confirmation, just the same signal expressed
differently).

## Statistical analysis

95% CI (t-distribution, df=9) computed on the **10 per-run medians**
(the appropriate independent-sample unit, consistent with H4/H5/H6's
methodology — treating 100,000 individual within-run-correlated
latencies as independent samples would understate variance):

| Combo | Mean of run-medians | 95% CI |
|---|---:|---|
| bun-cold | 412.1 μs | [377.3 – 447.0] |
| deno-cold | 650.3 μs | [621.8 – 678.9] |
| node-cold | 1,153.2 μs | [1,086.1 – 1,220.4] |
| bun-keepalive | 155.7 μs | [153.4 – 158.0] |
| deno-keepalive | 164.6 μs | [158.2 – 171.1] |
| node-keepalive | 191.7 μs | [183.1 – 200.4] |

**None of the three runtimes' cold-latency CIs overlap** — Bun < Deno <
Node is statistically clean for cold connections. For keep-alive, Bun and
Node's CIs are clearly non-overlapping; Bun and Deno's CIs are close but
still non-overlapping (158.0 vs 158.2 — a narrow, real gap, not a
dramatic one). Cross-run consistency (CV of the 10 run-medians) was very
low for all combos (2.1%–11.8%), confirming these are reproducible,
low-noise results, not one-off flukes. Pooled-sample CV is higher
(48%–146%) reflecting genuine right-tail latency variance within each
run (expected for any real request-latency distribution — reported
honestly via p95/p99, not hidden by only reporting the median).

Server CPU utilization stayed well below saturation throughout (max
~68.6% for a bun-cold run; node-keepalive ~40%), confirming the server
was not the bottleneck in any combo — the client's own dispatch path was
the pacing factor throughout (server load tracked client request rate,
lower for the slower Node runs, exactly as expected if the client is
setting the pace).

## Falsification status

**NOT SUPPORTED.** There IS a meaningful, statistically robust latency
difference between the three runtimes (the falsifier's "no meaningful
difference" condition is not met) — but the H2 hypothesis specifically
predicted Bun would show *higher* latency due to the thread-hop
mechanism, and the measured direction is the opposite: Bun has the
*lowest* latency of the three in both cold and keep-alive conditions.
A real difference in the wrong direction for the hypothesis is not a
confirmation — it is a direct falsification of the predicted
consequence. This is reported as NOT SUPPORTED, not reframed as
"inconclusive" or spun as an unrelated win for Bun.

## What the result supports

- A real, reproducible, statistically clean latency difference between
  the three runtimes' `fetch()` client implementations exists for tiny
  local requests, in both cold-connection and keep-alive/steady-state
  conditions.
- Bun's `fetch()` is measurably faster end-to-end than Node's and Deno's
  for this specific workload (local loopback, tiny JSON response,
  concurrency = 1), despite Bun being the only one of the three that adds
  a cross-thread hop for this call.

## What it does NOT support

- **Does not support H2's predicted mechanism.** The experiment does not
  show the thread-hop imposing a net latency cost — whatever the
  thread-hop's own cost is, it is evidently outweighed by something else
  in Bun's fetch path that makes the aggregate faster, not slower.
- **Per the explicit interpretation guard (Section 17): does NOT support
  "the thread hop is beneficial."** This experiment measures only the net,
  end-to-end client latency — it cannot isolate the thread-hop's own
  contribution from other parts of Bun's fetch implementation (JSC-side
  request/response object handling, header parsing, body reading, etc.).
  The thread-hop may coexist with — and be outweighed by — other,
  unmeasured faster components. A follow-up experiment that isolates the
  thread-hop specifically (e.g., comparing Bun's fetch against a
  same-thread-dispatch variant, if one existed) would be needed to make
  any causal claim about the thread-hop's own sign or magnitude.
- Does not establish that Node's or Deno's client architecture is
  "worse" in any general sense — this is one narrow workload (tiny local
  JSON, loopback, concurrency 1); different payload sizes, TLS,
  compression, or remote-network conditions could shift the ordering.
- Does not isolate whether Node's specific slowness (both cold and
  keep-alive) traces to undici's JS-side overhead, its warmup/JIT-tiering
  behavior (see "Surprising findings"), its connection-pool bookkeeping,
  or something else — the isolation needed to make that attribution was
  not part of this experiment's design.

## Surprising findings

- **The result direction is the opposite of the H2 hypothesis.** This is
  itself the most important finding of the experiment — the source-level
  architectural asymmetry (Bun alone adds a thread hop) does not manifest
  as a latency disadvantage in aggregate, at least not one large enough
  to survive whatever else differs between the three fetch
  implementations.
- **Node's fetch needed dramatically more warmup than Bun or Deno**
  (16,000-32,000 vs 2,000-8,000 iterations) before reaching a stable
  steady state — a genuine, reproducible, previously-undocumented
  (in this project) finding about undici's JIT-tiering/warmup behavior,
  caught only because the harness's stability check was designed to
  extend rather than assume a fixed warmup was sufficient. This is a
  methodologically important finding in its own right, independent of
  the main latency result.
- **Node's cold-connection latency (1,143 μs median) is nearly 3x Bun's**
  (401 μs) and nearly 2x Deno's (639 μs) — a much larger relative gap
  than the keep-alive gap (Node 191 μs vs Bun 155 μs, only ~23% higher).
  Connection-establishment overhead appears to be where Node's
  disadvantage is largest, not steady-state per-request dispatch.

## Counter-evidence

This result is direct counter-evidence against the specific mechanism
H2 set out to test: if Bun's dedicated HTTP thread imposed a dominant,
visible latency cost, Bun should have been slower, not faster, in this
controlled measurement. It is not counter-evidence against M9's
underlying architectural claim (the thread-hop mechanism itself is still
correctly source-verified to exist and to work as described) — only
against the inference that this specific mechanism dominates aggregate
client-side fetch() latency for tiny local requests.

## Confounders / limitations

- Shared 2-vCPU cloud sandbox, not dedicated hardware (disclosed per
  Stage 13's standing instruction).
- Server implemented in Python, not a highly-optimized C/native HTTP
  server — while held identical across all three client comparisons (so
  it cannot bias the *ordering*), its absolute overhead is baked into
  every reported number and this is not a claim about "real-world raw
  HTTP overhead," only about relative client ordering under one fixed
  server.
- This experiment measures only the AGGREGATE, END-TO-END client latency
  — it cannot and does not isolate the thread-hop's own individual
  contribution from other parts of each runtime's fetch implementation
  (see "What it does NOT support").
- Node's much longer warmup requirement, while handled correctly by the
  stability-check design, means Node's steady state was reached later in
  wall-clock terms per run — this by itself doesn't bias the *timed*
  measurement (which only starts after stability is confirmed), but is
  noted as a genuine architectural difference worth further
  investigation in its own right.
- Single-machine, single-session sample; no cross-machine replication.

## Data-quality classification

**PILOT / LIMITED** — driven by shared (non-dedicated) hardware, per the
standing Stage 13 instruction, despite the underlying data itself being
unusually clean: all 12 data-quality checklist items pass (same server
for all runtimes, same response, same network interface/IPv4, correct
cold/keep-alive distinction with server-level connection verification,
completed warmup with a predefined stability check, minimum run counts
met and exceeded, all raw measurements preserved with full underlying
distributions, no outlier removal, CPU/server-load effects recorded and
showed no bottleneck, runtime versions recorded, environment limitations
disclosed above).

## Evidence Map impact (recommendation only — not applied to evidence-map.md pending review)

Recommended targeted update to **M9 only**: this does not cleanly fit
"SUPPORTS MAGNITUDE" (the predicted direction was wrong), "NO MATERIAL
EFFECT DETECTED" (there IS a large, clean effect — just not the predicted
one), or "INCONCLUSIVE" (data quality was good, the answer is just not
what was hypothesized). The best fit is a variant explicitly acknowledged
by the Section 24 categories as closest to **MIXED**, with the specific
qualification that the "effect" that exists is real but runs counter to
M9's predicted direction: a real, controlled, reproducible latency
difference exists (Bun fastest), but the experiment does NOT support
attributing it to the thread-hop mechanism (it could easily be outweighed
by JS-side overhead differences elsewhere in each runtime's fetch
implementation) — and specifically REJECTS the hypothesis that the
thread-hop imposes a net observable cost in this workload. Do not modify
M10, M15, M16, M17, M18, or M22 based solely on H2.

## Recommendation for H1

No specific dependency identified between H2's findings and H1's scope.
H2 was self-contained (fetch() client latency, Bun/Node/Deno). Proceed to
H1 per the standing sequence (H6 ✅ → H4 ✅ → H5 ✅ → H3 ✅ → H2 ✅ → H1 → H7
deferred) once this report is reviewed.

## Exact benchmark source and run commands

```
python3 benchmark/orchestrate.py   # starts server, runs smoke test, then full 60-run protocol
python3 benchmark/analyze.py       # statistics + result tables
```

Per-run invocation (as issued by orchestrate.py):
```
<bun|node|deno [run --allow-net]> fetch-bench.js http://127.0.0.1:8765 <cold|keepalive> <timedCount>
```
