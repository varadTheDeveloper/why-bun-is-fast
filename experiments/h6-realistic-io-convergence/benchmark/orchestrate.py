#!/usr/bin/env python3
"""
H6 orchestration script. Runs each (runtime x workload) combination for
N independent runs (fresh process each time), each run = untimed warmup +
timed autocannon window, and writes raw, unaggregated results to raw/.

Deviations from the Stage 12 protocol default (documented, not silent —
see results/README.md "Confounders / limitations" for the full reasoning):
  - concurrency 20 instead of 50 (2-vCPU shared sandbox)
  - timed window 10s instead of 60s (session wall-clock constraint)
  - warmup 5s instead of 30s (same constraint)
All other protocol elements (10 independent runs per combo, fresh process
per run, raw data preserved unaggregated, identical workload/DB/schema
across runtimes) are followed as specified.
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BENCH_DIR, "..", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

CONCURRENCY = 20
WARMUP_SECS = 5
TIMED_SECS = 10
RUNS_PER_COMBO = 10
COOLDOWN_SECS = 1.5
DENO_BIN = "deno"

COMBOS = [
    {
        "runtime": "bun", "workload": "A", "port": 3100,
        "cmd": ["bun", "run", "servers/bun-a.ts"],
    },
    {
        "runtime": "bun", "workload": "B", "port": 3101,
        "cmd": ["bun", "run", "servers/bun-b.ts"],
    },
    {
        "runtime": "node", "workload": "A", "port": 3200,
        "cmd": ["node", "servers/node-a.js"],
    },
    {
        "runtime": "node", "workload": "B", "port": 3201,
        "cmd": ["node", "servers/node-b.js"],
    },
    {
        "runtime": "deno", "workload": "A", "port": 3300,
        "cmd": [DENO_BIN, "run", "--allow-net", "--allow-env", "--node-modules-dir=none", "servers/deno-a.ts"],
    },
    {
        "runtime": "deno", "workload": "B", "port": 3301,
        "cmd": [DENO_BIN, "run", "--allow-net", "--allow-env", "--node-modules-dir=none", "servers/deno-b.ts"],
    },
]


def wait_ready(port, timeout=10):
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def run_autocannon(port, duration, connections):
    cmd = [
        "autocannon", "-c", str(connections), "-d", str(duration), "-j",
        f"http://127.0.0.1:{port}/",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)
    if proc.returncode != 0:
        return None, proc.stderr
    try:
        return json.loads(proc.stdout), proc.stderr
    except json.JSONDecodeError:
        return None, f"JSON parse failure: {proc.stdout[:500]} STDERR: {proc.stderr[:500]}"


def db_active_connections():
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-U", "h6bench", "-d", "h6bench", "-t", "-c",
             "SELECT count(*) FROM pg_stat_activity WHERE datname='h6bench';"],
            env={**os.environ, "PGPASSWORD": "h6bench_pw"},
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception as e:
        return f"error: {e}"


def main():
    results_index = []
    for combo in COMBOS:
        runtime, workload, port, cmd = combo["runtime"], combo["workload"], combo["port"], combo["cmd"]
        print(f"\n=== {runtime}-{workload} (port {port}) ===", flush=True)
        for run_idx in range(1, RUNS_PER_COMBO + 1):
            run_id = f"{runtime}-{workload}-run{run_idx:02d}"
            log_path = os.path.join("/tmp/h6logs", f"{run_id}.log")
            os.makedirs("/tmp/h6logs", exist_ok=True)
            entry = {
                "run_id": run_id, "runtime": runtime, "workload": workload,
                "concurrency": CONCURRENCY, "timed_duration_s": TIMED_SECS,
                "warmup_duration_s": WARMUP_SECS, "port": port,
                "started_at_unix": time.time(),
                "status": "FAILED", "reason": None,
            }

            log_f = open(log_path, "w")
            try:
                proc = subprocess.Popen(cmd, cwd=BENCH_DIR, stdout=log_f, stderr=subprocess.STDOUT,
                                         preexec_fn=os.setsid)
            except Exception as e:
                entry["reason"] = f"spawn failure: {e}"
                results_index.append(entry)
                continue

            try:
                if not wait_ready(port, timeout=10):
                    entry["reason"] = "server did not become ready within 10s"
                    results_index.append(entry)
                    continue

                # Untimed warmup (discarded, not written as a result row)
                warm_result, warm_err = run_autocannon(port, WARMUP_SECS, CONCURRENCY)
                if warm_result is None:
                    entry["reason"] = f"warmup failed: {warm_err}"
                    results_index.append(entry)
                    continue

                db_conns_before = db_active_connections() if workload == "B" else "N/A"

                # Timed run (the actual measurement)
                result, err = run_autocannon(port, TIMED_SECS, CONCURRENCY)
                if result is None:
                    entry["reason"] = f"timed run failed: {err}"
                    results_index.append(entry)
                    continue

                db_conns_after = db_active_connections() if workload == "B" else "N/A"

                entry["status"] = "OK"
                entry["db_active_connections_before"] = db_conns_before
                entry["db_active_connections_after"] = db_conns_after
                entry["autocannon_raw"] = result

                # Save full raw autocannon JSON for this run individually too
                raw_run_path = os.path.join(RAW_DIR, f"{run_id}.json")
                with open(raw_run_path, "w") as f:
                    json.dump(result, f, indent=2)

                print(f"  run {run_idx}/{RUNS_PER_COMBO}: "
                      f"{result['requests']['average']:.0f} req/s avg, "
                      f"p50={result['latency']['p50']}ms p99={result['latency']['p99']}ms "
                      f"errors={result['errors']} timeouts={result['timeouts']} "
                      f"non2xx={result.get('non2xx', 0)}", flush=True)

            finally:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    pass
                proc.wait(timeout=5) if proc.poll() is None else None
                log_f.close()
                time.sleep(COOLDOWN_SECS)

            entry["finished_at_unix"] = time.time()
            results_index.append(entry)

    index_path = os.path.join(RAW_DIR, "run_index.json")
    with open(index_path, "w") as f:
        json.dump(results_index, f, indent=2)
    print(f"\nWrote {len(results_index)} run entries to {index_path}")

    failed = [r for r in results_index if r["status"] != "OK"]
    if failed:
        print(f"\n*** {len(failed)} FAILED RUNS (preserved, not deleted) ***")
        for f in failed:
            print(f"  {f['run_id']}: {f['reason']}")


if __name__ == "__main__":
    main()
