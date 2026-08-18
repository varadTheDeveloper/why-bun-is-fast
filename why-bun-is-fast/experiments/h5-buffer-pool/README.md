# H5 — Buffer allocation pooling

**Status: protocol only. Not yet executed. Classification: MUST RUN.**

## Purpose

Test whether Node's 64 KB `Buffer.allocUnsafe()` pool (32 KB carve threshold) provides a measurable allocation-throughput advantage over Bun's unpooled, fresh-allocation-per-call path (`JSC::JSUint8Array::createUninitialized()`).

## Hypothesis (H5, from Stage 11 — unmodified)

Node's Buffer pool provides measurable allocation efficiency Bun's fresh-allocation path lacks.

## Mechanism

M20 — both code paths fully traced in Stage 9 (`src/jsc/bindings/JSBuffer.cpp` for Bun; `lib/buffer.js` for Node).

## Runtime versions / commits

- **Track used: both.** Source-controlled: Bun `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4`, Node `nodejs/node@ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85`. Release build as a real-world cross-check.
- **Deno explicitly excluded** — Deno's `node:buffer` polyfill pooling status is unresolved (Stage 9/11 open item 26); including an unverified guess would misrepresent the comparison. If item 26 is resolved before Stage 13, Deno can be added as a third leg.

## Hardware / environment

Per Section 4. Memory-throughput microbenchmarks are somewhat less host-noise-sensitive than nanosecond-scale call-timing (H1) but still benefit from a quiet, dedicated machine, particularly for the GC-activity secondary metric.

## Setup

1. Implement a loop allocating `Buffer.allocUnsafe(size)` for `size` in `{16 B, 64 B, 256 B, 1 KB, 4 KB, 16 KB}` (chosen to span three orders of magnitude while staying under Node's 32 KB carve threshold at every point; 16 KB included as the size closest to the threshold, where pool-refresh frequency — and any pooling advantage — should be most visible).
2. Each allocated buffer: write at least one byte (prevents dead-code elimination, per Section 6) and retain briefly in a small fixed-size ring buffer (proposed: last 64 allocations) before the reference is dropped, to produce a realistic allocate-then-release pattern.
3. Implement per-size-class measurement, not a single blended run — the pool's carve-vs-refresh behavior is size-dependent and blending sizes would obscure that.

## Commands

```sh
# placeholder — finalized at Stage 13
./run.sh --runtime=bun --size=16
./run.sh --runtime=bun --size=16384
./run.sh --runtime=node --size=16
./run.sh --runtime=node --size=16384
# ... full size sweep, both runtimes
```

## Warmup

Per Section 6: warmup iteration count chosen empirically per runtime/size-class combination, verified via a flat/declining trend check over the last 10% of warmup before the timed phase begins. Default starting point: 50,000 warmup allocations per size class.

## Repetitions

- Timed iterations per run: 1,000,000 allocations per size class (chosen to produce a stable throughput measurement while keeping total run time reasonable across 6 size classes × 2 runtimes × ≥10 runs).
- Independent runs: minimum 10 per runtime/size-class combination.
- Outliers: not discarded by default; if GC-pause-driven outliers are observed and a documented trimming is applied, it is applied in addition to, not instead of, the untrimmed numbers.

## Metrics

- **Primary:** allocations/sec, per size class.
- **Secondary:** wall-clock time for the fixed 1M-allocation run; RSS and peak RSS (via OS-level measurement, `/usr/bin/time -v` or platform equivalent); GC-pause/activity counters where each runtime exposes reliable instrumentation (Bun: JSC heap-stats APIs if available and trustworthy; Node: `--expose-gc` plus `v8.getHeapStatistics()`), reported with an explicit note on instrumentation trust level per Section 5.

## Statistical method

Median + mean + stddev + CV across ≥10 independent runs per size class, per Section 5.

## Expected result (directional only)

If M20's mechanism holds, Node should show higher allocations/sec than Bun, especially at smaller size classes where pool-carving amortizes best. **Both outcomes (Node wins, or Bun performs equivalently/better) are treated as informative — this experiment was deliberately kept in MUST RUN partly because its predicted direction runs counter to the popular "Bun allocates less than Node" claim.**

## Falsifier

No meaningful Node advantage at any tested size, or Bun performs equivalently or better across the size sweep.

## Confounders / risks

- GC scheduling differences between JSC and V8 could introduce noise unrelated to the allocation mechanism itself — the ring-buffer retention pattern (Setup step 2) is designed to produce a realistic, not adversarial, GC interaction, but this should be revisited if results look GC-dominated rather than allocator-dominated.
- Deno's exclusion (pending open item 26) means this experiment cannot yet speak to a full three-way comparison.
