#!/usr/bin/env python3
"""Compute summary statistics from raw/run_index.json for H5."""
import json
import math
import os
import statistics as stats

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
RESULTS_DIR = os.path.join(HERE, "..", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

SIZES = [16, 64, 256, 1024, 4096, 16384]
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
    with open(os.path.join(RAW_DIR, "warmup_log.json")) as f:
        warmup_log = json.load(f)

    ok_runs = [r for r in runs if r["status"] == "OK"]
    failed_runs = [r for r in runs if r["status"] != "OK"]

    combos = {}
    for r in ok_runs:
        key = (r["runtime"], r["size"])
        combos.setdefault(key, []).append(r)

    per_combo = {}
    for (runtime, size), rs in sorted(combos.items()):
        allocs = [r["allocsPerSec"] for r in rs]
        rss = [r["peak_rss_kb"] for r in rs if r.get("peak_rss_kb") is not None]
        wall = [r["wall_clock_process_s"] for r in rs]
        per_combo[f"{runtime}-{size}"] = {
            "runtime": runtime, "size": size, "n_runs": len(rs),
            "allocs_per_sec": summarize(allocs),
            "peak_rss_kb": summarize(rss) if rss else None,
            "wall_clock_process_s": summarize(wall),
            "warmup_final_status": warmup_log.get(f"{runtime}-{size}", {}).get("final_status"),
            "warmup_final_iterations": warmup_log.get(f"{runtime}-{size}", {}).get("final_iterations"),
        }

    # Required result table: per size, Bun vs Node, faster runtime, relative diff, CV
    table_rows = []
    for size in SIZES:
        bun_key, node_key = f"bun-{size}", f"node-{size}"
        if bun_key not in per_combo or node_key not in per_combo:
            table_rows.append({"size": size, "status": "MISSING DATA"})
            continue
        b = per_combo[bun_key]["allocs_per_sec"]["median"]
        n = per_combo[node_key]["allocs_per_sec"]["median"]
        faster = "Bun" if b > n else ("Node" if n > b else "TIE")
        rel_diff = (b - n) / n if n else None  # positive => Bun faster
        table_rows.append({
            "size": size,
            "bun_median_allocs_per_sec": b,
            "node_median_allocs_per_sec": n,
            "faster_runtime": faster,
            "relative_diff_bun_minus_node_over_node": rel_diff,
            "bun_cv": per_combo[bun_key]["allocs_per_sec"]["cv"],
            "node_cv": per_combo[node_key]["allocs_per_sec"]["cv"],
        })

    warmup_stability_summary = {
        k: {"final_status": v["final_status"], "final_iterations": v["final_iterations"],
            "n_attempts": len(v["attempts"])}
        for k, v in warmup_log.items()
    }

    out = {
        "n_total_runs": len(runs), "n_ok_runs": len(ok_runs), "n_failed_runs": len(failed_runs),
        "failed_runs": failed_runs,
        "per_combo_summary": per_combo,
        "result_table": table_rows,
        "warmup_stability_summary": warmup_stability_summary,
    }

    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)

    print(f"n_ok={len(ok_runs)} n_failed={len(failed_runs)}")
    print()
    print(f"{'size':>7} | {'bun allocs/s':>15} | {'node allocs/s':>15} | {'faster':>6} | {'rel_diff':>9} | {'bun_cv':>7} | {'node_cv':>7}")
    for row in table_rows:
        if row.get("status") == "MISSING DATA":
            print(f"{row['size']:>7} | MISSING DATA")
            continue
        print(f"{row['size']:>7} | {row['bun_median_allocs_per_sec']:>15,.0f} | {row['node_median_allocs_per_sec']:>15,.0f} | "
              f"{row['faster_runtime']:>6} | {row['relative_diff_bun_minus_node_over_node']:>+8.1%} | "
              f"{row['bun_cv']:>6.1%} | {row['node_cv']:>6.1%}")

    print()
    print("Warmup stability (predefined check, NOT altered post-hoc):")
    for k, v in warmup_stability_summary.items():
        print(f"  {k}: {v['final_status']} (final_iterations={v['final_iterations']}, attempts={v['n_attempts']})")


if __name__ == "__main__":
    main()
