#!/usr/bin/env python3
"""Compute summary statistics from raw/run_index.json for H1."""
import json
import math
import os
import statistics as stats

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
RESULTS_DIR = os.path.join(HERE, "..", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

T_TABLE = {9: 2.262, 8: 2.306, 7: 2.365, 6: 2.447, 5: 2.571, 4: 2.776}


def ci95(values):
    n = len(values)
    if n < 5:
        return None
    m = stats.mean(values)
    sd = stats.stdev(values) if n > 1 else 0.0
    t = T_TABLE.get(n - 1, 2.262)
    margin = t * sd / math.sqrt(n)
    return {"mean": m, "margin": margin, "low": m - margin, "high": m + margin}


def summarize(values):
    return {
        "n": len(values), "median": stats.median(values), "mean": stats.mean(values),
        "stddev": stats.stdev(values) if len(values) > 1 else 0.0,
        "cv": (stats.stdev(values) / stats.mean(values)) if len(values) > 1 and stats.mean(values) else 0.0,
        "min": min(values), "max": max(values), "ci95": ci95(values), "raw_values": values,
    }


COMBOS = [
    "node-napi-control", "node-napi-test",
    "bun-napi-control", "bun-napi-test",
    "bun-ffi-control", "bun-ffi-test",
    "deno-ffi-control", "deno-ffi-fast", "deno-ffi-nonfast",
]


def main():
    with open(os.path.join(RAW_DIR, "run_index.json")) as f:
        runs = json.load(f)
    ok_runs = [r for r in runs if r["status"] == "OK"]

    per_combo = {}
    for combo_id in COMBOS:
        rs = [r for r in ok_runs if r["combo_id"] == combo_id]
        ns_per_call = [r["nsPerCall"] for r in rs]
        calls_per_sec = [r["callsPerSec"] for r in rs]
        warmup_statuses = [r.get("warmupFinalStatus") for r in rs]
        warmup_iters = [r.get("warmupFinalIterations") for r in rs]
        per_combo[combo_id] = {
            "n_runs": len(rs),
            "ns_per_call": summarize(ns_per_call) if ns_per_call else None,
            "calls_per_sec": summarize(calls_per_sec) if calls_per_sec else None,
            "warmup_final_statuses": warmup_statuses,
            "warmup_final_iterations": warmup_iters,
        }

    # Derived native-call overhead = test median - own control median
    def overhead(test_key, control_key):
        t = per_combo[test_key]["ns_per_call"]["median"]
        c = per_combo[control_key]["ns_per_call"]["median"]
        return t - c

    derived = {
        "node_napi_overhead_ns": overhead("node-napi-test", "node-napi-control"),
        "bun_napi_overhead_ns": overhead("bun-napi-test", "bun-napi-control"),
        "bun_ffi_overhead_ns": overhead("bun-ffi-test", "bun-ffi-control"),
        "deno_ffi_fast_overhead_ns": overhead("deno-ffi-fast", "deno-ffi-control"),
        "deno_ffi_nonfast_overhead_ns": overhead("deno-ffi-nonfast", "deno-ffi-control"),
    }

    # Required results table
    table = [
        {"runtime": "Bun", "variant": "bun:ffi (normal binding)",
         "control_ns_op": per_combo["bun-ffi-control"]["ns_per_call"]["median"],
         "test_ns_op": per_combo["bun-ffi-test"]["ns_per_call"]["median"],
         "derived_overhead_ns": derived["bun_ffi_overhead_ns"],
         "calls_per_sec": per_combo["bun-ffi-test"]["calls_per_sec"]["median"],
         "cv": per_combo["bun-ffi-test"]["ns_per_call"]["cv"], "comparable_group": "PRIMARY (own-normal-path)"},
        {"runtime": "Node", "variant": "N-API native addon",
         "control_ns_op": per_combo["node-napi-control"]["ns_per_call"]["median"],
         "test_ns_op": per_combo["node-napi-test"]["ns_per_call"]["median"],
         "derived_overhead_ns": derived["node_napi_overhead_ns"],
         "calls_per_sec": per_combo["node-napi-test"]["calls_per_sec"]["median"],
         "cv": per_combo["node-napi-test"]["ns_per_call"]["cv"], "comparable_group": "PRIMARY (own-normal-path) + N-API same-binary group"},
        {"runtime": "Bun", "variant": "N-API compat (same binary as Node row)",
         "control_ns_op": per_combo["bun-napi-control"]["ns_per_call"]["median"],
         "test_ns_op": per_combo["bun-napi-test"]["ns_per_call"]["median"],
         "derived_overhead_ns": derived["bun_napi_overhead_ns"],
         "calls_per_sec": per_combo["bun-napi-test"]["calls_per_sec"]["median"],
         "cv": per_combo["bun-napi-test"]["ns_per_call"]["cv"], "comparable_group": "N-API same-binary group"},
        {"runtime": "Deno", "variant": "Fast API (i32)",
         "control_ns_op": per_combo["deno-ffi-control"]["ns_per_call"]["median"],
         "test_ns_op": per_combo["deno-ffi-fast"]["ns_per_call"]["median"],
         "derived_overhead_ns": derived["deno_ffi_fast_overhead_ns"],
         "calls_per_sec": per_combo["deno-ffi-fast"]["calls_per_sec"]["median"],
         "cv": per_combo["deno-ffi-fast"]["ns_per_call"]["cv"], "comparable_group": "NOT APPLES-TO-APPLES vs N-API rows; comparable to bun:ffi as dlopen-FFI family"},
        {"runtime": "Deno", "variant": "non-fast path (i64/BigInt)",
         "control_ns_op": per_combo["deno-ffi-control"]["ns_per_call"]["median"],
         "test_ns_op": per_combo["deno-ffi-nonfast"]["ns_per_call"]["median"],
         "derived_overhead_ns": derived["deno_ffi_nonfast_overhead_ns"],
         "calls_per_sec": per_combo["deno-ffi-nonfast"]["calls_per_sec"]["median"],
         "cv": per_combo["deno-ffi-nonfast"]["ns_per_call"]["cv"], "comparable_group": "NOT APPLES-TO-APPLES vs N-API rows; within-Deno fast-vs-nonfast comparison"},
    ]

    out = {
        "n_total_runs": len(runs), "n_ok_runs": len(ok_runs), "n_failed_runs": len(runs) - len(ok_runs),
        "per_combo_summary": per_combo,
        "derived_overhead_ns": derived,
        "results_table": table,
    }

    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)

    print(f"n_ok={len(ok_runs)} n_failed={len(runs)-len(ok_runs)}")
    print()
    print(f"{'runtime':>7} | {'variant':>28} | {'ctrl_ns':>8} | {'test_ns':>8} | {'overhead_ns':>11} | {'calls/s':>13} | {'cv':>6}")
    for row in table:
        print(f"{row['runtime']:>7} | {row['variant']:>28} | {row['control_ns_op']:>8.3f} | {row['test_ns_op']:>8.3f} | "
              f"{row['derived_overhead_ns']:>11.3f} | {row['calls_per_sec']:>13,.0f} | {row['cv']:>6.1%}")


if __name__ == "__main__":
    main()
