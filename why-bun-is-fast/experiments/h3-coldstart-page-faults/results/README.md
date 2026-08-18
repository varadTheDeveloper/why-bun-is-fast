# H3 — Cold-Start Page-Fault Optimization

## Experiment

Tests M12: does Bun's page-fault-aware binary layout measurably reduce page
faults during cold process startup, for the minimal workload
`console.log("hello")`, compared against Node and Deno under identical
controlled cache-drop conditions?

## Hypothesis (H3)

> Bun's page-fault-aware binary layout contributes measurably to lower
> cold-start page-fault count.

No percentage or exact-magnitude prediction was made in advance, per
protocol.

**Falsifier (declared before running):** No meaningful page-fault-count
difference after controlling for cache state, binary size, linking, and
loader differences.

## Source mechanism

`src/bun_bin/lib.rs` doc comment, pinned commit `8326d1bd...` (quoted
directly, re-verified against the local clone before writing this report):

> "`main()`'s callee chain ... and everything `bun run` reaches under it —
> sits on the cold-start critical path: each call can fault a fresh page
> run. A `--symbol-ordering-file` 2-pass relink that clustered these onto
> shared pages was tried and dropped (the second link wasn't worth it over
> the monolithic `.text` lld emits by default ...). What still matters at
> the source level: keep cold-only code off these pages. Subcommand bodies
> never reached by `bun run` (`bun install`, `bun create`, the
> bundler/test-runner entry points) and the panic/crash-report path are
> tagged `#[cold]` so LLVM sinks them to the tail of their translation
> unit's `.text` instead of interleaving with the startup chain."

This is a binary-layout optimization (code placement, not an algorithmic
change), and the source itself documents a rejected alternative
(`--symbol-ordering-file` relink) — a genuine engineering artifact, not
just a design aspiration. Node and Deno's own binary-layout practices were
NOT source-audited to the same depth this stage (open item, unchanged from
the existing Evidence Map note) — H3 measures the shipped-binary behavior
of all three runtimes directly rather than relying on a source-level
comparison for Node/Deno.

## Environment

Same shared 2-vCPU cloud sandbox as H4/H5/H6 (Intel Xeon @2.80GHz, KVM/
Firecracker guest, Ubuntu 24.04.4, kernel `6.18.5-fc-v20`) — **not
dedicated hardware**. Per the Stage 13 instruction, this run is classified
LIMITED/PILOT for that reason regardless of how clean the data looks. Load
average at run start was low (0.25/0.19/0.08). Full detail in
`metadata.json`.

## Runtime versions / commits

| Runtime | Version | Build track | Binary path |
|---|---|---|---|
| Bun | 1.3.13 | release | `bun` |
| Node | v22.22.2 | release | `node` |
| Deno | 2.9.5 (V8 15.0.245.2-rusty) | release | `deno` |

**Source-controlled builds were not attempted for H3.** H5 already
established, and verified via direct curl (`static.rust-lang.org` →
connection code `000`), that this sandbox cannot install Bun's pinned Rust
nightly toolchain — the same blocker applies here and was not re-tested.
All three runtimes are on the same (release) track, which at least avoids
mixing source-controlled and release builds under one headline comparison,
per the Section 8 instruction.

## Cache-drop method

```
sync
echo 3 > /proc/sys/vm/drop_caches
```

**Verified, not assumed:** root with `cap_sys_admin` in the capability
bounding set; a standalone pre-check (write 100MB test file → read it into
cache, confirmed via `/proc/meminfo` `Cached` rising from 301,344 kB to
404,012 kB → drop caches → `Cached` falls back to 300,648 kB) confirmed the
mechanism works end-to-end. `cat /proc/sys/vm/drop_caches` itself returns
"Permission denied" in this sandbox (the sysctl is write-only/unreadable
here) — this did not block the write, which succeeded and produced a
verified drop every single time it was used, including independently for
every one of the 90 cold runs in the main dataset (see
`cache_drop_verification` in `summary.json` — median 104–126 MB of cache
dropped per cold run, `all_writes_ok=True` for all three runtimes, 30/30
runs each).

## Cold-run procedure

For every cold run: confirm no leftover test process, `sync`, drop caches,
verify the drop via `/proc/meminfo` `Cached` before/after (recorded per
run), then launch via
`perf stat -e page-faults,minor-faults,major-faults -- <runtime> hello.js`.
Cache was dropped **independently before every individual cold run** —
never dropped once and reused across multiple runs or across runtimes.
Runtimes were run in sequential blocks (30 Bun cold+warm pairs, then 30
Node, then 30 Deno), but the cache-preparation method for every individual
cold run is identical regardless of which block it's in, satisfying the
"equivalent preparation across runtimes" requirement without needing to
interleave runtimes run-by-run.

**perf tool deviation (disclosed):** the running kernel (`6.18.5-fc-v20`)
is a custom Firecracker-patched build with no matching `linux-tools`
package in Ubuntu's repos (`apt-get install linux-tools-generic` 404s —
the kernel-specific meta-package doesn't exist upstream). Installed the
closest available build (`linux-tools-6.8.0-111`, perf 6.8.12) and invoked
its versioned binary directly (`/usr/lib/linux-tools-6.8.0-111/perf`),
bypassing the `/usr/bin/perf` wrapper's kernel-version guard. Cross-checked
against `/usr/bin/time -v` (a different code path — getrusage() at
wait4(), not perf's software-counter subsystem) on a sample invocation:
2447 vs 2534 minor faults, both 0 major faults — reasonably consistent
(~3.5% apart, not a controlled cold/warm pair so some difference is
expected), supporting that the version-mismatched perf build's page-fault
software counters are behaviorally trustworthy on this kernel. Full detail
in `metadata.json` → `perf_tool`.

## Warm sanity procedure

Immediately after every cold run (same iteration, no intervening cache
drop), the same executable was run again. This produced 30 warm runs per
runtime (exceeding the 5-run minimum). Warm runs are the sanity dataset,
not the primary result.

**Prerequisite validation (Section 3), performed before the main run:**
cold node run showed 32 major faults / ~78–85ms elapsed; the immediate
warm re-run showed 0 major faults / ~30–32ms elapsed — repeated twice,
fully reproducible both times. This confirmed cold state ≠ warm state
before any of the 180-run main dataset was collected. **Result: PASSED.**
Not classified BLOCKED.

Across the full main dataset, every single warm run for all three runtimes
showed exactly **0 major faults**, with zero variance (min=max=0 across
30 runs each) — a strong, completely deterministic warm-vs-cold signal
that held for the entire experiment, not just the upfront check.

## Binary/linker audit

| Runtime | Size (bytes) | Type | Linking | LOAD segments | Notes |
|---|---:|---|---|---:|---|
| Bun | 101,814,712 | ELF EXEC (non-PIE) | dynamic (libc, libpthread, libdl, libm) | 3 | not stripped |
| Node | 124,679,552 | ELF EXEC (non-PIE) | dynamic (libdl, libstdc++, libm, libgcc_s, libpthread, libc) | 5 | not stripped, has debug_info |
| Deno | 95,582,008 | ELF PIE | dynamic (libdl, libgcc_s, librt, libpthread, libm, libc) | 4 | **stripped** |

All three are dynamically linked against the same base system libraries
(glibc, no exotic static linking that would trivially explain a
difference). Full `file`/`ldd`/`readelf -l` output preserved in
`../raw/binary_audit.json`.

**On binary size as an alternative explanation:** file size does NOT
monotonically predict total page-fault count in this data — Deno is the
*smallest* binary (95.6 MB) but has the *most* total page faults (2619
median); Node is the *largest* binary (124.7 MB) but has a *middle* total
fault count (2498 median); Bun (101.8 MB, middle size) has the *fewest*
total faults (1562 median). Binary size alone does not explain the
ranking — see "What this does NOT support" below.

## Repetitions

30 independent cold runs + 30 paired warm runs per runtime (90 cold + 90
warm = 180 total runs). 0 failed runs. No run was reduced, added, or
discarded after seeing results.

## Raw-data integrity

Every run preserved individually as
`raw/<runtime>-run<NN>-<cold|warm>.json` (180 files), plus
`raw/run_index.json` (all 180 entries) and `raw/binary_audit.json`. No run
overwritten or replaced.

## Page-fault results

**PRIMARY METRIC — cold runs, n=30 per runtime:**

| Runtime | Median minor faults | Median major faults | Median total faults | Mean | Stddev | CV |
|---|---:|---:|---:|---:|---:|---:|
| Bun | 1,535 | 27 | 1,562 | 1,563.7 | 3.75 | 0.24% |
| Node | 2,466 | 32 | 2,498 | 2,497.7 | 1.62 | 0.06% |
| Deno | 2,599 | 18 | 2,619 | 2,618.6 | 4.19 | 0.16% |

Extremely low run-to-run variance (CV under 0.25% for all three) — this is
an unusually clean, low-noise measurement, consistent with Stage 10's
prediction that page-fault counting would be one of the cleanest Stage 13
candidates. **Major fault counts were perfectly deterministic across all
30 runs per runtime** (stddev = 0.00; Bun always exactly 27, Node always
exactly 32, Deno always exactly 18).

**The ranking is not monotonic in one clean direction across all metrics:**

- **Fewest total page faults: Bun** (1,562), by a wide margin over both
  Node (2,498, +60%) and Deno (2,619, +68%).
- **Fewest major faults: Deno** (18), not Bun (27) — Node has the most
  (32). Major faults specifically require disk I/O to resolve (the more
  cache-state-sensitive signal), and here Bun does NOT win.
- Node has the most major faults despite not having the most total faults
  — its ranking flips between the two fault types.

## Startup-time results

**SECONDARY METRIC:**

| Runtime | Cold startup time (median) | Warm startup time (median) | Cold faults (median) | Warm faults (median) |
|---|---:|---:|---:|---:|
| Bun | 49.1 ms | 9.9 ms | 1,562 | 1,553 |
| Node | 77.6 ms | 35.8 ms | 2,498 | 2,476 |
| Deno | 49.2 ms | 15.4 ms | 2,619 | 2,602 |

**Critical finding (per the Section 18 interpretation rule):** total
page-fault count does **not** cleanly predict cold startup wall-clock time
here. Bun and Deno have nearly identical cold startup time (49.1 ms vs
49.2 ms — a difference far smaller than either's own run-to-run variation,
see CIs below) despite Deno having 68% more total page faults than Bun.
Node, despite having *fewer* total faults than Deno (2,498 vs 2,619) and
more major faults than Deno (32 vs 18), is the *slowest* to cold-start
(77.6 ms vs Deno's 49.2 ms). Page-fault count is not acting as a simple
linear proxy for startup time across these three runtimes.

## Statistical summary

95% CI via t-distribution (df=29 for n=30, t=2.045), computed on raw
per-run values, no outlier removal.

| Combo | Total faults median | Mean | 95% CI (mean) | Elapsed (s) median | 95% CI (mean, s) |
|---|---:|---:|---|---:|---|
| bun-cold | 1,562 | 1,563.7 | [1,562.3 – 1,565.1] | 0.0491 | [0.0464 – 0.0551] |
| bun-warm | 1,553 | 1,553.9 | [1,552.8 – 1,554.9] | 0.0099 | [0.0098 – 0.0101] |
| node-cold | 2,498 | 2,497.7 | [2,497.1 – 2,498.3] | 0.0776 | [0.0752 – 0.0841] |
| node-warm | 2,476 | 2,476.0 | [2,475.6 – 2,476.4] | 0.0358 | [0.0344 – 0.0374] |
| deno-cold | 2,619 | 2,618.6 | [2,617.0 – 2,620.1] | 0.0492 | [0.0484 – 0.0512] |
| deno-warm | 2,602 | 2,602.1 | [2,599.8 – 2,604.4] | 0.0154 | [0.0150 – 0.0158] |

None of the three runtimes' cold-fault CIs overlap with each other — the
total-fault ranking (Bun < Node < Deno) is statistically clean in this
sample. Bun's and Deno's cold-elapsed-time CIs DO overlap substantially
([0.0464–0.0551] vs [0.0484–0.0512]) — their startup times are not
statistically distinguishable in this data, despite very different fault
profiles.

## Falsification status

**SUPPORTED (qualified)** — for the specific, narrow claim "Bun shows
fewer total cold-start page faults than Node and Deno under controlled,
cache-dropped conditions." The effect is large (Bun has 37–60% fewer total
faults than Node/Deno), highly reproducible (CV under 0.25%), and
statistically clean (non-overlapping CIs).

The falsifier's core condition — "no meaningful page-fault-count
difference after controlling for cache state, binary size, linking, and
loader differences" — is **not** met for total fault count (a meaningful,
reproducible difference exists, and it does not reduce to binary size
since Deno, the smallest binary, has the most total faults). It **is**
partially met for major faults specifically, where Bun does not win.

## What the result supports

- Bun's binary, as shipped (release 1.3.13), incurs substantially and
  reproducibly fewer total page faults on cold start than Node or Deno's
  shipped binaries, for a minimal `console.log` workload, under verified
  cold cache conditions.
- This difference is not trivially explained by binary file size alone
  (Deno is smaller than Bun but faults more).
- The result is directionally consistent with M12's source-level claim
  (Bun deliberately keeps cold-only code off the hot startup path,
  reducing pages that must be faulted in) — but see the qualifications
  below before treating this as confirmation of the mechanism's specific
  causal contribution.

## What this does NOT support

- **Does not establish that M12's specific mechanism (the `#[cold]`
  tagging / code-placement strategy) *caused* the observed page-fault
  reduction**, as opposed to some other difference between the binaries
  (different runtime architecture, different startup code paths, JSC vs
  V8 initialization differences, different numbers of shared library
  dependencies, etc.). This experiment measures the binary-level
  *consequence* consistent with M12, not an isolated, controlled test of
  the layout technique itself (that would require, e.g., comparing a Bun
  build with the technique against an otherwise-identical Bun build
  without it — not attempted here, and not what M12 or this experiment
  claims to do).
- **Does not establish that fewer page faults caused faster startup.**
  Bun and Deno have statistically indistinguishable cold startup times
  despite very different total fault counts — the "fewer page faults →
  faster start" inference explicitly does NOT hold cleanly across all
  three runtimes in this data, exactly the kind of premature conclusion
  Section 18 warned against.
- **Does not establish an advantage for Bun on major faults specifically**
  — Deno has fewer (18 vs Bun's 27), which is real counter-evidence against
  a maximally strong version of the M12 story.
- Does not compare against Node's or Deno's own binary-layout engineering
  practices at the source level (unchanged open item from the existing
  Evidence Map note).

## Surprising findings

- Total page faults and major page faults give **opposite runtime
  rankings for who "wins"**: Bun wins on total, Deno wins on major. These
  are genuinely different signals (major faults require disk I/O; minor
  faults are typically just first-touch of a newly-mapped zero/anonymous
  page or a page already resident in cache after warm-up) and this
  experiment shows they should not be conflated even within a single
  runtime comparison.
- Total fault count and cold startup wall-clock time diverge sharply for
  Bun vs Deno: nearly identical startup time, very different fault
  profiles — direct evidence against assuming a simple linear
  fault-count-to-time relationship.
- Warm-run major faults were **exactly 0 with zero variance** across all
  90 warm runs (30 per runtime) — a remarkably clean, fully deterministic
  signal that the cache-drop procedure is working correctly and
  consistently, strengthening confidence in the cold-run data's validity.
- Node's major-fault count (32) is the highest of the three despite Node
  not being the runtime with the most total faults (that's Deno) — Node's
  fault profile is worse specifically in the I/O-costly category, a
  distinct finding from its already-known lower HTTP throughput (H6) or
  buffer allocation results (H5).

## Counter-evidence

Deno's fewer major faults than Bun (18 vs 27) is direct counter-evidence
against an unqualified "Bun's binary layout minimizes page faults" claim —
it does not hold for the major-fault sub-metric, only for total faults.
Additionally, the near-identical Bun/Deno cold startup times, despite very
different total fault counts, is counter-evidence against treating page
faults as the dominant driver of cold-start wall-clock time for these
particular runtimes/binaries — other factors (V8 vs JSC initialization
cost, dynamic linker resolution time, etc.) evidently matter enough to
equalize Bun and Deno's wall-clock outcome despite Bun's large total-fault
advantage.

## Confounders / limitations

- Shared 2-vCPU cloud sandbox, not dedicated hardware (disclosed per
  Stage 13 instruction; classified LIMITED/PILOT for this reason alone,
  independent of data quality).
- perf tool version mismatch against the running kernel (disclosed above,
  cross-validated but not a perfect substitute for a kernel-matched perf
  build).
- All three runtimes benchmarked as release builds, not source-controlled
  at the pinned commits (consistent with H4/H5's precedent, disclosed).
- Node's binary retains debug_info (not stripped) while Deno's is
  stripped and Bun's is not stripped either — differing symbol-table/debug
  section sizes could contribute to file-size differences, though debug
  sections are typically not part of LOAD segments actually mapped/faulted
  at runtime; this was not independently verified for each binary in this
  pass and is logged as an open item.
- Minimal single-file workload (`console.log("hello")`) isolates
  process/runtime bootstrap cleanly but does not represent a realistic
  application cold start (real module graphs, real dependencies) — this
  is a deliberate Section 5 scope choice, not an oversight, but the
  result should not be generalized to larger real-world cold starts
  without further testing.
- Single-machine, single-session sample; no cross-machine replication.

## Data-quality assessment (12-item check)

1. Page-cache dropping actually possible: **YES** (verified, root +
   cap_sys_admin, measurable Cached drop).
2. Cache-drop procedure succeeded before every cold run: **YES** (every
   one of 90 cold runs logged a positive `cached_kb_dropped`; median
   104–126 MB dropped per run across the three runtimes).
3. Warm sanity runs showed the expected cache-state difference: **YES** —
   deterministically 0 major faults warm vs consistently nonzero (18–32)
   cold, for all three runtimes, across all 30 pairs each.
4. All runtimes received equivalent preparation: **YES** — identical
   per-run cache-drop procedure applied independently before every cold
   run regardless of runtime.
5. Binary sizes recorded: **YES**.
6. Static/dynamic linking recorded: **YES** — all three dynamically
   linked, no static-linking confound.
7. Loader/dependency information recorded: **YES** (`ldd`, `readelf -l`
   in `raw/binary_audit.json`).
8. Minimum 30 independent cold runs per runtime: **YES** — exactly 30 for
   all three, 0 failures.
9. Raw measurements preserved: **YES** — all 180 runs individually, plus
   consolidated index.
10. No outliers silently removed: **YES** — none removed; variance was
    already extremely low (CV <0.25%) so no trimming was ever
    contemplated.
11. Primary metric remained page-fault count: **YES** — startup time is
    explicitly reported as secondary and was not used to override the
    primary interpretation (see "Critical finding" under Startup-time
    results, where the primary/secondary divergence is reported
    honestly rather than smoothed over).
12. Environment limitations explicitly recorded: **YES** (shared
    hardware, perf version mismatch, release-not-source builds, all
    disclosed above).

**Classification: PILOT / LIMITED.** Per the Stage 13 instruction, shared
(non-dedicated) hardware alone is sufficient to classify this as
LIMITED/PILOT rather than authoritative, regardless of the otherwise very
clean data quality (all 12 checklist items pass; variance is unusually
low for this kind of measurement). The perf-tool version mismatch and
release-build (not source-controlled) tracks are additional, smaller
contributors to the LIMITED classification.

## Evidence Map impact (recommendation only — not applied to evidence-map.md pending review)

Recommended targeted update to **M12 only**:
**MIXED** — a measurable, reproducible, statistically clean total-page-
fault reduction for Bun exists relative to Node and Deno under this
controlled test, consistent in direction with M12's source-level claim.
However, this does NOT extend cleanly to major faults (Deno wins that
sub-metric) or to startup wall-clock time (Bun and Deno are
statistically indistinguishable there despite very different fault
counts) — so the effect exists but does not translate consistently into
every metric that might be construed as "the benefit" of the mechanism.
This is exactly the "MIXED" category as defined in Section 23
("page-fault effect exists but doesn't translate consistently into
startup-time benefit"). Do not modify M5, M6, M19, M20, M2, M9, or M16
based solely on H3.

## Recommendation for H2

No specific dependency identified between H3's findings and H2's scope.
H3 was self-contained (cold-start page faults, Bun/Node/Deno). Proceed to
H2 per the standing sequence (H6 ✅ → H4 ✅ → H5 ✅ → H3 ✅ → H2 → H1 → H7
deferred) once this report is reviewed.

## Exact benchmark source and run commands

Program under test (`benchmark/hello.js`, identical across all three
runtimes):
```js
console.log("hello");
```

Orchestration:
```
python3 benchmark/orchestrate.py   # full 180-run protocol (30 cold+30 warm x 3 runtimes)
python3 benchmark/analyze.py       # statistics + result tables
```

Per-run invocation (as issued by orchestrate.py):
```
sync
echo 3 > /proc/sys/vm/drop_caches
/usr/lib/linux-tools-6.8.0-111/perf stat -e page-faults,minor-faults,major-faults -- <bun|node|deno run> hello.js
```
