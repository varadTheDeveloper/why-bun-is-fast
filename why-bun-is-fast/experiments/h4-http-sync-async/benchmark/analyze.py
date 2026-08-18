#!/usr/bin/env python3
"""Compute summary statistics from raw/run_index.json for H4."""
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


def main():
    with open(os.path.join(RAW_DIR, "run_index.json")) as f:
        runs = json.load(f)
    ok_runs = [r for r in runs if r["status"] == "OK"]

    combos = {}
    for r in ok_runs:
        key = (r["case"], r["concurrency"])
        combos.setdefault(key, []).append(r)

    summary = {}
    for (case, conc), rs in sorted(combos.items()):
        ac = [r["autocannon_raw"] for r in rs]
        throughput = [a["requests"]["average"] for a in ac]
        lat_p50 = [a["latency"]["p50"] for a in ac]
        lat_p95proxy = [a["latency"]["p97_5"] for a in ac]
        lat_p99 = [a["latency"]["p99"] for a in ac]
        lat_mean = [a["latency"]["mean"] for a in ac]
        server_cpu = [r["server_cpu_percent_avg"] for r in rs if r.get("server_cpu_percent_avg") is not None]
        errors = sum(a["errors"] for a in ac)
        timeouts = sum(a["timeouts"] for a in ac)

        summary[f"case{case}-c{conc}"] = {
            "case": case, "concurrency": conc, "n_runs": len(rs),
            "throughput_req_per_s": summarize(throughput),
            "latency_p50_ms": summarize(lat_p50),
            "latency_p95_proxy_p97_5_ms": summarize(lat_p95proxy),
            "latency_p99_ms": summarize(lat_p99),
            "latency_mean_ms": summarize(lat_mean),
            "server_cpu_percent": summarize(server_cpu) if server_cpu else None,
            "errors_total": errors, "timeouts_total": timeouts,
        }

    # Primary derived quantity: (sync - async) / async, per concurrency level
    comparisons = {}
    for conc in [50, 1]:
        a = summary[f"caseA-c{conc}"]["throughput_req_per_s"]["median"]
        b = summary[f"caseB-c{conc}"]["throughput_req_per_s"]["median"]
        comparisons[f"c{conc}"] = {
            "sync_median_req_s": a, "async_median_req_s": b,
            "relative_diff_sync_minus_async_over_async": (a - b) / b if b else None,
            "sync_faster": a > b,
        }

    out = {
        "n_total_runs": len(runs), "n_ok_runs": len(ok_runs),
        "per_combo_summary": summary,
        "sync_vs_async_comparison": comparisons,
    }

    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)

    print(json.dumps(comparisons, indent=2))
    print()
    for k, v in summary.items():
        t = v["throughput_req_per_s"]
        cpu = v["server_cpu_percent"]
        cpu_str = f"{cpu['median']:.1f}%" if cpu else "N/A"
        print(f"{k}: median={t['median']:.0f} req/s mean={t['mean']:.0f} sd={t['stddev']:.0f} cv={t['cv']:.3f} "
              f"ci95=({t['ci95']['low']:.0f},{t['ci95']['high']:.0f}) server_cpu={cpu_str} "
              f"p50_lat={v['latency_p50_ms']['median']} p99_lat={v['latency_p99_ms']['median']}")


if __name__ == "__main__":
    main()
