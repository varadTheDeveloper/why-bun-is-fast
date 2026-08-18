# H1 — Native JS→Native Call Boundary

## Experiment

Tests M2: does Bun's synchronous JS→native boundary have measurably lower
per-call overhead than Node's equivalent native path? The original
source-level hypothesis relates this to JSC's non-moving/conservative-
stack-scanning GC (no handle-scope bookkeeping needed) versus V8's moving
GC (requiring every native `Local<T>` reference to live inside a tracked
`HandleScope`). This is the final planned Stage 13 experiment.

## Hypothesis (H1)

> Bun's synchronous JS→native boundary has lower per-call overhead than
> Node's equivalent native path.

**Prediction:** Bun may show lower nanoseconds/call than Node. Deno's Fast
API path may be closer to Bun than Deno's non-fast path. No exact
magnitude predicted in advance.

**Falsifier (declared before running):** No statistically meaningful
difference between Bun and Node after controlled repetition and
control-subtraction of benchmark-loop overhead.

## Source mechanism

`node_file.cc` on `nodejs/node@main` (`HandleScope scope(isolate);` in
`Access`, `ExistsSync`, etc.) and `src/node_http_parser.cc` (same pattern
on the HTTP-parsing path) show V8 requiring per-call handle-scope
construction. Bun's `src/jsc/CallFrame.rs`/`src/jsc/host_fn.rs` and JSC's
documented conservative-stack-scanning design remove that requirement.
Deno's `op2` Fast API layer is documented (WebKit/V8 GC blog posts,
Bun engineering blog) as a deliberate V8-side engineering workaround for
this exact cost, for eligible call signatures — meaning V8's handle-scope
tax is avoidable in the common case, not an absolute tax, which is the
central reason this experiment includes a Deno fast/non-fast pair rather
than treating "V8 = slow" as a foregone conclusion.

## Equivalence validation

**Candidate operation:** `int32 in → native function → increment → int32
out`, implemented as `return x + 1;` in both the FFI shared library
(`native.c`) and the N-API addon (`napi_addon.c`, with N-API's own
required `napi_get_value_int32`/`napi_create_int32` marshaling calls
around the same logic).

A genuine three-way, single-technology equivalent operation was **not**
achievable: Node has no built-in dlopen-style FFI without a third-party
package, and Deno has no N-API/native-addon compatibility. Per Section 4,
this is handled as follows rather than manufacturing a fake three-way
comparison:

- **PRIMARY COMPARISON: Bun vs Node, each via its own normal/actual
  native-call mechanism** — `bun:ffi` for Bun (Bun's own documented FFI
  system), N-API native addon for Node (Node's standard, ABI-stable,
  most-used native-addon mechanism — chosen over a raw V8 addon
  specifically because it doesn't require pinning to an exact V8 version,
  and is genuinely what most real-world Node native modules use).
- **SUPPLEMENTARY SAME-BINARY CONTROL:** the identical compiled
  `napi_addon.node` binary loaded by both Node (natively) and Bun (via
  Bun's own Node-API compatibility layer, confirmed present via source —
  `src/jsc/bindings/napi.cpp`, `node_api.h`, `js_native_api.h` in the
  pinned Bun clone). This is the strongest possible equivalence
  (byte-identical machine code executed by both runtimes' loaders), at
  the cost of testing Bun's N-API **emulation** overhead specifically,
  not Bun's fastest normal path.
- **DENO: presented separately, explicitly NOT APPLES-TO-APPLES** against
  the N-API rows (`Deno.dlopen()` is a different binding technology).
  Two internally-comparable sub-variants: `fast` (i32, documented as V8
  Fast-API-eligible) and `nonfast` (i64/BigInt, documented as NOT
  Fast-API-eligible since V8's Fast API doesn't support 64-bit integers
  without boxing) — both fully synchronous (no `nonblocking: true`),
  isolating the *type-driven* fast-path eligibility specifically, not
  conflating it with async dispatch. Loosely comparable to `bun:ffi` as
  both are dlopen-based FFI systems, though this specific relationship
  was not independently source-verified (disclosed below).

Argument/return type, native function body, JS call structure
(`sink = fn(i)` in a for-loop, matching Section 6's example exactly),
optimization barriers (sink printed in output, verified non-zero and
consistent with the expected computation in every run), and thread/async
behavior (fully synchronous, no cross-thread hop, unlike H2) were all
checked and matched or explicitly documented as differing — full detail
in `metadata.json` → `equivalence_validation`.

**Disclosed open uncertainty:** whether N-API's `napi_create_int32`
return-value boxing allocates on the heap in either engine (vs.
small-integer/SMI-style tagging avoiding allocation) was not independently
verified at the engine-internals level this session.

**Disclosed methodology gap:** Deno's fast/non-fast classification is
based on documented Deno/V8 Fast API type support, not a source-level
trace of Deno's FFI fast-call dispatch logic (Deno's source was not
cloned for this check) — corroborated empirically instead (see Results).

## Runtime implementations

| Runtime | Mechanism | Version | Track |
|---|---|---|---|
| Bun | `bun:ffi` (own normal binding) | 1.3.13 | release |
| Bun | N-API compat (same binary as Node row) | 1.3.13 | release |
| Node | N-API native addon | v22.22.2 | release |
| Deno | `Deno.dlopen()`, fast + non-fast | 2.9.5 | release |

Release builds for all three — the same disclosed fallback used for
H3/H4/H5/H6 (Bun's pinned-commit Rust nightly toolchain is unreachable in
this sandbox, verified in H5, unchanged here).

## Control benchmark

Per Section 11: each variant has a `control` mode running the identical
loop structure with **no native call** — `sink = (i & 0x7fffffff) + 1 |
0`, the same logical operation performed in pure JS. Derived native-call
overhead = test median ns/call − that variant's own control median
ns/call (not a single shared control across all variants, though the
control loop body is byte-identical across every script).

## Warmup

Chunked stability check (10 chunks per attempt, extended up to 12.8M
iterations across 7 bounded extensions), requiring **two consecutive
passing attempts** before declaring STABLE — the same refined design H2
introduced after finding a single-attempt check could be fooled by an
intermediate JIT-tiering plateau. All 90 official runs reached STABLE;
none hit the extension ceiling. Final warmup iteration counts varied
naturally by variant (400K–12.8M), not tuned.

## Timed methodology

10,000,000 timed calls per run, split into 10 chunks (1,000,000 calls
each), each chunk timed via a single `process.hrtime.bigint()` pair — the
timer itself is invoked only 20 times per run (10 chunk-starts + 10
chunk-ends), not once per call, amortizing timer overhead to a negligible
fraction of the measured time (Section 8).

**Timer-resolution disclosure:** control-mode measurements (~0.45–0.9ns/
call) show much higher relative variance (CV 25%–37%) than test-mode
measurements (CV 3.6%–6.3%), because the absolute magnitude being
measured in control mode is extremely close to the practical noise floor
of chunked timing on shared, non-isolated cores. This is disclosed, not
hidden — the derived overhead figures remain meaningful because test-mode
absolute magnitudes (5–82ns) are far above this floor.

## Repetitions

9 combos (5 test variants + 4 controls of which 1 control is shared by
name only — actually 4 distinct control combos, one per binding-family/
runtime-pair, plus 5 test-mode combos = 9 total) × 10 independent
fresh-process runs × 10,000,000 calls = 90 total runs, 0 failures.

## Raw-data integrity

Every run preserved individually as `raw/<combo_id>-run<NN>.json`
(90 files, including full warmup-attempt history and per-chunk timings),
plus `raw/run_index.json`. Benchmark source (`native.c`, `napi_addon.c`,
`napi-bench.js`, `bun-ffi-bench.js`, `deno-ffi-bench.js`, `orchestrate.py`,
`analyze.py`) preserved in `../benchmark/`.

## Results

| Runtime | Variant | Control ns/op | Native-test ns/op | Derived native overhead | Calls/sec | CV | Comparable group |
|---|---|---:|---:|---:|---:|---:|---|
| Bun | `bun:ffi` (normal binding) | 0.479 | 15.017 | **14.538** | 66,591,375 | 4.5% | **PRIMARY** |
| Node | N-API native addon | 0.765 | 22.869 | **22.105** | 43,727,262 | 6.3% | **PRIMARY** + N-API same-binary group |
| Bun | N-API compat (same binary as Node row) | 0.452 | 82.353 | **81.901** | 12,143,923 | 3.6% | N-API same-binary group |
| Deno | Fast API (i32) | 0.701 | 4.979 | **4.278** | 200,843,603 | 5.9% | NOT apples-to-apples vs N-API rows |
| Deno | non-fast path (i64/BigInt) | 0.701 | 24.685 | **23.984** | 40,510,911 | 5.8% | NOT apples-to-apples vs N-API rows; within-Deno comparison |

## Statistical analysis

95% CI (t-distribution, df=9) on the 10 per-run `nsPerCall` medians:

| Combo | Mean ns/call | 95% CI |
|---|---:|---|
| bun-ffi-test | 15.002 | [14.518 – 15.485] |
| node-napi-test | 23.369 | [22.314 – 24.424] |
| bun-napi-test | 82.100 | [79.960 – 84.240] |
| deno-ffi-fast | 5.061 | [4.846 – 5.277] |
| deno-ffi-nonfast | 25.066 | [24.021 – 26.111] |

- **`bun-ffi-test` vs `node-napi-test`:** non-overlapping ([14.5–15.5] vs
  [22.3–24.4]) — the PRIMARY comparison shows a clean, statistically
  robust gap in H1's predicted direction (Bun lower).
- **`bun-napi-test` vs `node-napi-test`:** non-overlapping in the
  **opposite** direction ([80.0–84.2] vs [22.3–24.4]) — on the identical
  compiled binary, Bun's N-API compat layer is ~3.6× **slower** than
  Node's native N-API path.
- **`deno-ffi-fast`** has the lowest CI of any tested variant
  ([4.8–5.3]), non-overlapping with (below) `bun-ffi-test`.
- **`deno-ffi-nonfast` vs `node-napi-test`:** CIs are close and border on
  overlapping ([24.0–26.1] vs [22.3–24.4]) — not a clean separation,
  unlike the other comparisons.
- **`deno-ffi-fast` vs `deno-ffi-nonfast`:** non-overlapping
  ([4.8–5.3] vs [24.0–26.1]) — a large, clean, within-Deno confirmation
  that the two variants exercise meaningfully different binding paths,
  corroborating the documented fast/non-fast type distinction
  empirically (see "Equivalence validation" disclosure above).

## Falsification status

**MIXED — cannot be reported as a single verdict.** Per-comparison:

- **PRIMARY (Bun `bun:ffi` vs Node N-API, each runtime's own normal
  path): SUPPORTED.** A real, statistically robust, non-overlapping gap
  exists in the predicted direction (Bun ≈14.5ns overhead vs Node
  ≈22.1ns overhead — Bun about 34% lower).
- **SAME-BINARY N-API CONTROL (Bun compat vs Node native): NOT SUPPORTED
  — actively reversed.** On identical machine code, Bun is dramatically
  *slower* (≈81.9ns vs ≈22.1ns), not faster.
- **DENO:** Deno's Fast API path (≈4.3ns) has *lower* overhead than
  Bun's own `bun:ffi` (≈14.5ns) — real counter-evidence against a simple
  "Bun's native calls are fastest" narrative, though not directly
  comparable to the N-API rows per the equivalence disclosure above.

## What this supports

- For each runtime's own normal, real-world native-call mechanism, Bun's
  `bun:ffi` measurably outperforms Node's N-API addon path in this
  isolated microbenchmark — a real, reproducible, statistically clean
  result in H1's predicted direction.
- The magnitude and even the *direction* of any Bun-vs-Node native-call
  comparison depends heavily on which binding mechanism is used — the
  identical compiled binary run through Bun's N-API compatibility shim is
  dramatically slower than the same binary run through Node natively.
  This is a genuine, load-bearing nuance: "Bun's native calls are faster"
  is not a mechanism-level (JSC-vs-V8) claim that survives every binding
  path — it is at minimum path-dependent.
- Deno's documented V8 Fast API optimization, when eligible, produces the
  lowest measured overhead of any variant in this experiment — direct,
  clean confirmation that V8's handle-scope tax (M2's original framing)
  is genuinely avoidable via Fast API, not an unconditional V8-wide cost.

## What this does NOT support

- **Does not support "JSC is faster than V8" as a general claim** (per
  the explicit Section 15 interpretation guard). The proper, narrower
  first conclusion is: "this particular native-call benchmark measured
  lower per-call overhead in Bun's tested `bun:ffi` binding path than in
  Node's tested N-API binding path." The same-binary N-API control
  directly demonstrates this does NOT generalize to "any native-call
  mechanism JSC executes is faster than the equivalent V8 mechanism" —
  the opposite was true for the N-API path specifically.
- **Does not establish that the difference is attributable to M2's
  specific proposed mechanism** (JSC's non-moving/conservative GC vs
  V8's handle-scope bookkeeping) as opposed to other implementation
  differences between `bun:ffi`'s and N-API's respective marshaling code
  paths — this experiment measures aggregate call overhead per binding
  mechanism, not GC/handle-scope cost in isolation.
- **Does not establish that this difference survives realistic native
  work** — the tested native function does negligible work (`x + 1`) by
  design; a native function doing meaningful work would dilute any fixed
  per-call boundary overhead as a fraction of total time.
- **Does not establish that Deno's Fast API "erases the gap" in Bun's
  favor** — Deno's fast path actually beats Bun's own `bun:ffi` in this
  measurement, which is the opposite of "erasing a gap in Bun's favor."

## Counter-evidence

Two distinct, real pieces of counter-evidence against an unqualified
"Bun's native-call boundary is faster" claim:

1. **The same-binary N-API control reverses the direction entirely** —
   Bun is ~3.6× slower than Node when forced through a common binding
   mechanism, not faster.
2. **Deno's Fast API path beats Bun's own best mechanism** — 4.3ns
   overhead vs Bun's 14.5ns, meaning even restricting the comparison to
   "each runtime's fastest normal path," Bun is not universally the
   fastest of the three; Deno is, for this specific (Fast-API-eligible)
   signature shape.

## Surprising findings

- The magnitude of Bun's N-API compat penalty (~3.7× the raw call time of
  its own `bun:ffi` path, ~3.6× Node's native N-API time) is far larger
  than the magnitude of Bun's `bun:ffi` advantage over Node's N-API
  (~1.5×) — the "cost of going through the wrong binding path" dwarfs the
  "engine-level advantage" this experiment set out to measure.
- Deno's Fast API path is not just competitive but the outright lowest-
  overhead variant measured, including lower than Bun's own primary
  mechanism — this project's Evidence Map already flagged Deno's `op2`
  Fast API layer as "a genuine, deliberate engineering effort" (M7), and
  H1 provides the first direct benchmark evidence corroborating that it
  actually delivers on that engineering investment at the call-boundary
  level.
- `deno-ffi-nonfast`'s CI came close to overlapping `node-napi-test`'s CI
  — two structurally different "non-optimized" native-call paths (Deno's
  type-disqualified dlopen call and Node's N-API call) land in a similar
  overhead range, suggestive (not proven) that "no special optimization
  applied" native calls may cluster in a similar cost band regardless of
  runtime, while "specifically optimized" paths (Deno Fast API, Bun's own
  FFI) separate cleanly below that band.

## Confounders / limitations

- Shared 2-vCPU cloud sandbox — H1 is explicitly the most noise-sensitive
  experiment in the Stage 13 set; classified PILOT/LIMITED for this
  reason alone regardless of how clean the CIs look.
- Release builds, not source-controlled, for all three runtimes
  (disclosed, consistent with H3/H4/H5/H6's precedent).
- No true three-way single-technology equivalence (disclosed at length
  above) — the PRIMARY comparison itself compares two different binding
  technologies (`bun:ffi` vs N-API), which is a real limitation even
  though it's the fairest available comparison of each runtime's actual
  normal path.
- Whether N-API's int32 boxing allocates was not independently verified
  at the engine-internals level (disclosed above).
- Deno's fast/nonfast classification relies on documented behavior plus
  empirical corroboration, not a source-level trace of Deno's own FFI
  dispatch code (disclosed above).
- The tested native operation is maximally trivial by design (`x + 1`) —
  intentionally isolates the call boundary, but by the same token says
  nothing about how these overheads compare once real native work
  dominates.
- Single-machine, single-session sample; no cross-machine replication.

## Data-quality classification

**PILOT / LIMITED** — driven primarily by shared, non-dedicated hardware
(H1's own protocol explicitly calls this the most noise-sensitive
experiment in the set), and secondarily by the release-build fallback and
the inherent binding-technology non-equivalence discussed at length
above. The underlying statistical signal itself is clean (non-overlapping
CIs for 4 of 5 pairwise comparisons examined, low test-mode CVs of
3.6%–6.3%), but PILOT/LIMITED is the correct classification per the
standing Stage 13 environment rule, not a comment on internal data
quality alone.

## Evidence Map impact (recommendation only — not applied to evidence-map.md pending review)

Recommended targeted update to **M2 only**: **MIXED**. The PRIMARY
comparison (each runtime's own normal binding) supports the predicted
direction and magnitude (Bun lower). The SAME-BINARY N-API control
reverses that direction entirely. The DENO comparison shows a third
mechanism (Fast API) beating both. This is squarely Section 21's "MIXED —
difference depends on binding path or runtime/variant" category, not
SUPPORTS MAGNITUDE (too path-dependent to state as a clean magnitude) and
not NO MATERIAL EFFECT DETECTED (there are large, statistically robust
effects — they just point in different directions depending on
mechanism). Do not modify other M entries based solely on H1.

## Final recommendation

This is the final planned Stage 13 experiment (H6 ✅ → H4 ✅ → H5 ✅ → H3 ✅
→ H2 ✅ → H1 ✅; H7 remains deferred). Per the kickoff instruction, the
automatic experiment sequence stops here — recommend proceeding to Final
Evidence Synthesis (comparing source evidence, all six experiment
results, counter-evidence, benchmark audit, mechanism confidence,
measured magnitude, and workload dependence) before any thesis selection
or article drafting begins.

## Exact benchmark source and run commands

```
python3 benchmark/orchestrate.py   # full 90-run protocol (9 combos x 10 runs)
python3 benchmark/analyze.py       # statistics + result table
```

Native library / addon build (identical flags for both):
```
gcc -shared -fPIC -O2 -o libnative.so native.c
gcc -shared -fPIC -O2 -I<bun-repo>/src/runtime/napi -DNAPI_VERSION=8 -o napi_addon.node napi_addon.c
```

Per-run invocation (as issued by orchestrate.py):
```
node napi-bench.js <control|test> 10000000
bun napi-bench.js <control|test> 10000000
bun bun-ffi-bench.js <control|test> 10000000
deno run --allow-ffi deno-ffi-bench.js <control|fast|nonfast> 10000000
```
