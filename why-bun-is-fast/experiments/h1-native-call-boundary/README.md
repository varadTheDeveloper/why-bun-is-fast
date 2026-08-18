# H1 — Native call boundary overhead

**Status: protocol only. Not yet executed. Classification: SHOULD RUN (see `../../notes/12-experiment-design.md` Section 1).**

## Purpose

Test whether Bun's JSC-native call boundary has measurably lower per-call overhead than Node's equivalent V8-native path, for synchronous, high-frequency native calls — the mechanism M2 attributes to JSC's non-moving, conservative-stack-scanning GC removing the need for a tracked `HandleScope`, which V8's moving GC requires.

## Hypothesis (H1, from Stage 11 — unmodified)

Bun's synchronous JS→native call path has lower per-call overhead than Node's, for calls that would otherwise pay V8's handle-scope construction/teardown cost.

## Mechanism

M2 (`evidence/evidence-map.md`). Two independent source confirmation sites: general native-call binding code (Stages 3/5) and `node_http_parser.cc`'s `HandleScope` construction at multiple llhttp-adjacent callback sites (Stage 8).

## Design-risk resolution (read this before anything else)

Per Stage 12 Section 7: **the first task of this experiment, before any timing code is written, is determining whether a genuinely equivalent native operation can be built across all three runtimes.** Candidate operation: a no-op native function accepting one integer argument and returning it incremented by one, implemented via:

- **Bun:** a native binding using Bun's normal JSC binding mechanism (see `implementing-jsc-classes-cpp`/`implementing-jsc-classes-rust` skills in the Bun repo for the current binding pattern).
- **Node:** either an existing Node built-in with an equivalent trivial signature, or a minimal N-API addon built specifically for this experiment that exercises the same general call convention a real built-in would use (N-API is the fairest choice since it's Node's own supported native-binding path, not an artificial shortcut).
- **Deno:** two variants — one using `op2`'s Fast API path (eligible signature: plain integer in, integer out) and one deliberately using a signature known to fall outside Fast API eligibility (e.g., involving a struct or non-Latin1 string), to capture both Deno's optimized and unoptimized native-call cost rather than just one.

**If this three-way equivalence cannot be established to a standard both engineers on this project would sign off on, this experiment reports a two-way Bun-vs-Node comparison with Deno's numbers presented separately and explicitly not compared apples-to-apples.** This decision must be made and documented in this file (append the outcome here) before Stage 13 timing runs begin — not discovered partway through execution.

## Runtime versions / commits

- **Track used:** source-controlled comparison (this experiment tests a specific, line-level-traced mechanism; the release track is a secondary cross-check, not primary).
- **Bun:** `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4` (existing project pin).
- **Node:** `nodejs/node@ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85` (existing project pin).
- **Deno:** **BLOCKED.** No reproducible commit pin exists yet (Stage 11 open item 27). Must be resolved — a full 40-character SHA recorded here — before this experiment's Deno leg can run. Until resolved, this experiment can proceed Bun-vs-Node only.
- Record actual `bun --version` / `node --version` / `deno --version` and, for any source-controlled build, `git rev-parse HEAD`, into `metadata.json` at execution time — do not rely on the pins above without re-confirming the build actually used them.

## Hardware / environment

Per `../../notes/12-experiment-design.md` Section 4. **This experiment is timing-sensitive at the nanosecond scale and is the single most noise-vulnerable experiment in the set — it should not be run on a shared/virtualized environment without explicitly widened error bars, and ideally not at all until a dedicated machine is available.** Record CPU model, governor/power mode, and background load explicitly; prefer a machine with CPU frequency scaling disabled (fixed performance governor) if available.

## Setup

1. Resolve the design-risk question above (equivalence check) and document the outcome in this file.
2. Resolve the Deno commit pin, or proceed Bun-vs-Node only and note that explicitly in results.
3. Build each runtime at its pinned commit (source-controlled track) per that repo's own build instructions (Bun: `bun bd`; Node: standard `configure`/`make`; Deno: `cargo build --release`).
4. Implement the benchmark harness using `mitata` (or an equivalently trusted micro-benchmarking tool with documented optimization-barrier support) for the JS-side timing loop, per Stage 12 Section 6.

## Commands

```sh
# placeholder — finalized once benchmark/ is implemented at Stage 13
./run.sh --runtime=bun
./run.sh --runtime=node
./run.sh --runtime=deno-op2
./run.sh --runtime=deno-slowpath   # only if the equivalence check allows it
```

## Warmup

Per Stage 12 Section 6: warmup iteration count is chosen empirically per runtime by verifying that per-iteration timing over the last 10% of the warmup phase is flat or still declining (not yet increasing/noisy in a way suggesting de-optimization) before the timed phase begins. This check is logged into the raw warmup data, not just asserted. A default starting point of 100,000 warmup iterations is used, extended if the flatness check fails.

## Repetitions

- Timed iterations per run: 10,000,000 calls (chosen to push per-call cost well above the timer's own resolution floor when amortized, per standard microbenchmark practice — not an arbitrary round number chosen for appearance).
- Independent runs (separate process invocations): minimum 10 per runtime/variant, to capture run-to-run (not just iteration-to-iteration) variance.
- Outliers: not discarded by default; full raw distribution preserved in `raw/raw.csv`.

## Metrics

- **Primary:** nanoseconds per call (median across the 10M-call run, reported per independent run, then aggregated across runs).
- **Secondary:** calls/sec, standard deviation, coefficient of variation, p95 (flagged as possibly tool-resolution-limited at this timescale — report the caveat alongside the number, not instead of it).

## Statistical method

Per `../../notes/12-experiment-design.md` Section 5: median primary, mean + stddev + CV secondary, minimum 10 independent runs before any cross-run statistic (including a CI, if reported) is computed.

## Expected result (directional only — not a number)

If M2's mechanism story holds, Bun's per-call time should be lower than Node's for this operation, with Deno's `op2`-fast-path numbers closer to Bun's than to Node's slow-path numbers, and Deno's slow-path numbers closer to Node's. No specific magnitude is predicted.

## Falsifier

No statistically meaningful difference between Bun's and Node's per-call time after controlled repetition (confidence intervals overlap substantially beyond what the measurement tool's own noise floor would explain).

## Confounders / risks

- Three-way binding equivalence may not be achievable (see Design-risk resolution above) — the single largest risk to this experiment's validity.
- Deno commit pin currently missing (blocking for the Deno leg specifically).
- Nanosecond-scale timing is highly sensitive to host-level noise (see Hardware section) — this experiment should be treated as higher-risk for producing an inconclusive result than H3, H4, or H5.
