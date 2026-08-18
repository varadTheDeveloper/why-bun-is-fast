# H2 — `fetch()` thread-hop overhead

**Status: protocol only. Not yet executed. Classification: SHOULD RUN.**

## Purpose

Test whether Bun's dedicated background `HTTPThread` (used by `fetch()`) introduces measurable per-request latency overhead for tiny local requests, relative to Node's and Deno's same-OS-thread dispatch paths.

## Hypothesis (H2, from Stage 11 — unmodified)

Bun's `fetch()` thread hop introduces measurable overhead for tiny local requests.

## Mechanism

M9 (Bun's cross-thread fetch dispatch: JS thread → queue → `HTTPThread` → socket → completion queue → wake → JS thread) contrasted with M10 (Node's and Deno's single-thread dispatch, no queue, no wake syscall).

## Runtime versions / commits

- **Track used:** primarily release comparison (this is an architectural, not line-pinned, mechanism at the level H2 tests it — the thread-hop's existence is what's pinned in source, not its exact timing behavior at a specific commit). Source-controlled pins available as a cross-check: Bun `8326d1bd39a96f1f298c3de195aad15972d4f3b4`, Node `ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85`.
- **Deno: BLOCKED on the same commit-pin gap as H1** (Stage 11 open item 27) if the source-controlled cross-check is desired; the release-comparison leg can proceed independently using Deno's current stable release.
- Record actual versions into `metadata.json` at execution time.

## Hardware / environment

Per Stage 12 Section 4. Loopback-only networking reduces but does not eliminate host-level noise sensitivity — record CPU/OS/virtualization details; note explicitly if run on the shared cloud sandbox rather than dedicated hardware.

## Setup

1. Implement a single, minimal loopback HTTP server (fixed small response, e.g., `{"ok":true}`), used identically as the target for all three runtimes' clients. **This server implementation must not change between runtime comparisons** — only the client runtime varies.
2. Implement three client harnesses (Bun `fetch()`, Node `fetch()`/undici, Deno `fetch()`) issuing requests to the same server.
3. Bind server and clients to IPv4 loopback (`127.0.0.1`) explicitly, to remove a dual-stack (IPv4/IPv6) confound.

## Commands

```sh
# placeholder — finalized once benchmark/ is implemented at Stage 13
./run.sh --client=bun
./run.sh --client=node
./run.sh --client=deno
```

## Warmup

A fixed number of untimed requests (proposed: 1,000) precede each timed run to reach steady TCP/keep-alive state, separate from the cold-vs-warm connection distinction below (which is about individual connection state, not JIT warmup — this experiment is I/O-bound, not compute-bound, so JIT-tiering warmup per Section 6 is a secondary concern here, though still worth 1,000 requests of buffer).

## Repetitions

- **Cold connection (first request):** measured and reported separately — minimum 100 independent cold-connection measurements (fresh connection each time) per runtime.
- **Steady-state (established connection, keep-alive):** minimum 10,000 requests per run, minimum 10 independent runs (fresh process per run) per runtime.
- Outliers: not discarded by default; raw distribution preserved.

## Metrics

- **Primary:** per-request latency — median, p95, p99, reported separately for cold-connection and steady-state cases.
- **Secondary:** throughput (req/sec) at a fixed, modest concurrency level (proposed: concurrency = 1, sequential requests, as the cleanest isolation of per-request dispatch cost; a secondary concurrency = 10 run as a supplementary data point, clearly labeled as such).

## Statistical method

Per Section 5 of the design doc: median + p95/p99 for latency; throughput as mean/median across ≥10 independent runs with a CI only if n≥5 (satisfied here).

## Expected result (directional only)

If M9's mechanism story holds, Bun's `fetch()` should show higher per-request latency than Node's or Deno's for the steady-state case, attributable to the queue-plus-wake-syscall round trip. No specific magnitude predicted.

## Falsifier

No meaningful latency difference after controls. **Interpretive guard (restated from Stage 11/Stage 12):** if Bun is measured as slower, this alone does not confirm the thread hop as the cause unless the isolation is tight enough to rule out undici-side or Deno-fetch-side JS-level overhead as the actual source — the results write-up must state explicitly whether that isolation was achieved.

## Confounders / risks

- Host-level scheduling noise even on loopback.
- undici's own JS-level overhead could mask or exaggerate the thread-hop's contribution — must be discussed explicitly in the results, not assumed away.
- Deno commit pin gap (source-controlled leg only; release-comparison leg unaffected).
