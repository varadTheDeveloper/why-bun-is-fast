# Environment

The single most important limitation of this research: **every experiment ran on the same shared, non-dedicated hardware.** This page documents exactly what that hardware was, so results can be interpreted correctly and so anyone attempting to reproduce them knows what to expect.

## Hardware

| Property | Value |
|---|---|
| CPU | Intel(R) Xeon(R) Processor @ 2.80GHz |
| Physical cores | 2 |
| Logical cores | 2 |
| RAM | 7.8 GB |
| Architecture | x86_64 |
| CPU governor | Not exposed / not controllable in this sandbox |
| Virtualization | VM (KVM/Firecracker guest) |
| Dedicated or shared | **Shared** — the same cloud sandbox was used across all six experiments, not dedicated benchmarking hardware |
| OS | Ubuntu 24.04.4 LTS (Noble Numbat) |
| Kernel | 6.18.5-fc-v20 |

Exact per-experiment values (load average at run start, timestamps, etc.) are recorded in each experiment's `results/metadata.json`.

## Why this matters

A shared 2-vCPU VM means: no CPU pinning, no guaranteed cache isolation, and a "noisy neighbor" risk from other workloads on the same physical host. This is explicitly why every experiment is classified PILOT/LIMITED rather than a definitive production benchmark, and why H1 in particular (which measures nanosecond-scale native-call overhead) is the single most noise-sensitive experiment in the set. It's also part of why H6's absolute throughput numbers should not be read as representative of dedicated production hardware — the *relative* comparison across runtimes on the same shared machine is the meaningful signal, not the absolute req/s figures.

## Runtime versions and build track

| Runtime | Version | Build |
|---|---|---|
| Bun | 1.3.13 | Release, pre-installed |
| Node.js | v22.22.2 | Release, pre-installed |
| Deno | 2.9.5 (stable) | Release binary |

**Release builds were used for all three runtimes in every experiment**, not source-controlled builds pinned to an exact commit. This was a deliberate fallback, not an oversight: building Bun from source at the project's originally intended pinned commit requires a specific Rust nightly toolchain (`nightly-2026-07-20`), and this sandbox's network access to `static.rust-lang.org` failed with a hard connection error (`curl` returned status `000`) when this was attempted — verified directly, not assumed. Because a from-source Bun build was infeasible, the release-build track was used consistently for all three runtimes rather than mixing source-built and release-built binaries, which would have introduced its own comparability problem.

Where source code is cited for mechanism-level claims (e.g., Bun's `#[cold]` startup-layout attributes, or the header/URL clone on request suspension), those citations reference a separately maintained source clone pinned at `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4` — used only to read and verify source-level claims, not built or benchmarked. This distinction (source read for mechanism verification vs. binary actually benchmarked) is stated explicitly in each experiment's `results/metadata.json` under `runtime_build_track`.

## A note on machine identifiers

Each experiment's `results/metadata.json` includes a `machine.anonymized_machine_id` field. This has been generalized to `shared-cloud-sandbox-vm` in this public repository — the original internal identifier referenced this project's specific cloud research environment and has been removed as an infrastructure detail that doesn't affect reproducibility. Every reproducibility-relevant environment detail (CPU, RAM, OS, kernel, shared/dedicated status, load average) is preserved unchanged.
