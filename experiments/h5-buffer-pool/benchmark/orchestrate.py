#!/usr/bin/env python3
"""
H5 orchestration script — Buffer allocation pooling asymmetry (Bun vs Node).

Protocol (per user's H5 kickoff spec):
  - Runtimes: Bun (release 1.3.13) and Node (release v22.22.2) ONLY.
    NOTE (deviation, disclosed): the user's spec designated "source-controlled
    builds at the exact pinned commits" as the PRIMARY track. That track was
    attempted and is NOT usable in this sandbox: `rustup` cannot install
    Bun's pinned rust-toolchain.toml requirement (nightly-2026-07-20) because
    static.rust-lang.org is unreachable from this environment (curl returns
    connection code 000 — a hard network failure, verified directly, not
    assumed). This blocks compiling Bun from source at the pinned commit.
    Falling back to the release-build track for BOTH runtimes (same
    consistent track for both sides of the comparison, as was done for H4
    and H6). The pinned source commits are still used, unchanged, to
    characterize the M20 mechanism itself (source citation only — not the
    benchmarked binary). This is documented again in metadata.json and
    results/README.md.
  - Buffer sizes: 16, 64, 256, 1024, 4096, 16384 bytes (fixed, no additions).
  - Warmup: start at 50,000 allocations per runtime x size, chunked into 10
    pieces (handled by alloc-bench.js). A stability check is applied to the
    per-chunk timings: if the trend across the last chunks is not flat
    (defined below), warmup is EXTENDED (not just assumed sufficient) by
    re-running warmup with an increased iteration count, up to a bounded
    number of extensions, and the outcome (stable / extended / still
    unstable) is recorded per runtime x size, not silently discarded.
  - Timed: 1,000,000 allocations per run, single chunk.
  - Repetitions: minimum 10 independent fresh-process runs per runtime x
    size (2 x 6 x 10 = 120 minimum timed runs). Failed runs are recorded,
    not silently replaced/deleted.
  - Secondary metrics: wall-clock (from alloc-bench.js), peak RSS via
    `/usr/bin/time -v` (Maximum resident set size), process.memoryUsage()
    heapUsed/external snapshot is NOT available post-exit for a
    single-shot child process, so peak RSS from /usr/bin/time is the
    cross-runtime-comparable secondary memory metric used here.
    GC activity: Node exposes --expose-gc / perf_hooks; Bun's GC
    instrumentation is different (JSC based). Rather than force an
    unequal-footing comparison, GC-specific instrumentation is NOT
    collected in the main timed runs (documented as a limitation) — see
    results/README.md "Confounders / Limitations".
"""
import json
import os
import re
import statistics as stats
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

SIZES = [16, 64, 256, 1024, 4096, 16384]
RING_SIZE = 4096  # bounded ring buffer; same for all sizes (16384*4096 = 64MB max resident from ring, acceptable)

WARMUP_ITERS_START = 50_000
WARMUP_MAX_EXTENSIONS = 3          # bounded — if still unstable after this, record UNSTABLE, don't loop forever
WARMUP_EXTENSION_MULTIPLIER = 2    # each extension doubles the warmup iteration count

TIMED_ITERS = 1_000_000
RUNS_PER_COMBO = 10

RUNTIMES = {
    "bun": {"cmd": "bun", "version_flag": "--version"},
    "node": {"cmd": "node", "version_flag": "--version"},
}


def run_bench(runtime_cmd, size, iterations, phase, timeout=120):
    """Run alloc-bench.js under /usr/bin/time -v, return (parsed_json_or_None, peak_rss_kb_or_None, stderr, returncode)."""
    cmd = ["/usr/bin/time", "-v", runtime_cmd, os.path.join(HERE, "alloc-bench.js"),
           str(size), str(iterations), str(RING_SIZE), phase]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return None, None, f"TIMEOUT after {timeout}s: {e}", -1

    stdout = proc.stdout.strip()
    stderr = proc.stderr

    peak_rss_kb = None
    m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", stderr)
    if m:
        peak_rss_kb = int(m.group(1))

    parsed = None
    if stdout:
        try:
            # alloc-bench.js prints exactly one JSON line to stdout
            parsed = json.loads(stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            parsed = None

    return parsed, peak_rss_kb, stderr, proc.returncode


def chunk_stability_check(chunk_timings_ns, num_chunks):
    """
    Predefined stability check (declared BEFORE any data is examined):
    Compare the mean per-iteration time of the LAST HALF of chunks vs the
    FIRST HALF of chunks. Stable if the last-half mean is within 15% of the
    first-half mean (i.e., no strong monotonic warm-up drift remaining) AND
    the coefficient of variation across the last half of chunks is <= 0.25
    (chunk-to-chunk noise in the presumed-steady region is bounded).
    This is a heuristic, disclosed as such; it does not guarantee true JIT
    steady state, only that timings have stopped trending in this sample.
    """
    if num_chunks < 4:
        return {"stable": None, "reason": "too few chunks to evaluate trend (numChunks<4)"}
    half = num_chunks // 2
    first_half = chunk_timings_ns[:half]
    last_half = chunk_timings_ns[-half:]
    first_mean = stats.mean(first_half)
    last_mean = stats.mean(last_half)
    rel_change = (last_mean - first_mean) / first_mean if first_mean else None
    last_cv = (stats.stdev(last_half) / last_mean) if len(last_half) > 1 and last_mean else 0.0
    stable = (rel_change is not None and abs(rel_change) <= 0.15) and last_cv <= 0.25
    return {
        "stable": stable,
        "first_half_mean_ns": first_mean,
        "last_half_mean_ns": last_mean,
        "relative_change": rel_change,
        "last_half_cv": last_cv,
        "reason": "within thresholds" if stable else "exceeded drift or noise threshold",
    }


def do_warmup_with_stability(runtime_cmd, size):
    """Run warmup, extending (bounded) if not stable. Returns a record of the whole warmup process."""
    attempts = []
    iters = WARMUP_ITERS_START
    for attempt_idx in range(WARMUP_MAX_EXTENSIONS + 1):
        parsed, peak_rss_kb, stderr, rc = run_bench(runtime_cmd, size, iters, "warmup")
        if parsed is None:
            attempts.append({
                "attempt": attempt_idx, "iterations": iters, "status": "FAILED",
                "stderr_tail": stderr[-2000:], "returncode": rc,
            })
            # A failed warmup run is preserved; we still attempt the next extension
            # rather than silently aborting the whole combo, unless this was the
            # last allowed attempt.
            iters *= WARMUP_EXTENSION_MULTIPLIER
            continue

        check = chunk_stability_check(parsed["chunkTimingsNs"], parsed["numChunks"])
        attempts.append({
            "attempt": attempt_idx, "iterations": iters, "status": "OK",
            "allocsPerSec": parsed["allocsPerSec"], "peak_rss_kb": peak_rss_kb,
            "chunkTimingsNs": parsed["chunkTimingsNs"], "stability_check": check,
        })
        if check.get("stable"):
            return {"final_status": "STABLE", "attempts": attempts, "final_iterations": iters}
        iters *= WARMUP_EXTENSION_MULTIPLIER

    last_ok = [a for a in attempts if a["status"] == "OK"]
    final_status = "UNSTABLE_AFTER_MAX_EXTENSIONS" if last_ok else "ALL_WARMUP_ATTEMPTS_FAILED"
    return {"final_status": final_status, "attempts": attempts, "final_iterations": iters // WARMUP_EXTENSION_MULTIPLIER}


def main():
    runtime_versions = {}
    for name, info in RUNTIMES.items():
        v = subprocess.run([info["cmd"], info["version_flag"]], capture_output=True, text=True).stdout.strip()
        runtime_versions[name] = v
        print(f"{name}: {v}", flush=True)

    warmup_log = {}
    results_index = []

    for runtime_name, info in RUNTIMES.items():
        runtime_cmd = info["cmd"]
        for size in SIZES:
            combo_key = f"{runtime_name}-{size}"
            print(f"\n=== WARMUP: {runtime_name} size={size} ===", flush=True)
            warmup_result = do_warmup_with_stability(runtime_cmd, size)
            warmup_log[combo_key] = warmup_result
            print(f"    warmup final_status={warmup_result['final_status']} "
                  f"final_iterations={warmup_result['final_iterations']} "
                  f"n_attempts={len(warmup_result['attempts'])}", flush=True)

            print(f"=== TIMED: {runtime_name} size={size} ({RUNS_PER_COMBO} runs x {TIMED_ITERS} iters) ===", flush=True)
            for run_idx in range(1, RUNS_PER_COMBO + 1):
                run_id = f"{runtime_name}-size{size}-run{run_idx:02d}"
                started = time.time()
                parsed, peak_rss_kb, stderr, rc = run_bench(runtime_cmd, size, TIMED_ITERS, "timed")
                finished = time.time()

                entry = {
                    "run_id": run_id, "runtime": runtime_name, "runtime_version": runtime_versions[runtime_name],
                    "size": size, "timed_iterations_requested": TIMED_ITERS,
                    "started_at_unix": started, "finished_at_unix": finished,
                    "wall_clock_process_s": finished - started,
                }
                if parsed is None:
                    entry["status"] = "FAILED"
                    entry["reason"] = stderr[-2000:] if stderr else f"no stdout, returncode={rc}"
                    print(f"  run {run_idx}/{RUNS_PER_COMBO}: FAILED — {entry['reason'][:200]}", flush=True)
                else:
                    entry["status"] = "OK"
                    entry["allocsPerSec"] = parsed["allocsPerSec"]
                    entry["totalNs"] = parsed["totalNs"]
                    entry["iterations_actually_run"] = parsed["iterations"]
                    entry["acc"] = parsed["acc"]
                    entry["peak_rss_kb"] = peak_rss_kb
                    entry["runtimeVersion_selfReported"] = parsed["runtimeVersion"]
                    print(f"  run {run_idx}/{RUNS_PER_COMBO}: {parsed['allocsPerSec']:.0f} allocs/sec, "
                          f"peak_rss={peak_rss_kb}KB, wall={entry['wall_clock_process_s']:.2f}s", flush=True)

                raw_path = os.path.join(RAW_DIR, f"{run_id}.json")
                with open(raw_path, "w") as f:
                    json.dump(entry, f, indent=2)
                results_index.append(entry)

    with open(os.path.join(RAW_DIR, "run_index.json"), "w") as f:
        json.dump(results_index, f, indent=2)
    with open(os.path.join(RAW_DIR, "warmup_log.json"), "w") as f:
        json.dump(warmup_log, f, indent=2)

    n_ok = sum(1 for r in results_index if r["status"] == "OK")
    n_failed = sum(1 for r in results_index if r["status"] != "OK")
    print(f"\nWrote {len(results_index)} run entries ({n_ok} OK, {n_failed} FAILED) to raw/run_index.json")
    unstable_combos = [k for k, v in warmup_log.items() if v["final_status"] != "STABLE"]
    if unstable_combos:
        print(f"*** WARMUP DID NOT REACH DECLARED STABILITY for: {unstable_combos} — see raw/warmup_log.json ***")


if __name__ == "__main__":
    main()
