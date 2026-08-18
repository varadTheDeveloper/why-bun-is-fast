#!/usr/bin/env python3
"""Compute summary statistics from raw/run_index.json for H3."""
import json
import math
import os
import statistics as stats

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
RESULTS_DIR = os.path.join(HERE, "..", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

T_TABLE = {29: 2.045, 9: 2.262, 8: 2.306, 7: 2.365, 6: 2.447, 5: 2.571, 4: 2.776}


def ci95(values):
    n = len(values)
    if n < 5:
        return None
    m = stats.mean(values)
    sd = stats.stdev(values) if n > 1 else 0.0
    t = T_TABLE.get(n - 1, 2.045 if n - 1 >= 29 else 2.262)
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
    with open(os.path.join(RAW_DIR, "binary_audit.json")) as f:
        binary_audit = json.load(f)

    ok_runs = [r for r in runs if r["status"] == "OK"]
    failed_runs = [r for r in runs if r["status"] != "OK"]

    combos = {}
    for r in ok_runs:
        key = (r["runtime"], r["state"])
        combos.setdefault(key, []).append(r)

    per_combo = {}
    for (runtime, state), rs in sorted(combos.items()):
        total = [r["page_faults"] for r in rs]
        minor = [r["minor_faults"] for r in rs]
        major = [r["major_faults"] for r in rs]
        elapsed = [r["elapsed_s"] for r in rs]
        per_combo[f"{runtime}-{state}"] = {
            "runtime": runtime, "state": state, "n_runs": len(rs),
            "total_page_faults": summarize(total),
            "minor_faults": summarize(minor),
            "major_faults": summarize(major),
            "elapsed_s": summarize(elapsed),
        }

    # cache-drop verification stats (cold runs only)
    cache_drop_stats = {}
    for runtime in ["bun", "node", "deno"]:
        drops = [r["cached_kb_dropped"] for r in ok_runs if r["runtime"] == runtime and r["state"] == "cold"
                  and r.get("cached_kb_dropped") is not None]
        write_oks = [r["cache_drop_write_ok"] for r in ok_runs if r["runtime"] == runtime and r["state"] == "cold"]
        cache_drop_stats[runtime] = {
            "all_writes_ok": all(write_oks), "n_writes": len(write_oks),
            "cached_kb_dropped_summary": summarize(drops) if drops else None,
        }

    # Primary results table
    table_rows = []
    for runtime in ["bun", "node", "deno"]:
        cold = per_combo.get(f"{runtime}-cold")
        if not cold:
            continue
        table_rows.append({
            "runtime": runtime,
            "median_minor_faults": cold["minor_faults"]["median"],
            "median_major_faults": cold["major_faults"]["median"],
            "median_total_faults": cold["total_page_faults"]["median"],
            "mean_total_faults": cold["total_page_faults"]["mean"],
            "stddev_total_faults": cold["total_page_faults"]["stddev"],
            "cv_total_faults": cold["total_page_faults"]["cv"],
        })

    startup_table = []
    for runtime in ["bun", "node", "deno"]:
        cold = per_combo.get(f"{runtime}-cold")
        warm = per_combo.get(f"{runtime}-warm")
        startup_table.append({
            "runtime": runtime,
            "cold_startup_s_median": cold["elapsed_s"]["median"] if cold else None,
            "warm_startup_s_median": warm["elapsed_s"]["median"] if warm else None,
            "cold_faults_median": cold["total_page_faults"]["median"] if cold else None,
            "warm_faults_median": warm["total_page_faults"]["median"] if warm else None,
        })

    out = {
        "n_total_runs": len(runs), "n_ok_runs": len(ok_runs), "n_failed_runs": len(failed_runs),
        "failed_runs": failed_runs,
        "per_combo_summary": per_combo,
        "cache_drop_verification": cache_drop_stats,
        "primary_results_table": table_rows,
        "startup_time_table": startup_table,
        "binary_audit": binary_audit,
    }

    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)

    print(f"n_ok={len(ok_runs)} n_failed={len(failed_runs)}")
    print()
    print("PRIMARY: page faults (cold)")
    print(f"{'runtime':>7} | {'minor':>8} | {'major':>6} | {'total_med':>10} | {'mean':>10} | {'sd':>8} | {'cv':>6}")
    for row in table_rows:
        print(f"{row['runtime']:>7} | {row['median_minor_faults']:>8.0f} | {row['median_major_faults']:>6.0f} | "
              f"{row['median_total_faults']:>10.0f} | {row['mean_total_faults']:>10.0f} | "
              f"{row['stddev_total_faults']:>8.1f} | {row['cv_total_faults']:>6.1%}")
    print()
    print("SECONDARY: startup time (cold vs warm)")
    for row in startup_table:
        print(f"{row['runtime']:>7}: cold={row['cold_startup_s_median']*1000:.1f}ms warm={row['warm_startup_s_median']*1000:.1f}ms "
              f"cold_faults={row['cold_faults_median']:.0f} warm_faults={row['warm_faults_median']:.0f}")
    print()
    print("Cache-drop verification (cold runs):")
    for k, v in cache_drop_stats.items():
        s = v["cached_kb_dropped_summary"]
        print(f"  {k}: all_writes_ok={v['all_writes_ok']} n={v['n_writes']} "
              f"median_kb_dropped={s['median'] if s else 'N/A'}")


if __name__ == "__main__":
    main()
