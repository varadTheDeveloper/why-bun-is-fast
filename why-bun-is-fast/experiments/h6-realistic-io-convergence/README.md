# H6 — Realistic-I/O HTTP convergence

**Status: protocol only. Not yet executed. Classification: MUST RUN — highest thesis importance of the seven.**

## Purpose

Test whether the Bun/Node/Deno HTTP-throughput gap — large under plaintext hello-world benchmarks — shrinks substantially once realistic application I/O (a database round-trip) dominates per-request time, reproducing the pattern two independent external sources (Evert Heylen, HackerNoon — Stage 10) found on their own hardware and workloads.

## Hypothesis (H6, from Stage 11 — unmodified)

The Bun/Node/Deno HTTP-throughput gap shrinks substantially once realistic I/O (a DB round-trip) dominates the workload.

## Mechanism(s)

Deliberately a cluster-level test, not a single-mechanism isolation: M9 (fetch/HTTP threading asymmetry), M15–M18 (HTTP server implementation, copy boundary, write-batching, header laziness), M22 (HTTP pooling). This experiment tests whether the cluster's combined effect survives realistic application logic — not which specific mechanism within the cluster is responsible (that would be follow-up work, not this experiment's job).

## Runtime versions / commits

- **Track used: release comparison, primary.** This experiment is about real-world relevance under realistic workloads — what a user actually installs is the right comparison here, more than a specific pinned commit. A source-controlled cross-check (Bun `8326d1bd39a96f1f298c3de195aad15972d4f3b4`, Node `ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85`) may be run secondarily if results look surprising and a mechanism-level explanation is sought.
- Deno: release build; source-controlled cross-check blocked on the same commit-pin gap as H1/H2 if pursued.
- Record exact versions into `metadata.json`.

## Hardware / environment

Per Section 4. This experiment is the least sensitive of the set to host-level micro-noise (the I/O step itself, and its associated wait time, tends to dominate and dampen sensitivity to scheduling jitter) but is the most sensitive to *database*-side noise — the database instance, its configuration, and its own load state must be recorded and held constant across all runtime comparisons within a single experimental session.

## Setup — the single most important section of this protocol

1. Choose one database engine (proposed: PostgreSQL, matching both external sources Stage 10 found, for direct comparability) with a fixed schema and seed dataset.
2. Implement **Workload A (runtime-dominated):** `GET` → handler → return a tiny fixed JSON response. No I/O.
3. Implement **Workload B (realistic I/O):** `GET` → handler → one parameterized SQL query (issued via each runtime's native/standard database client — **no ORM**, to remove query-layer choice as a variable) → minimal object processing (e.g., pick two fields off the returned row) → JSON response.
4. **The query, schema, seed data, response shape, and application logic in Workload B must be textually identical (modulo unavoidable per-runtime driver API syntax) across all three runtimes.** This is the exact discipline the Trigger.dev case study lacked (Stage 10) and the reason that case study could not be used as evidence — this experiment exists specifically to do correctly what that one did not.
5. Use the same database connection-pool size/configuration across all three runtime implementations.
6. Use the same load-generator tool and configuration for both workloads and all three runtimes.

## Commands

```sh
# placeholder — finalized at Stage 13
docker compose up -d postgres    # fixed DB instance, identical across all runs
bun run workload-a-server.ts &
oha -z 30s -c 50 http://127.0.0.1:PORT/
bun run workload-b-server.ts &
oha -z 30s -c 50 http://127.0.0.1:PORT/
# repeat for node, deno servers against the SAME postgres instance/schema/seed data
```

## Warmup

30 seconds of untimed load per run, including enough requests to warm the database's own query cache/connection pool to steady state (not just JIT warmup) — I/O-bound warmup needs to account for both layers.

## Repetitions

- Timed window: 60 seconds per run.
- Independent runs: minimum 10 per (runtime × workload) combination — 3 runtimes × 2 workloads × 10 runs = 60 total runs minimum.
- Concurrency: primary at concurrency = 50; a secondary sweep (10, 50, 200) recommended if time permits, to check whether the convergence pattern (if found) holds across concurrency levels or is concurrency-dependent.
- Outliers: not discarded by default.

## Metrics

- **Primary:** throughput (req/sec), median/p95/p99 latency — for both Workload A and Workload B, all three runtimes.
- **Secondary:** CPU utilization (server-side), database query latency in isolation (to confirm the DB itself isn't the uncontrolled variable across runtime comparisons — the DB's own response time should be comparable regardless of which runtime is querying it, and this should be checked, not assumed).

## Statistical method

Mean/median throughput across ≥10 runs with CI (n≥5 satisfied); median/p95/p99 latency, per Section 5. **Primary derived quantity: the *relative gap* between runtimes (e.g., fastest-runtime-throughput ÷ slowest-runtime-throughput) computed separately for Workload A and Workload B**, since the hypothesis is about the gap shrinking, not about any single runtime's absolute number.

## Expected result (directional only — no specific percentage predicted)

If the hypothesis holds, the relative throughput gap between the three runtimes under Workload B should be substantially smaller than under Workload A — consistent with, though not necessarily matching the exact magnitude of, the pattern Evert Heylen and HackerNoon each found independently (Stage 10).

## Falsifier

The relative gap under Workload B remains approximately the same magnitude as under Workload A. This is treated as at least as important a possible outcome as confirmation — it would directly weaken Stage 11's Candidate C working synthesis, and this experiment is designed to be able to produce that result, not just confirm the expected one.

## Confounders / risks

- Database driver/client library maturity differs across runtimes (e.g., a runtime's Postgres driver could itself be faster/slower independent of the runtime's own HTTP-path mechanisms) — this is a real, hard-to-fully-eliminate confound; the results write-up must discuss it explicitly, and where possible, the isolated database-query-latency secondary metric should be used to sanity-check that the DB layer itself isn't silently driving the result.
- This experiment cannot, on its own, attribute a confirmed gap-shrinkage to any specific mechanism within the HTTP cluster — that would require follow-up experiments isolating M9/M15–M18/M22 individually under the same I/O-bound conditions, out of scope here.
