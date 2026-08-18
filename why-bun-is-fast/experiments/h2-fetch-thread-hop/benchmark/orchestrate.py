#!/usr/bin/env python3
"""
H2 orchestration — fetch() thread-hop overhead (Bun vs Node vs Deno, client
side only; server is a single fixed Python implementation used identically
for all three).

Design recap (see fetch-bench.js and server.py for full detail):
  - Server: Python stdlib http.server, 127.0.0.1 only, TCP_NODELAY set on
    every accepted socket (removes a Nagle/delayed-ACK confound found
    during smoke-testing — see server.py comment). GET / for keep-alive
    (persistent connection), GET /cold for cold (server sends
    `Connection: close`, forcing a fresh TCP connection per request via
    standard HTTP/1.1 semantics — not client-specific configuration).
  - Client: fetch-bench.js, identical script run under all three runtimes.
    Warmup uses a chunked, predefined stability check (requiring TWO
    CONSECUTIVE passing attempts, not just one — a single-attempt check
    was found during smoke-testing to be fooled by an intermediate
    JIT-tiering plateau in Node's fetch specifically). Warmup and timed
    measurement run in the SAME process for keep-alive mode (a two-process
    design was tried first and found to silently discard JIT/connection
    warmup between phases — documented in fetch-bench.js's header comment
    as a caught-and-fixed methodology flaw, not hidden).
  - Cold: 10 independent fresh-process runs x 10 timed cold requests per
    runtime = 100 cold latency samples/runtime.
  - Keep-alive: 10 independent fresh-process runs x 10,000 timed
    sequential requests per runtime = 100,000 timed latency samples/runtime.
  - Connection-reuse verification via the server's /stats endpoint before
    and after each run (adjusting for the verification query's own
    connection — see note in query_stats_delta()).
"""
import json
import os
import subprocess
import time
import urllib.request

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

SERVER_PORT = 8765
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"

COLD_RUNS_PER_RUNTIME = 10
COLD_TIMED_PER_RUN = 10  # -> 100 cold samples/runtime

KEEPALIVE_RUNS_PER_RUNTIME = 10
KEEPALIVE_TIMED_PER_RUN = 10000  # -> 100,000 timed samples/runtime

RUNTIMES = {
    "bun": ["bun", os.path.join(HERE, "fetch-bench.js")],
    "node": ["node", os.path.join(HERE, "fetch-bench.js")],
    "deno": ["deno", "run", "--allow-net", os.path.join(HERE, "fetch-bench.js")],
}


def http_get_json(path):
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as resp:
        return json.loads(resp.read())


def http_post(path):
    req = urllib.request.Request(f"{BASE_URL}{path}", method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=5):
        pass


def query_stats_delta(before, after):
    """
    Both `before` and `after` are /stats responses (each of which included
    its own verification connection). connections_delta = after - before
    equals (client connections opened during the run) + 1 (the `after`
    query's own connection) — the `before` query's own connection is
    already baked into `before` itself, so it cancels out. Hence:
    actual_client_connections = (after.connections - before.connections) - 1.
    """
    return {
        "client_connections_opened": (after["connections"] - before["connections"]) - 1,
        "keepalive_requests_delta": after["keepalive_requests"] - before["keepalive_requests"],
        "cold_requests_delta": after["cold_requests"] - before["cold_requests"],
    }


def run_client(runtime_name, mode, timed_count, run_id, server_proc, timeout=120):
    cmd = RUNTIMES[runtime_name] + [BASE_URL, mode, str(timed_count)]
    stats_before = http_get_json("/stats")

    server_ps = psutil.Process(server_proc.pid)
    server_ps.cpu_percent(interval=None)  # prime
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return {"run_id": run_id, "status": "FAILED", "reason": f"TIMEOUT: {e}"}
    wall = time.time() - start
    server_cpu_percent = server_ps.cpu_percent(interval=None)

    stats_after = http_get_json("/stats")
    conn_info = query_stats_delta(stats_before, stats_after)

    if proc.returncode != 0:
        return {"run_id": run_id, "status": "FAILED",
                "reason": f"nonzero exit {proc.returncode}: stderr={proc.stderr[-1500:]}"}
    try:
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"run_id": run_id, "status": "FAILED", "reason": f"unparseable stdout: {proc.stdout[:500]}"}

    entry = {
        "run_id": run_id, "runtime": runtime_name, "mode": mode,
        "timed_count_requested": timed_count, "wall_clock_process_s": wall,
        "server_cpu_percent_during_run": server_cpu_percent,
        "connection_verification": conn_info,
        "status": "OK",
    }
    entry.update(parsed)
    return entry


def main():
    print("=== Starting server ===", flush=True)
    server_proc = subprocess.Popen(
        ["python3", os.path.join(HERE, "server.py"), str(SERVER_PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(1.0)
    # readiness check
    for _ in range(20):
        try:
            http_get_json("/stats")
            break
        except Exception:
            time.sleep(0.25)
    else:
        print("SERVER FAILED TO START"); return

    print("=== Smoke test (Section 18) ===", flush=True)
    smoke_results = {}
    http_post("/reset")
    for runtime_name in RUNTIMES:
        r_keepalive = run_client(runtime_name, "keepalive", 5, f"smoke-{runtime_name}-keepalive", server_proc)
        r_cold = run_client(runtime_name, "cold", 5, f"smoke-{runtime_name}-cold", server_proc)
        smoke_results[runtime_name] = {"keepalive": r_keepalive, "cold": r_cold}
        ka_ok = r_keepalive.get("allStatus200") and r_keepalive.get("allBodyMatch") and r_keepalive.get("allContentLengthMatch")
        cold_ok = r_cold.get("allStatus200") and r_cold.get("allBodyMatch") and r_cold.get("allContentLengthMatch")
        # connection-reuse sanity: keepalive should open far fewer connections than
        # (warmup + timed) requests; cold should open ~1 connection per
        # (warmup + timed) request — cold's warmup is a fixed, known count
        # (COLD_FIXED_WARMUP_COUNT in fetch-bench.js), so the expected total is
        # warmupFinalIterations + timedCount.
        ka_conn_ok = r_keepalive.get("connection_verification", {}).get("client_connections_opened", 999) < 5
        cold_expected_total = r_cold.get("warmupFinalIterations", 0) + r_cold.get("timedCount", 0)
        cold_opened = r_cold.get("connection_verification", {}).get("client_connections_opened", -999999)
        cold_conn_ok = cold_expected_total > 0 and abs(cold_opened - cold_expected_total) <= max(2, 0.02 * cold_expected_total)
        print(f"  {runtime_name}: keepalive_ok={ka_ok} keepalive_conn_reuse_ok={ka_conn_ok} "
              f"(opened {r_keepalive.get('connection_verification',{}).get('client_connections_opened')} conns, "
              f"warmup={r_keepalive.get('warmupFinalStatus')}/{r_keepalive.get('warmupFinalIterations')}) "
              f"cold_ok={cold_ok} cold_fresh_conn_ok={cold_conn_ok} "
              f"(opened {cold_opened} conns for {cold_expected_total} expected reqs)", flush=True)
        if not (ka_ok and cold_ok and ka_conn_ok and cold_conn_ok):
            print(f"*** SMOKE TEST FAILED for {runtime_name} — STOPPING per Section 18 ***", flush=True)
            with open(os.path.join(RAW_DIR, "smoke_test_results.json"), "w") as f:
                json.dump(smoke_results, f, indent=2, default=str)
            server_proc.terminate()
            return
    with open(os.path.join(RAW_DIR, "smoke_test_results.json"), "w") as f:
        json.dump(smoke_results, f, indent=2, default=str)
    print("=== Smoke test PASSED for all three runtimes ===", flush=True)

    results_index = []

    # --- COLD phase ---
    for runtime_name in RUNTIMES:
        print(f"\n=== COLD: {runtime_name} ({COLD_RUNS_PER_RUNTIME} runs x {COLD_TIMED_PER_RUN} timed) ===", flush=True)
        for run_idx in range(1, COLD_RUNS_PER_RUNTIME + 1):
            run_id = f"{runtime_name}-cold-run{run_idx:02d}"
            entry = run_client(runtime_name, "cold", COLD_TIMED_PER_RUN, run_id, server_proc)
            with open(os.path.join(RAW_DIR, f"{run_id}.json"), "w") as f:
                json.dump(entry, f, indent=2)
            results_index.append({k: v for k, v in entry.items() if k != "latenciesNs"})  # index omits full arrays
            if entry["status"] == "OK":
                import statistics as st
                lat = entry["latenciesNs"]
                print(f"  run {run_idx}: median={st.median(lat)/1000:.1f}us warmup={entry.get('warmupFinalStatus')}"
                      f"({entry.get('warmupFinalIterations')}) conns_opened={entry['connection_verification']['client_connections_opened']}", flush=True)
            else:
                print(f"  run {run_idx}: FAILED — {entry.get('reason','')[:200]}", flush=True)

    # --- KEEPALIVE phase ---
    for runtime_name in RUNTIMES:
        print(f"\n=== KEEPALIVE: {runtime_name} ({KEEPALIVE_RUNS_PER_RUNTIME} runs x {KEEPALIVE_TIMED_PER_RUN} timed) ===", flush=True)
        for run_idx in range(1, KEEPALIVE_RUNS_PER_RUNTIME + 1):
            run_id = f"{runtime_name}-keepalive-run{run_idx:02d}"
            entry = run_client(runtime_name, "keepalive", KEEPALIVE_TIMED_PER_RUN, run_id, server_proc, timeout=180)
            with open(os.path.join(RAW_DIR, f"{run_id}.json"), "w") as f:
                json.dump(entry, f, indent=2)
            results_index.append({k: v for k, v in entry.items() if k != "latenciesNs"})
            if entry["status"] == "OK":
                import statistics as st
                lat = entry["latenciesNs"]
                print(f"  run {run_idx}: median={st.median(lat)/1000:.1f}us throughput={entry['throughputPerSec']:.0f}/s "
                      f"warmup={entry.get('warmupFinalStatus')}({entry.get('warmupFinalIterations')}) "
                      f"conns_opened={entry['connection_verification']['client_connections_opened']} "
                      f"wall={entry['wall_clock_process_s']:.2f}s", flush=True)
            else:
                print(f"  run {run_idx}: FAILED — {entry.get('reason','')[:200]}", flush=True)

    with open(os.path.join(RAW_DIR, "run_index.json"), "w") as f:
        json.dump(results_index, f, indent=2)

    n_ok = sum(1 for r in results_index if r["status"] == "OK")
    n_failed = sum(1 for r in results_index if r["status"] != "OK")
    print(f"\nWrote {len(results_index)} entries ({n_ok} OK, {n_failed} FAILED) to raw/run_index.json", flush=True)

    server_proc.terminate()
    try:
        server_proc.wait(timeout=5)
    except Exception:
        server_proc.kill()


if __name__ == "__main__":
    main()
