#!/usr/bin/env python3
"""Compute summary statistics from raw/run_index.json, per Stage 12 Section 5
methodology: median primary, mean+stddev+CV secondary, p95/p99 for latency,
95% CI on throughput since n=10 >= 5. No outliers discarded (none observed:
zero errors/timeouts/non-2xx across all 60 runs, confirmed separately)."""
import json
import math
import os
import statistics as stats

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
RESULTS_DIR = os.path.join(HERE, "..", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def ci95(values):
    n = len(values)
    if n < 5:
        return None
    m = stats.mean(values)
    sd = stats.stdev(values) if n > 1 else 0.0
    # t-value for 95% CI, df=9 (n=10) ~ 2.262; generalize with a small lookup
    t_table = {9: 2.262, 8: 2.306, 7: 2.365, 6: 2.447, 5: 2.571, 4: 2.776}
    t = t_table.get(n - 1, 2.262)
    margin = t * sd / math.sqrt(n)
    return {"mean": m, "margin": margin, "low": m - margin, "high": m + margin}


def summarize_throughput(values):
    return {
        "n": len(values),
        "median": stats.median(values),
        "mean": stats.mean(values),
        "stddev": stats.stdev(values) if len(values) > 1 else 0.0,
        "cv": (stats.stdev(values) / stats.mean(values)) if len(values) > 1 and stats.mean(values) else 0.0,
        "min": min(values),
        "max": max(values),
        "ci95": ci95(values),
        "raw_values": values,
    }


def summarize_latency(values):
    return {
        "n": len(values),
        "median": stats.median(values),
        "mean": stats.mean(values),
        "stddev": stats.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "raw_values": values,
    }


def main():
    with open(os.path.join(RAW_DIR, "run_index.json")) as f:
        runs = json.load(f)

    ok_runs = [r for r in runs if r["status"] == "OK"]
    failed_runs = [r for r in runs if r["status"] != "OK"]

    combos = {}
    for r in ok_runs:
        key = (r["runtime"], r["workload"])
        combos.setdefault(key, []).append(r)

    summary = {}
    for (runtime, workload), rs in sorted(combos.items()):
        ac = [r["autocannon_raw"] for r in rs]
        throughput_avg = [a["requests"]["average"] for a in ac]
        throughput_total = [a["requests"]["total"] for a in ac]
        lat_p50 = [a["latency"]["p50"] for a in ac]
        lat_p95 = [a["latency"]["p97_5"] for a in ac]  # autocannon buckets; p97_5 closest below p99 tier used as p95 proxy noted explicitly
        lat_p99 = [a["latency"]["p99"] for a in ac]
        lat_mean = [a["latency"]["mean"] for a in ac]
        errors = sum(a["errors"] for a in ac)
        timeouts = sum(a["timeouts"] for a in ac)
        non2xx = sum(a.get("non2xx", 0) for a in ac)

        summary[f"{runtime}-{workload}"] = {
            "runtime": runtime,
            "workload": workload,
            "n_runs": len(rs),
            "throughput_req_per_s": summarize_throughput(throughput_avg),
            "total_requests_per_run": summarize_throughput(throughput_total),
            "latency_p50_ms": summarize_latency(lat_p50),
            "latency_p95_proxy_p97_5_ms": summarize_latency(lat_p95),
            "latency_p99_ms": summarize_latency(lat_p99),
            "latency_mean_ms": summarize_latency(lat_mean),
            "errors_total": errors,
            "timeouts_total": timeouts,
            "non2xx_total": non2xx,
        }

    # Primary derived quantity: relative gap (fastest / slowest median throughput) per workload
    gaps = {}
    for workload in ["A", "B"]:
        entries = {rt: summary[f"{rt}-{workload}"]["throughput_req_per_s"]["median"]
                   for rt in ["bun", "node", "deno"]}
        fastest_rt = max(entries, key=entries.get)
        slowest_rt = min(entries, key=entries.get)
        gaps[workload] = {
            "per_runtime_median_req_s": entries,
            "fastest_runtime": fastest_rt,
            "slowest_runtime": slowest_rt,
            "fastest_value": entries[fastest_rt],
            "slowest_value": entries[slowest_rt],
            "relative_gap_ratio": entries[fastest_rt] / entries[slowest_rt] if entries[slowest_rt] else None,
        }

    out = {
        "n_total_runs": len(runs),
        "n_ok_runs": len(ok_runs),
        "n_failed_runs": len(failed_runs),
        "failed_run_ids": [r["run_id"] for r in failed_runs],
        "per_combo_summary": summary,
        "relative_gap_by_workload": gaps,
        "gap_shrinkage": {
            "workload_A_gap_ratio": gaps["A"]["relative_gap_ratio"],
            "workload_B_gap_ratio": gaps["B"]["relative_gap_ratio"],
            "gap_ratio_of_ratios_B_over_A": (gaps["B"]["relative_gap_ratio"] / gaps["A"]["relative_gap_ratio"])
                if gaps["A"]["relative_gap_ratio"] else None,
        },
    }

    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)

    print(json.dumps(out["gap_shrinkage"], indent=2))
    print()
    for k, v in summary.items():
        t = v["throughput_req_per_s"]
        print(f"{k}: median={t['median']:.0f} req/s, mean={t['mean']:.0f}, stddev={t['stddev']:.0f}, "
              f"CV={t['cv']:.3f}, p50_lat={v['latency_p50_ms']['median']}ms, p99_lat={v['latency_p99_ms']['median']}ms, "
              f"errors={v['errors_total']}")


if __name__ == "__main__":
    main()
