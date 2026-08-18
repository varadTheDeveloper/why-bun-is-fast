# H5 — Buffer Allocation Pooling Asymmetry (Bun vs Node)

## Experiment

Tests M20: whether Node's pooled `Buffer.allocUnsafe(size)` path provides a
measurable allocation-throughput advantage over Bun's fresh-allocation path
(`JSC::JSUint8Array::createUninitialized()`) — or the reverse, or neither.
Bun vs Node only; Deno excluded per protocol (open item 26 not positively
resolved).

## Hypothesis (H5)

Node's buffer pooling gives it a measurable allocations/sec advantage over
Bun for small buffer sizes, because Node can hand out a slice of a
pre-allocated pool instead of performing a fresh heap allocation, while Bun
always performs a fresh allocation regardless of size.

**Falsifier (declared before running):** No meaningful Node advantage at
any tested size, or Bun performs equivalently or better across the tested
size sweep.

## Source mechanism

**Bun** (`src/jsc/bindings/JSBuffer.cpp`, pinned commit `8326d1bd...`):
`jsBufferConstructorFunction_allocUnsafeBody()` unconditionally calls
`createUninitializedBuffer()`, which calls
`JSC::JSUint8Array::createUninitialized(lexicalGlobalObject, subclassStructure, length)`.
There is no pool/threshold branch — every `Buffer.allocUnsafe(size)` call
performs a fresh allocation regardless of size.

**Node** (`lib/buffer.js`): `Buffer.allocUnsafe(size)` branches on
`size < (Buffer.poolSize >>> 1)`. Below the threshold, it slices from a
shared pre-allocated pool (`allocPool`, refilled via
`createUnsafeAlignedBuffer` when exhausted); at/above the threshold it calls
`createUnsafeBuffer(size)` — a fresh allocation, structurally the same kind
of call as Bun's path.

**Important verified divergence (source pin vs. benchmarked binary):**
The pinned Node commit `ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85`'s
`lib/buffer.js` (fetched directly, HTTP 200, via
`raw.githubusercontent.com` — not cloned/built) sets
`Buffer.poolSize = 64 * 1024`, i.e. a 64KB pool with a **32KB** pooling
threshold. But the actual **benchmarked binary** (release v22.22.2) was
empirically probed and reports `Buffer.poolSize === 8192` — an **8KB** pool
with a **4KB (4096-byte)** threshold, the long-standing Node default. The
pinned commit is evidently a point on Node's main development branch with a
different pool-size constant than the shipped v22.22.2 release actually
benchmarked here. Per the H5 protocol's explicit instruction not to assume
source-pin fidelity without verification, this was checked directly (not
assumed) — see "Benchmark implementation" below for the verification
method — and all quantitative interpretation in this report uses the
**empirically confirmed 4096-byte threshold of the binary actually under
test**, not the pinned commit's 32KB figure.

## Environment

Shared cloud sandbox (2 vCPU Intel Xeon @2.80GHz, KVM guest, Ubuntu
24.04.4, kernel 6.18.5, 7.8GB RAM) — the same sandbox used for H4 and H6,
**not dedicated benchmarking hardware**. Unlike H4/H6, H5 has no HTTP load
generator or Postgres server running concurrently — each timed run is a
short-lived local child process measured via in-process
`process.hrtime.bigint()`, not network round-trips — so H5 is less exposed
to that specific confound, but this is still not claimed to be
authoritative production-grade evidence. Load average at run start was low
(0.51/0.31/0.12) with no other CPU-heavy process observed. Full detail in
`metadata.json`.

## Runtime versions / commits

| | Benchmarked binary | Version | Pinned source commit (mechanism citation only, NOT built/benchmarked) |
|---|---|---|---|
| Bun | release, pre-installed | 1.3.13 | `8326d1bd39a96f1f298c3de195aad15972d4f3b4` |
| Node | release, pre-installed | v22.22.2 | `ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85` |

**Deviation from spec (disclosed):** the H5 protocol designated
source-controlled builds at the exact pinned commits as the *primary*
track. That track was attempted for Bun and found infeasible in this
sandbox: Bun's `rust-toolchain.toml` requires Rust
`nightly-2026-07-20`, and `rustup toolchain install` failed —
`error downloading file from 'https://static.rust-lang.org/dist/2026-07-20/channel-rust-nightly.toml.sha256' ... tunnel error: unsuccessful`.
Verified independently: `curl -s -o /dev/null -w '%{http_code}' https://static.rust-lang.org/`
returned `000` (hard connection failure, not an HTTP error) — a real,
verified network restriction in this environment, not an assumption.
Because a from-source Bun build was infeasible, **both** runtimes were
benchmarked as release builds (the same consistent track on both sides),
matching the approach already used and user-approved for H4 and H6. The
pinned source commits were still used, unmodified, purely to characterize
the M20 mechanism (see above), with the source-vs-binary check performed
explicitly rather than assumed.

## Benchmark implementation

`benchmark/alloc-bench.js` — a single script run identically (no
runtime-specific branches, except the final version-report field) under
both `bun` and `node`. Per invocation: allocates `Buffer.allocUnsafe(size)`
in a loop, writes an observable, non-constant byte to the first (and, for
size>1, last) byte position — XORed against a running accumulator and the
loop index so neither JSC's nor V8's JIT can constant-fold or dead-code-
eliminate the write — retains the buffer in a bounded ring buffer
(`ringSize=4096` entries) so the oldest allocation falls out of scope only
after ~4096 iterations (preventing a trivial "allocate and instantly
discard" pattern the JIT might special-case), and reports
`iterations / (elapsed_ns / 1e9)` as `allocsPerSec`.

**Pooling verification (empirical, on the actual benchmarked binaries):**
before trusting either source-read description, both runtimes' actual
`Buffer.allocUnsafe()` behavior was probed directly: allocate 5 consecutive
buffers of a given size, compare `.buffer` (the underlying `ArrayBuffer`)
identity across them.

- **Node v22.22.2**: sizes 16/64/256/1024 → all 4 consecutive buffers share
  one 8192-byte `ArrayBuffer` (pooled). Sizes 4096/4097/16384 → each buffer
  gets its own distinct `ArrayBuffer` sized exactly to the request (not
  pooled). Confirms the pooling threshold is exactly 4096 bytes on this
  binary.
- **Bun 1.3.13**: sizes 16/64/1024/4096/16384 → 0/4 pooled at every size
  tested; every allocation returns a distinct, freshly-sized `ArrayBuffer`.
  Confirms no pooling at any tested size, consistent with the source read.

## Buffer sizes

16, 64, 256, 1024, 4096, 16384 bytes — fixed in advance per protocol, no
additions. (These happen to bracket the empirically-confirmed 4096-byte
Node pooling threshold on both sides — sizes 16/64/256/1024 fall inside the
pooled regime, 4096/16384 fall outside it — but this was not known when the
sizes were chosen; the sweep was specified by the user before any
measurement.)

## Warmup

Started at 50,000 allocations per runtime×size, split into 10 timed
chunks. A stability check — written into `orchestrate.py` **before** the
experiment ran and not modified afterward — compares the mean per-chunk
time of the last 5 chunks against the first 5: stable requires
`|relative_change| <= 0.15` AND last-half CV `<= 0.25`. If not stable,
warmup was extended (iteration count doubled), up to 3 extensions
(50k → 100k → 200k → 400k).

**Outcome (disclosed, not hidden):** only 2 of 12 combos (`bun-64`,
`bun-1024`) reached the declared STABLE state within the bounded
extensions. The other 10 hit `UNSTABLE_AFTER_MAX_EXTENSIONS` at 400,000
warmup iterations. Inspecting the raw chunk timings (`raw/warmup_log.json`)
shows the instability is driven almost entirely by a large **first-chunk
outlier** in most warmup attempts (consistent with process/JIT cold-start
cost concentrated at the very start of the first chunk) — the **last-half
CV was frequently low** (many combos at 0.03–0.09), meaning the tail of
each warmup attempt was reasonably flat even when the first-half/last-half
drift comparison failed the declared threshold. This is reported as a
limitation of the predefined check's sensitivity to a single early outlier
chunk, not as evidence that steady state was never reached — but per
protocol the check is reported exactly as specified and was not loosened
after seeing this pattern. All timed runs used a completely fresh process
(no shared warmup state between runs), so warmup here refers only to each
combo's *warmup measurement*, used purely to characterize the JIT/process
startup profile of that runtime×size — it is not the mechanism by which
timed runs get their JIT/OS caches warmed.

## Repetitions

1,000,000 allocations per timed run, single chunk, fresh process per run.
10 independent runs × 2 runtimes × 6 sizes = **120 timed runs**. All 120
completed successfully (0 failed runs). No run count was reduced or
increased selectively after seeing results.

## Raw-data integrity

Every one of the 120 timed runs preserved individually as
`raw/<runtime>-size<size>-run<NN>.json`, plus a consolidated
`raw/run_index.json` (all 120 entries) and `raw/warmup_log.json` (every
warmup attempt for all 12 combos, including the unstable ones). No run was
overwritten, deleted, or silently replaced. Benchmark source
(`alloc-bench.js`, `orchestrate.py`, `analyze.py`) preserved in
`../benchmark/`.

## Results

| Buffer size | Bun alloc/s (median) | Node alloc/s (median) | Faster runtime | Relative difference (Bun vs Node) | Bun CV | Node CV | Notes |
|---|---|---|---|---|---|---|---|
| 16 B | 11,801,970 | 13,260,126 | **Node** | −11.0% | 6.1% | 5.1% | Both pooled (Node) / fresh (Bun) |
| 64 B | 9,578,974 | 11,782,221 | **Node** | −18.7% | 3.3% | 4.0% | Both pooled (Node) / fresh (Bun) |
| 256 B | 6,983,603 | 7,587,909 | **Node** | −8.0% | 3.6% | 6.0% | Both pooled (Node) / fresh (Bun) |
| 1024 B | 2,829,340 | 3,647,748 | **Node** | −22.4% | 7.3% | 3.3% | Both pooled (Node) / fresh (Bun) |
| 4096 B | 2,524,779 | 835,888 | **Bun** | **+202.0%** | 4.9% | 5.7% | Node crosses threshold to fresh alloc here |
| 16384 B | 2,203,141 | 568,822 | **Bun** | **+287.3%** | 3.5% | 6.5% | Both fresh allocation |

Reversal is NOT hidden or averaged away: Node is faster at every pooled
size (16–1024 B, −8% to −22%), and Bun is dramatically faster at every
unpooled size (4096–16384 B, +202% to +287%). This is a clean,
threshold-aligned reversal, not a monotonic trend in either direction.

## Statistical summary

All values median/mean/stddev/CV/95% CI (t-distribution, df=n−1) computed
over n=10 independent fresh-process runs per combo; full raw values and CI
bounds in `summary.json`. No outliers were discarded at any point (no
trimming rule was ever declared or applied).

| Combo | Median allocs/s | Mean | StdDev | CV | 95% CI (mean) |
|---|---|---|---|---|---|
| bun-16 | 11,801,970 | 11,700,717 | 713,753 | 6.1% | [11,190,164 – 12,211,269] |
| bun-64 | 9,578,974 | 9,630,439 | 320,131 | 3.3% | [9,401,447 – 9,859,431] |
| bun-256 | 6,983,603 | 6,972,470 | 252,584 | 3.6% | [6,791,795 – 7,153,145] |
| bun-1024 | 2,829,340 | 2,778,443 | 203,554 | 7.3% | [2,632,839 – 2,924,047] |
| bun-4096 | 2,524,779 | 2,533,021 | 122,935 | 4.9% | [2,445,085 – 2,620,957] |
| bun-16384 | 2,203,141 | 2,190,629 | 76,658 | 3.5% | [2,135,795 – 2,245,463] |
| node-16 | 13,260,126 | 13,156,582 | 671,781 | 5.1% | [12,676,052 – 13,637,112] |
| node-64 | 11,782,221 | 11,886,633 | 477,580 | 4.0% | [11,545,016 – 12,228,249] |
| node-256 | 7,587,909 | 7,651,605 | 459,845 | 6.0% | [7,322,674 – 7,980,536] |
| node-1024 | 3,647,748 | 3,667,674 | 119,843 | 3.3% | [3,581,949 – 3,753,399] |
| node-4096 | 835,888 | 834,680 | 47,843 | 5.7% | [800,458 – 868,902] |
| node-16384 | 568,822 | 562,852 | 36,777 | 6.5% | [536,545 – 589,159] |

None of the six sizes have overlapping 95% CIs between Bun and Node — every
size shows a statistically distinguishable difference in this sample, in
the direction consistent with the pooled-vs-unpooled explanation above.

### Secondary metric: peak RSS (kB, median across 10 runs)

| Size | Bun peak RSS | Node peak RSS |
|---|---|---|
| 16 | 43,458 | 70,080 |
| 64 | 43,078 | 74,076 |
| 256 | 45,240 | 89,394 |
| 1024 | 54,702 | 107,962 |
| 4096 | 91,276 | 144,194 |
| 16384 | 140,884 | 242,152 |

Node's peak RSS is consistently higher than Bun's at every tested size —
directionally consistent with Node's pool retaining a full pool-sized
backing buffer (and general baseline Node process overhead) even for small
requested sizes, though this was not isolated from general baseline
runtime memory footprint and should not be over-interpreted as solely
pooling-attributable.

GC activity/pause instrumentation was **not** collected (see
`metadata.json` → `gc_instrumentation_note`): Node's and Bun's GC
instrumentation are not directly equivalent, and forcing a comparison on
non-equivalent counters was judged worse than omitting it, per the H5
protocol's own caution on this point.

## Falsification status

**NOT SUPPORTED** — for the full tested size range as a single claim.
Node does NOT have a uniform advantage across all six sizes; the advantage
is confined to the pooled regime (sizes below the empirically-confirmed
4096-byte threshold on the benchmarked binary). Framed as declared, the
falsifier ("no meaningful Node advantage at any tested size, or Bun
performs equivalently or better across the tested size sweep") is
partially triggered: Bun performs *better*, not equivalently, across half
the sweep (4096, 16384 B) — which is exactly the falsifier's second
disjunct. The hypothesis as originally framed (a general Node pooling
advantage) is **NOT SUPPORTED** as a blanket claim; a **narrower,
threshold-conditional** version of it — Node's pool gives it a measurable
advantage specifically for buffer sizes below its allocation threshold,
and loses decisively once a request crosses that threshold — **is
supported** by this data.

## What this supports

- Node's `Buffer.allocUnsafe()` pooling provides a real, measurable,
  statistically distinguishable allocations/sec advantage over Bun **for
  buffer sizes below the empirically-confirmed 4096-byte pooling
  threshold of the benchmarked v22.22.2 binary** (8–22% faster across
  16/64/256/1024 B, CIs non-overlapping).
- Once a requested size reaches or exceeds that threshold, Node must
  perform a fresh allocation just like Bun does at every size — and at
  that point Bun's fresh-allocation path is measurably, substantially
  faster than Node's fresh-allocation path (+202% at 4096 B, +287% at
  16384 B, CIs non-overlapping).
- The reversal aligns cleanly with the independently, empirically verified
  pooling threshold (not merely with a source-read assumption) — this is
  a mechanism-consistent result, not just a correlation.

## What this does NOT support

- This does **not** establish that "Node is faster than Bun" in general —
  the direction of the effect fully reverses depending on whether the
  workload's buffer sizes fall inside or outside Node's pooling range, and
  a realistic workload's size distribution determines which regime
  dominates.
- This does **not** establish that "Bun's mimalloc/native allocator is
  faster than V8's allocator" — M19 distinguishes Bun's native
  allocator/mimalloc from JSC's JS-heap allocation path, and this
  experiment measured JS-heap-level `Buffer.allocUnsafe()` throughput, not
  mimalloc directly. The mechanism traced here is
  `JSC::JSUint8Array::createUninitialized()`, a JSC JS-heap allocation
  call — this experiment does not isolate whether or how mimalloc
  contributes to that path's underlying speed.
- This does **not** establish that the specific 32KB pooling threshold
  described in the pinned Node development commit is what a production
  Node deployment on a different release would exhibit — the empirically
  verified 4096-byte threshold applies specifically to the benchmarked
  v22.22.2 release binary.

## Surprising findings

- The magnitude of Bun's advantage above the pooling threshold (+202% to
  +287%) is far larger than the magnitude of Node's advantage below it
  (−8% to −22%) — the asymmetry is not symmetric. Node's fresh-allocation
  path (`createUnsafeBuffer`) appears substantially slower in absolute
  terms than Bun's fresh-allocation path, independent of pooling.
- The pinned Node source commit's `Buffer.poolSize` constant (64KB) does
  NOT match the actual benchmarked release binary's constant (8KB) — a
  concrete, verified instance of exactly the source/binary divergence risk
  the H5 protocol warned about, this time on the Node side rather than the
  Bun side (where the earlier H4 experiment's source/binary distinction
  was about the binary not being built from the pinned commit at all,
  not about a *value* differing between an unbuilt pinned commit and a
  shipped release).
- The predefined warmup-stability check almost never returned STABLE
  (10/12 combos), apparently driven by a single early-chunk outlier rather
  than genuine ongoing drift (see "Warmup" above) — worth flagging as a
  methodology note for any future chunked-warmup stability check design,
  without retroactively changing this run's check.

## Counter-evidence

This result is itself partial counter-evidence against an unqualified
"Bun allocates faster than Node" narrative: for four of the six tested
sizes (16, 64, 256, 1024 bytes — plausibly the more common size range for
many real workloads, e.g. small JSON payloads, short strings, small
protocol headers), **Node is measurably faster**, not Bun. Any article
claim about Bun's allocation performance should state the pooling-
threshold-dependent nature of this result rather than a blanket "Bun
allocates faster."

## Confounders / limitations

- Shared 2-vCPU cloud sandbox, not dedicated hardware (though less exposed
  to network/DB-load confounds than H4/H6, per the "Environment" section
  above).
- Release builds for both runtimes, not the pinned source commits (see
  "Runtime versions / commits" — a disclosed, consistent-track deviation).
- GC pause/activity instrumentation not collected (disclosed above).
- Peak RSS captured via `/usr/bin/time -v`'s "Maximum resident set size,"
  a whole-process measurement that includes runtime baseline overhead
  (e.g. Node's larger baseline footprint from V8/npm-related
  initialization) as well as buffer-pool-attributable memory — not a
  pooling-isolated memory metric.
- Warmup stability was not reached by the predefined check for 10/12
  combos (disclosed above, with the likely first-chunk-outlier
  explanation) — this affects confidence in "true JIT steady state" being
  reached before timed measurement, though the CVs of the *timed* runs
  themselves (3.3%–7.3%) are consistently low, suggesting the timed
  measurements were themselves reasonably stable even if the formal
  warmup-stability criterion was not met.
- Single-machine, single-session sample — no cross-machine replication.

## Data-quality assessment (12-item check)

1. Fixed hardware/OS across both runtimes: **YES** (same VM, same run
   session).
2. Dedicated (non-shared) hardware: **NO** — shared sandbox (disclosed).
3. Identical benchmark code across runtimes (no runtime-specific
   branching beyond version-reporting): **YES**.
4. Buffer sizes fixed in advance, no post-hoc additions: **YES**.
5. Dead-code-elimination-resistant workload (observable write + retention):
   **YES** — verified via non-pooled, size-matched `.buffer` allocation on
   every timed iteration; accumulator/loop-index-dependent write cannot be
   constant-folded.
6. JIT/warmup control present: **PARTIAL** — warmup was performed and
   extended per a predefined rule, but the declared STABLE state was
   reached for only 2/12 combos (disclosed).
7. Minimum run count met (≥10 independent fresh-process runs per combo):
   **YES** — exactly 10 for all 12 combos, 120 total, 0 failed.
8. No outlier removal without a predeclared rule: **YES** — no trimming
   was ever applied.
9. Primary and secondary metrics both reported (not cherry-picked):
   **YES** — allocs/sec (primary) and peak RSS (secondary) both reported
   for every size; wall-clock also preserved in raw data.
10. Source-controlled build track used exactly as specified: **NO** —
    release-build fallback used for both runtimes (disclosed, with
    verified reason).
11. Source-pin-vs-binary fidelity actively verified rather than assumed:
    **YES** — this check specifically surfaced the Node poolSize
    divergence documented above.
12. Falsifier evaluated honestly, not adjusted after seeing results:
    **YES** — the declared falsifier's second disjunct ("Bun performs
    equivalently or better") is what actually triggered for half the
    sweep, and this is reported as NOT SUPPORTED for the blanket
    hypothesis rather than reframed as a clean win.

**Classification: PILOT / LIMITED.** Item 2 (shared hardware) and item 10
(release-build fallback instead of source-controlled) are both violated,
consistent with H4/H6's precedent; this is not authoritative,
production-grade evidence. However, item 6's partial failure is offset by
the low CVs of the actual timed runs (3.3%–7.3%) and by the non-overlapping
95% CIs at every size, so the core qualitative finding (threshold-aligned
reversal) is reasonably well-supported by this pilot's internal
consistency even though the run does not meet every quality bar.

## Evidence Map impact (recommendation only — not applied to evidence-map.md pending review)

Recommended targeted update to **M20 only**:
**MIXED** — supports magnitude in both directions depending on buffer size
relative to Node's pooling threshold; does NOT support a blanket "Node
pooling beats Bun" or "Bun beats Node" framing. Do not modify M2, M9, M15,
M16, M17, M18, or M19 based solely on H5.

## Recommendation for H3

No specific dependency identified between H5's findings and H3's scope from
this experiment; H5 was self-contained (Bun vs Node buffer allocation
only). Proceed to H3 per the standing sequence (H6 ✅ → H4 ✅ → H5 ✅ → H3 →
H2 → H1 → H7 deferred) once this report is reviewed.

## Exact run commands

```
python3 benchmark/orchestrate.py   # full 120-run protocol
python3 benchmark/analyze.py       # statistics + result table
```

Per-run invocation (as issued by orchestrate.py):
```
/usr/bin/time -v <bun|node> alloc-bench.js <size> <iterations> 4096 <warmup|timed>
```
