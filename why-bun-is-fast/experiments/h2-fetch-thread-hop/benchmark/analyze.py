#!/usr/bin/env python3
"""Compute summary statistics from raw/*.json for H2."""
import glob
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


def percentile(values_sorted, p):
    if not values_sorted:
        return None
    k = (len(values_sorted) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values_sorted[int(k)]
    return values_sorted[f] + (values_sorted[c] - values_sorted[f]) * (k - f)


def summarize_latencies(values_ns):
    v = sorted(values_ns)
    return {
        "n": len(v), "median_ns": stats.median(v), "mean_ns": stats.mean(v),
        "stddev_ns": stats.stdev(v) if len(v) > 1 else 0.0,
        "cv": (stats.stdev(v) / stats.mean(v)) if len(v) > 1 and stats.mean(v) else 0.0,
        "p95_ns": percentile(v, 0.95), "p99_ns": percentile(v, 0.99),
        "min_ns": min(v), "max_ns": max(v),
    }


def main():
    run_files = sorted(glob.glob(os.path.join(RAW_DIR, "*-run*.json")))
    runs = []
    for path in run_files:
        with open(path) as f:
            runs.append(json.load(f))

    ok_runs = [r for r in runs if r["status"] == "OK"]

    # Per-run summary (median/p95/p99 of that run's own latency distribution)
    per_run_summary = {}
    for r in ok_runs:
        per_run_summary[r["run_id"]] = summarize_latencies(r["latenciesNs"])

    # Per (runtime, mode) aggregate: pool ALL individual latencies across all
    # 10 runs into one distribution (preserves the full underlying
    # distribution, per Section 21), AND report per-run median distribution
    # (median-of-medians) for cross-run consistency/CV.
    combos = {}
    for r in ok_runs:
        key = (r["runtime"], r["mode"])
        combos.setdefault(key, []).append(r)

    per_combo = {}
    for (runtime, mode), rs in sorted(combos.items()):
        pooled = []
        for r in rs:
            pooled.extend(r["latenciesNs"])
        run_medians = [stats.median(r["latenciesNs"]) for r in rs]
        run_throughputs = [r["throughputPerSec"] for r in rs] if mode == "keepalive" else None
        per_combo[f"{runtime}-{mode}"] = {
            "runtime": runtime, "mode": mode, "n_runs": len(rs),
            "n_pooled_requests": len(pooled),
            "pooled_latency_stats": summarize_latencies(pooled),
            "run_median_summary": summarize_latencies(run_medians),  # cross-run consistency of the median
            "throughput_per_sec_summary": summarize_latencies(run_throughputs) if run_throughputs else None,
            "warmup_final_statuses": [r.get("warmupFinalStatus") for r in rs],
            "warmup_final_iterations": [r.get("warmupFinalIterations") for r in rs],
            "connections_opened": [r["connection_verification"]["client_connections_opened"] for r in rs],
            "server_cpu_percent_during_run": [r.get("server_cpu_percent_during_run") for r in rs],
        }

    # Required results table
    table_rows = []
    for runtime in ["bun", "node", "deno"]:
        for mode, label in [("cold", "Cold"), ("keepalive", "Keep-alive")]:
            combo = per_combo.get(f"{runtime}-{mode}")
            if not combo:
                continue
            pls = combo["pooled_latency_stats"]
            tput = combo["throughput_per_sec_summary"]
            table_rows.append({
                "runtime": runtime, "state": label,
                "median_latency_us": pls["median_ns"] / 1000,
                "p95_latency_us": pls["p95_ns"] / 1000,
                "p99_latency_us": pls["p99_ns"] / 1000,
                "throughput_per_sec_median": tput["median_ns"] if tput else None,  # note: field name reused, holds req/s not ns for this combo
            })

    out = {
        "n_total_runs": len(runs), "n_ok_runs": len(ok_runs), "n_failed_runs": len(runs) - len(ok_runs),
        "failed_runs": [{"run_id": r.get("run_id"), "reason": r.get("reason")} for r in runs if r["status"] != "OK"],
        "per_run_summary": per_run_summary,
        "per_combo_summary": per_combo,
        "results_table": table_rows,
    }

    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)

    print(f"n_ok={len(ok_runs)} n_failed={len(runs)-len(ok_runs)}")
    print()
    print(f"{'runtime':>7} | {'state':>10} | {'median_us':>10} | {'p95_us':>8} | {'p99_us':>8} | {'throughput/s':>13}")
    for row in table_rows:
        tput_str = f"{row['throughput_per_sec_median']:.0f}" if row['throughput_per_sec_median'] else "N/A"
        print(f"{row['runtime']:>7} | {row['state']:>10} | {row['median_latency_us']:>10.1f} | "
              f"{row['p95_latency_us']:>8.1f} | {row['p99_latency_us']:>8.1f} | {tput_str:>13}")


if __name__ == "__main__":
    main()
