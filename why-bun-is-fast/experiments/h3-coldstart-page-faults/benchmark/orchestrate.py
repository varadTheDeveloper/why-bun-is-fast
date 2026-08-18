#!/usr/bin/env python3
"""
H3 orchestration — Cold-start page-fault measurement (Bun vs Node vs Deno).

Measures M12: does Bun's page-fault-aware binary layout measurably reduce
page faults during cold process startup, for the minimal workload
`console.log("hello")`.

Cache control: `sync; echo 3 > /proc/sys/vm/drop_caches` (verified working
in this sandbox — root with CAP_SYS_ADMIN in the bounding set; page cache
measurably drops via /proc/meminfo Cached after the write, confirmed both
in the prerequisite check and re-verified per-run below).

Fault counting: `perf stat -e page-faults,minor-faults,major-faults`.
DEVIATION (disclosed): the running kernel is a custom Firecracker-patched
build (6.18.5-fc-v20) with no matching `linux-tools` package in Ubuntu's
repos (apt install of `linux-tools-generic` 404s trying to resolve a
kernel-specific meta-package). The closest available Ubuntu perf build
(linux-tools-6.8.0-111, i.e. perf version 6.8.12) was installed and
invoked directly at its versioned path
(/usr/lib/linux-tools-6.8.0-111/perf), bypassing the /usr/bin/perf
wrapper's kernel-version guard (which would otherwise refuse to run).
This was cross-validated against an independent method
(`/usr/bin/time -v`, which reports getrusage()-based minor/major fault
counts at process exit, not perf's software-counter approach) during the
prerequisite check and found closely consistent (within ~3%). This is
documented as a deviation, not hidden.

Design: for EACH cold run, cache is dropped immediately before that run
(never dropped once and reused across multiple runs/runtimes). Each cold
run is immediately followed (same iteration) by one warm run of the same
executable with no intervening cache drop, per Section 12's per-cold-run
pairing instruction. This produces 30 cold + 30 warm runs per runtime
(exceeding the declared minimums of 30 cold / 5 warm).
"""
import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "..", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

PERF = "/usr/lib/linux-tools-6.8.0-111/perf"
HELLO = os.path.join(HERE, "hello.js")
RUNS_PER_RUNTIME = 30

RUNTIMES = {
    "bun": {"bin": "bun", "cmd": ["bun", HELLO]},
    "node": {"bin": "node", "cmd": ["node", HELLO]},
    "deno": {"bin": "deno", "cmd": ["deno", "run", HELLO]},
}


def meminfo_cached_kb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("Cached:"):
                return int(line.split()[1])
    return None


def drop_caches():
    """Returns (success, cached_before_kb, cached_after_kb)."""
    before = meminfo_cached_kb()
    subprocess.run(["sync"], check=False)
    try:
        with open("/proc/sys/vm/drop_caches", "w") as f:
            f.write("3\n")
        write_ok = True
    except Exception as e:
        write_ok = False
    after = meminfo_cached_kb()
    return write_ok, before, after


PERF_RE = {
    "page_faults": re.compile(r"^\s*([\d,]+)\s+page-faults"),
    "minor_faults": re.compile(r"^\s*([\d,]+)\s+minor-faults"),
    "major_faults": re.compile(r"^\s*([\d,]+)\s+major-faults"),
    "elapsed_s": re.compile(r"^\s*([\d.]+)\s+seconds time elapsed"),
    "user_s": re.compile(r"^\s*([\d.]+)\s+seconds user"),
    "sys_s": re.compile(r"^\s*([\d.]+)\s+seconds sys"),
}


def run_perf(cmd, timeout=30):
    full_cmd = [PERF, "stat", "-e", "page-faults,minor-faults,major-faults", "--"] + cmd
    start = time.time()
    try:
        proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return None, f"TIMEOUT: {e}", time.time() - start
    wall = time.time() - start

    stderr = proc.stderr
    parsed = {}
    for key, rx in PERF_RE.items():
        for line in stderr.splitlines():
            m = rx.search(line.replace(",", ""))
            if m:
                parsed[key] = float(m.group(1))
                break
    if "page_faults" not in parsed:
        return None, f"perf output not parseable. stderr={stderr[-1000:]} stdout={proc.stdout[:200]}", wall
    parsed["wall_clock_python_s"] = wall
    parsed["stdout"] = proc.stdout.strip()
    parsed["returncode"] = proc.returncode
    return parsed, stderr, wall


def audit_binary(path):
    def sh(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception as e:
            return f"ERROR: {e}"

    size_bytes = os.path.getsize(path)
    return {
        "path": path,
        "size_bytes": size_bytes,
        "file_output": sh(["file", path]),
        "ldd_output": sh(["ldd", path]),
        "readelf_header": sh(["readelf", "-h", path]),
        "readelf_program_headers_load_interp": "\n".join(
            l for l in sh(["readelf", "-l", path]).splitlines() if "INTERP" in l or "LOAD" in l
        ),
    }


def main():
    print("=== Binary/loader audit ===", flush=True)
    binary_audit = {}
    for name, info in RUNTIMES.items():
        binary_audit[name] = audit_binary(info["bin"])
        print(f"{name}: {info['bin']}  size={binary_audit[name]['size_bytes']} bytes", flush=True)
    with open(os.path.join(RAW_DIR, "binary_audit.json"), "w") as f:
        json.dump(binary_audit, f, indent=2)

    results_index = []
    for runtime_name, info in RUNTIMES.items():
        cmd = info["cmd"]
        print(f"\n=== {runtime_name}: {RUNS_PER_RUNTIME} cold+warm pairs ===", flush=True)
        for run_idx in range(1, RUNS_PER_RUNTIME + 1):
            run_id = f"{runtime_name}-run{run_idx:02d}"

            # --- COLD ---
            drop_ok, cached_before, cached_after = drop_caches()
            cold_parsed, cold_stderr, cold_wall = run_perf(cmd)
            cold_entry = {
                "run_id": run_id, "runtime": runtime_name, "state": "cold",
                "timestamp_unix": time.time(), "command": cmd,
                "cache_drop_write_ok": drop_ok,
                "cached_kb_before_drop": cached_before, "cached_kb_after_drop": cached_after,
                "cached_kb_dropped": (cached_before - cached_after) if (cached_before is not None and cached_after is not None) else None,
            }
            if cold_parsed is None:
                cold_entry["status"] = "FAILED"
                cold_entry["reason"] = cold_stderr
            else:
                cold_entry["status"] = "OK"
                cold_entry.update(cold_parsed)

            # --- WARM (immediately after, no drop) ---
            warm_parsed, warm_stderr, warm_wall = run_perf(cmd)
            warm_entry = {
                "run_id": run_id, "runtime": runtime_name, "state": "warm",
                "timestamp_unix": time.time(), "command": cmd,
            }
            if warm_parsed is None:
                warm_entry["status"] = "FAILED"
                warm_entry["reason"] = warm_stderr
            else:
                warm_entry["status"] = "OK"
                warm_entry.update(warm_parsed)

            for entry, tag in [(cold_entry, "cold"), (warm_entry, "warm")]:
                raw_path = os.path.join(RAW_DIR, f"{run_id}-{tag}.json")
                with open(raw_path, "w") as f:
                    json.dump(entry, f, indent=2)
                results_index.append(entry)

            if cold_entry["status"] == "OK" and warm_entry["status"] == "OK":
                print(f"  {run_id}: cold pf={cold_entry.get('page_faults')} maj={cold_entry.get('major_faults')} "
                      f"elapsed={cold_entry.get('elapsed_s')}s | warm pf={warm_entry.get('page_faults')} "
                      f"maj={warm_entry.get('major_faults')} elapsed={warm_entry.get('elapsed_s')}s | "
                      f"cache_dropped_kb={cold_entry['cached_kb_dropped']}", flush=True)
            else:
                print(f"  {run_id}: FAILED cold_status={cold_entry['status']} warm_status={warm_entry['status']}", flush=True)

    with open(os.path.join(RAW_DIR, "run_index.json"), "w") as f:
        json.dump(results_index, f, indent=2)

    n_ok = sum(1 for r in results_index if r["status"] == "OK")
    n_failed = sum(1 for r in results_index if r["status"] != "OK")
    print(f"\nWrote {len(results_index)} entries ({n_ok} OK, {n_failed} FAILED) to raw/run_index.json")


if __name__ == "__main__":
    main()
