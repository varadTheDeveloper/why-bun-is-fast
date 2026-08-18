# H3 — Cold-start page-fault count

**Status: protocol only. Not yet executed. Classification: MUST RUN.**

## Purpose

Test whether Bun's deliberately-tuned binary layout (cold-path code physically separated from the `bun run` hot chain, per `src/bun_bin/lib.rs`'s own doc comment) measurably reduces page-fault count during cold process startup, relative to Node and Deno.

## Hypothesis (H3, from Stage 11 — unmodified)

Bun's binary layout contributes measurably to cold-start page-fault count.

## Mechanism

M12 — page-fault-aware binary layout. Ranked the cleanest, lowest-noise measurement of any candidate in the Evidence Map (Stage 10/11).

## Runtime versions / commits

- **Track used: both.** Source-controlled build validates the exact mechanism traced in `src/bun_bin/lib.rs`; release build validates real-world relevance for what a user actually installs.
- Bun source-controlled pin: `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4`.
- Node source-controlled pin: `nodejs/node@ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85`.
- Deno: release build only for this experiment (Deno's binary-layout claims were never part of this project's source-verified mechanism set — M12 is Bun-specific; Deno is included as a general comparator, not a mechanism-matched one).
- Record actual versions/commits used into `metadata.json`.

## Hardware / environment

Linux with root access required (page-cache dropping needs `CAP_SYS_ADMIN`/root for `/proc/sys/vm/drop_caches`). If the execution machine is macOS, substitute the documented macOS equivalent (`purge` command plus `vm_stat`/`dtrace`-based fault counting) and note the substitution explicitly in results — do not silently treat the two OSes' numbers as comparable without flagging the different measurement path. Record CPU, OS, kernel, filesystem type explicitly per Section 4.

## Setup

1. Prepare a minimal entrypoint script per runtime that does the least possible work while still representing "a normal cold start" (`console.log("hello")` or closest equivalent) — chosen to isolate startup cost from JS-execution cost, per M12's own scope.
2. Verify page-cache-drop capability on the execution machine (`sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`) before any measurement — this is a hard setup prerequisite, not optional.
3. Record binary size, static-vs-dynamic linking status, and OS loader details for each runtime into `metadata.json` as documented confounders (this directly closes Stage 11 HIGH-priority open item 10 — Node/Deno static-linking parity vs. Bun's — as part of this experiment's own setup, rather than leaving it as a separate unresolved gap).

## Commands

```sh
# placeholder — finalized at Stage 13; sketch:
sync && echo 3 | sudo tee /proc/sys/vm/drop_caches
perf stat -e page-faults,major-faults,minor-faults,task-clock,context-switches -- bun ./hello.js
# repeat immediately (same binary, now page-cache-warm) as a sanity check:
perf stat -e page-faults,major-faults,minor-faults -- bun ./hello.js
# repeat full cold sequence for node, deno
```

## Warmup

**Inverted relative to every other experiment in this set: this experiment measures the COLD state deliberately, not a warmed-up one.** A "warm" run (identical binary, page cache intact from the immediately preceding cold run) is captured as a sanity check that the cache-drop actually worked (warm fault count should be materially lower than cold), not as the primary measured condition.

## Repetitions

- Cold-start measurements: minimum 30 independent runs per runtime (each preceded by a fresh page-cache drop), since page-fault count can vary run to run even at "cold" state depending on what else the OS had to fault in.
- Warm sanity-check runs: 5 per runtime, sufficient to confirm the cache-drop is working, not intended as a primary data source.
- Outliers: not discarded by default.

## Metrics

- **Primary:** page-fault count (major and minor, reported separately where the OS distinguishes them).
- **Secondary:** startup wall-clock time (`task-clock` from `perf stat`, or platform equivalent).

## Statistical method

Median + mean + stddev + CV across the ≥30 cold runs per runtime, per Section 5.

## Expected result (directional only)

If M12's mechanism holds, Bun should show a lower page-fault count on cold start than Node and/or Deno, after controlling for binary-size/linking differences. No magnitude predicted. If Node/Deno turn out to have comparable layout efficiency by other means, a null result is equally informative and directly addresses the previously-unresolved "does Node/Deno do anything equivalent" question from M12's own Counter-evidence field.

## Falsifier

No meaningful page-fault-count difference after controlling for page-cache state and binary-layout confounders (static/dynamic linking, binary size).

## Confounders / risks

- Requires root/cache-drop capability — a hard environment prerequisite that may not be available on the current cloud sandbox (flag explicitly if so, and treat any measurement taken without confirmed cache-dropping as informal/non-authoritative).
- Static vs. dynamic linking differences between the three runtimes are a real, separate confound from the binary-layout mechanism M12 describes — must be recorded and discussed, not conflated with the layout finding itself.
