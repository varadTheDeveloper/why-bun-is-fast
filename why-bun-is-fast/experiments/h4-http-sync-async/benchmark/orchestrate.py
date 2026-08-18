#!/usr/bin/env python3
"""
H4 orchestration script. Runs Case A (sync) and Case B (async, setImmediate-
suspended — see case-b-async.ts for why) at two concurrency levels (50
primary, 1 secondary), 10 independent runs each (fresh server process per
run), with lightweight CPU sampling during each timed window.

Documented deviations from Stage 12 default (same reasoning as H6 — 2-vCPU
shared sandbox, session wall-clock constraints):
  - timed window 10s instead of 60s
  - warmup 5s instead of 30s
Concurrency itself is NOT deviated: c=50 was empirically verified (pre-run
check) to run with zero errors/timeouts on this machine for this handler, so
the Stage 12-specified 50/1 pair is used as literally specified.
"""
import json
import os
import signal
import subprocess
import time
import urllib.request

import psutil

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BENCH_DIR, "..", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

WARMUP_SECS = 5
TIMED_SECS = 10
RUNS_PER_COMBO = 10
COOLDOWN_SECS = 1.5
CONCURRENCIES = [50, 1]  # primary, secondary

COMBOS = [
    {"case": "A", "port": 4100, "cmd": ["bun", "run", "case-a-sync.ts"]},
    {"case": "B", "port": 4101, "cmd": ["bun", "run", "case-b-async.ts"]},
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


def run_autocannon(port, duration, connections, sample_pid=None):
    cmd = ["autocannon", "-c", str(connections), "-d", str(duration), "-j", f"http://127.0.0.1:{port}/"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    samples = []
    sys_samples = []
    server_ps = psutil.Process(sample_pid) if sample_pid else None
    if server_ps:
        server_ps.cpu_percent(interval=None)  # prime
    psutil.cpu_percent(interval=None)  # prime system-wide

    start = time.time()
    while proc.poll() is None and (time.time() - start) < duration + 25:
        time.sleep(0.5)
        try:
            if server_ps:
                samples.append(server_ps.cpu_percent(interval=None))
            sys_samples.append(psutil.cpu_percent(interval=None))
        except Exception:
            pass

    stdout, stderr = proc.communicate(timeout=30)
    if proc.returncode != 0:
        return None, stderr, samples, sys_samples
    try:
        return json.loads(stdout), stderr, samples, sys_samples
    except json.JSONDecodeError:
        return None, f"JSON parse failure: {stdout[:300]}", samples, sys_samples


def main():
    results_index = []
    for concurrency in CONCURRENCIES:
        for combo in COMBOS:
            case, port, cmd = combo["case"], combo["port"], combo["cmd"]
            print(f"\n=== Case {case}, concurrency={concurrency} (port {port}) ===", flush=True)
            for run_idx in range(1, RUNS_PER_COMBO + 1):
                run_id = f"case{case}-c{concurrency}-run{run_idx:02d}"
                log_path = os.path.join("/tmp/h4logs", f"{run_id}.log")
                os.makedirs("/tmp/h4logs", exist_ok=True)
                entry = {
                    "run_id": run_id, "case": case, "concurrency": concurrency,
                    "timed_duration_s": TIMED_SECS, "warmup_duration_s": WARMUP_SECS,
                    "port": port, "started_at_unix": time.time(),
                    "status": "FAILED", "reason": None,
                }

                log_f = open(log_path, "w")
                try:
                    proc = subprocess.Popen(cmd, cwd=BENCH_DIR, stdout=log_f, stderr=subprocess.STDOUT,
                                             preexec_fn=os.setsid, env={**os.environ, "PORT": str(port)})
                except Exception as e:
                    entry["reason"] = f"spawn failure: {e}"
                    results_index.append(entry)
                    continue

                try:
                    if not wait_ready(port, timeout=10):
                        entry["reason"] = "server did not become ready within 10s"
                        results_index.append(entry)
                        continue

                    warm_result, warm_err, _, _ = run_autocannon(port, WARMUP_SECS, concurrency)
                    if warm_result is None:
                        entry["reason"] = f"warmup failed: {warm_err}"
                        results_index.append(entry)
                        continue

                    result, err, cpu_samples, sys_cpu_samples = run_autocannon(
                        port, TIMED_SECS, concurrency, sample_pid=proc.pid)
                    if result is None:
                        entry["reason"] = f"timed run failed: {err}"
                        results_index.append(entry)
                        continue

                    entry["status"] = "OK"
                    entry["autocannon_raw"] = result
                    entry["server_cpu_percent_samples"] = cpu_samples
                    entry["system_cpu_percent_samples"] = sys_cpu_samples
                    entry["server_cpu_percent_avg"] = (sum(cpu_samples) / len(cpu_samples)) if cpu_samples else None
                    entry["system_cpu_percent_avg"] = (sum(sys_cpu_samples) / len(sys_cpu_samples)) if sys_cpu_samples else None

                    raw_run_path = os.path.join(RAW_DIR, f"{run_id}.json")
                    with open(raw_run_path, "w") as f:
                        json.dump(result, f, indent=2)

                    cpu_str = f"{entry['server_cpu_percent_avg']:.1f}" if entry['server_cpu_percent_avg'] is not None else "N/A"
                    print(f"  run {run_idx}/{RUNS_PER_COMBO}: "
                          f"{result['requests']['average']:.0f} req/s avg, "
                          f"p50={result['latency']['p50']}ms p99={result['latency']['p99']}ms "
                          f"errors={result['errors']} timeouts={result['timeouts']} "
                          f"server_cpu%={cpu_str}",
                          flush=True)

                finally:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass
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
