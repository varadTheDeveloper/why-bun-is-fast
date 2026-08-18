#!/usr/bin/env python3
"""
H1 orchestration — native JS->native call boundary (M2).

Variant groups (see README.md "Equivalence validation" for full discussion):

  napi   : the SAME compiled napi_addon.node binary loaded via require()
           by both node and bun (Bun's Node-API compatibility layer).
           Cleanest possible same-machine-code control for the Bun-vs-Node
           comparison, but tests Bun's N-API EMULATION overhead specifically,
           not Bun's own normal/fastest native-call path.
  bun-ffi: Bun's own normal/documented native binding mechanism
           (bun:ffi, dlopen-based) -- the PRIMARY Bun-side measurement,
           calling the same libnative.so used by Deno's dlopen variants.
  deno-ffi: Deno.dlopen(), Deno's own FFI mechanism (structurally similar
           to bun:ffi -- both are dlopen-a-shared-library systems, unlike
           N-API). Two sub-variants: fast (i32, V8 Fast-API eligible per
           documented type support) and nonfast (i64/BigInt, not eligible).

Each variant has a "control" mode (pure JS loop, no native call -- isolates
loop/timer overhead per Section 11) and one or more "test" modes (the
native call each iteration). Derived native-call overhead = test ns/call -
control ns/call, computed in analyze.py using each variant's OWN control
(not a shared cross-variant control), since even the control loop's
absolute cost can differ slightly between bun:ffi's control and napi's
control if the two scripts have arbitrarily different code paths --
though in this experiment the control loop body is byte-for-byte identical
across scripts by design.

10,000,000 timed calls per run (Stage 12 spec), minimum 10 independent
fresh processes per runtime/variant/mode. Warmup uses the chunked,
two-consecutive-pass stability check (same design as H2/H5, refined
further after H2's finding that a single-attempt check can be fooled by
an intermediate JIT-tiering plateau).
"""
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

TIMED_ITERATIONS = 10_000_000
RUNS_PER_COMBO = 10

COMBOS = [
    # (combo_id, cmd_prefix, script, mode)
    ("node-napi-control", ["node", "napi-bench.js"], "control"),
    ("node-napi-test", ["node", "napi-bench.js"], "test"),
    ("bun-napi-control", ["bun", "napi-bench.js"], "control"),
    ("bun-napi-test", ["bun", "napi-bench.js"], "test"),
    ("bun-ffi-control", ["bun", "bun-ffi-bench.js"], "control"),
    ("bun-ffi-test", ["bun", "bun-ffi-bench.js"], "test"),
    ("deno-ffi-control", ["deno", "run", "--allow-ffi", "deno-ffi-bench.js"], "control"),
    ("deno-ffi-fast", ["deno", "run", "--allow-ffi", "deno-ffi-bench.js"], "fast"),
    ("deno-ffi-nonfast", ["deno", "run", "--allow-ffi", "deno-ffi-bench.js"], "nonfast"),
]


def run_one(cmd_prefix, mode, run_id, timeout=120):
    cmd = cmd_prefix + [mode, str(TIMED_ITERATIONS)]
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return {"run_id": run_id, "status": "FAILED", "reason": f"TIMEOUT: {e}"}
    wall = time.time() - start

    if proc.returncode != 0:
        return {"run_id": run_id, "status": "FAILED",
                "reason": f"nonzero exit {proc.returncode}: stderr={proc.stderr[-1500:]}"}
    try:
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"run_id": run_id, "status": "FAILED", "reason": f"unparseable stdout: {proc.stdout[:500]} stderr={proc.stderr[:500]}"}

    entry = {"run_id": run_id, "status": "OK", "wall_clock_process_s": wall}
    entry.update(parsed)
    return entry


def main():
    results_index = []
    for combo_id, cmd_prefix, mode in COMBOS:
        print(f"\n=== {combo_id} ({RUNS_PER_COMBO} runs x {TIMED_ITERATIONS} calls) ===", flush=True)
        for run_idx in range(1, RUNS_PER_COMBO + 1):
            run_id = f"{combo_id}-run{run_idx:02d}"
            entry = run_one(cmd_prefix, mode, run_id)
            entry["combo_id"] = combo_id
            with open(os.path.join(RAW_DIR, f"{run_id}.json"), "w") as f:
                # omit chunkTimingsNs from console print but keep in file
                json.dump(entry, f, indent=2)
            results_index.append({k: v for k, v in entry.items() if k not in ("chunkTimingsNs", "warmupAttempts")})
            if entry["status"] == "OK":
                print(f"  run {run_idx}: nsPerCall={entry['nsPerCall']:.3f} callsPerSec={entry['callsPerSec']:.0f} "
                      f"warmup={entry.get('warmupFinalStatus')}({entry.get('warmupFinalIterations')}) "
                      f"wall={entry['wall_clock_process_s']:.2f}s", flush=True)
            else:
                print(f"  run {run_idx}: FAILED — {entry.get('reason','')[:200]}", flush=True)

    with open(os.path.join(RAW_DIR, "run_index.json"), "w") as f:
        json.dump(results_index, f, indent=2)

    n_ok = sum(1 for r in results_index if r["status"] == "OK")
    n_failed = sum(1 for r in results_index if r["status"] != "OK")
    print(f"\nWrote {len(results_index)} entries ({n_ok} OK, {n_failed} FAILED) to raw/run_index.json", flush=True)


if __name__ == "__main__":
    main()
