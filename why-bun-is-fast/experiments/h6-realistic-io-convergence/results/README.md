# H6 Results — Realistic-I/O HTTP Convergence

**Status: EXECUTED. First real Stage 13 result. Pending your review before H4 begins.**

## Experiment

Tests whether the Bun/Node/Deno HTTP-throughput gap — large under a plaintext runtime-dominated workload — shrinks substantially once a realistic, controlled database round-trip is added to the request path. Cluster-level test (M9, M15–M18, M22 collectively); does not isolate any one mechanism.

## Hypothesis (H6, unmodified from Stage 11)

> The Bun/Node/Deno HTTP-throughput gap shrinks substantially once realistic I/O (a DB round-trip) dominates the workload.

## Method

Followed `experiments/h6-realistic-io-convergence/README.md` and `notes/12-experiment-design.md` with three **documented deviations**, all driven by this session's actual execution environment rather than convenience:

| Parameter | Stage 12 spec | Actually run | Reason |
|---|---|---|---|
| Concurrency | 50 | **20** | Execution machine has 2 vCPUs total, shared with the load generator, server, and database. Concurrency 50 on 2 cores would mostly measure OS scheduling contention, not runtime behavior. |
| Timed window | 60s | **10s** | Session wall-clock constraint. Compensated with 10 independent runs per combo (unchanged from spec) rather than one long run, per Stage 12 Section 5's own preference for run-to-run repetition over single-long-run duration. |
| Warmup | 30s untimed | **5s untimed** | Same wall-clock constraint. Verified sufficient by inspecting run-to-run stability (CV 2.7%–6.7% across all six combos — see Results) rather than assumed. |

Everything else followed the protocol as specified: identical database/schema/seed data/query across all three runtimes, identical response-shape validation before data collection, 10 independent runs per (runtime × workload) combination (fresh process per run), raw data preserved unaggregated, falsifier fixed before execution, no result discarded or replaced.

**One additional deliberate design decision, not a deviation but a choice made explicit here:** all three runtimes' Workload B servers use the same `pg` npm package (v8.13.1) as their PostgreSQL driver — including Bun, which has its own native `Bun.sql` client that was *not* used. This removes database-driver implementation as a confound axis entirely (all three runtimes execute the same driver code against the same database), at the cost of not testing each runtime's "best available" native database path. See Confounders below for what this means for interpretation.

## Environment

Full machine-readable record: `results/metadata.json`. Headline facts, stated plainly because they matter for how much weight this result should carry:

- **2 physical/logical CPU cores** (Intel Xeon @ 2.80GHz, KVM-virtualized), **shared cloud sandbox**, not dedicated benchmarking hardware. This is exactly the constraint Stage 12 Section 4 flagged in advance as a risk for this project's environment.
- CPU steal time spot-checked post-run at ~0.01% (negligible at that instant) — not continuously monitored throughout the full ~17.6-minute run.
- Load generator (autocannon, itself a Node.js process) ran on the **same 2-core machine** as the server under test and the database — not a separate load-generation host. This is a real, structural limitation: the load generator competes with the server for the same cores.
- OS: Ubuntu 24.04.4 LTS, kernel 6.18.5.

**This environment does not meet Stage 12's stated quality bar for authoritative, production-scale measurement.** It is used here anyway, per Stage 12/13's own instruction ("only proceed if the resulting data can reasonably answer H6"), because: (1) all six runtime×workload combinations experienced the *identical* constraint, so a relative (not absolute) comparison is still meaningful in principle; (2) H6 was assessed in Stage 12 as the least timing-noise-sensitive of the seven experiments, since the I/O wait itself is expected to dominate; (3) the measured data turned out to have low run-to-run variance (CV mostly under 7%), suggesting the 2-core constraint did not make the measurement itself unstable, whatever else it may have done to the *magnitude* of any true effect. This reasoning is revisited honestly in Limitations below — low variance in a constrained measurement does not prove the constraint had no effect on the result, only that the constrained result itself is reproducible.

## Runtime versions

Release comparison track (per `notes/12-experiment-design.md` Section 3, H6's primary track):

- **Bun:** 1.3.13 (pre-installed in sandbox)
- **Node:** v22.22.2 (pre-installed in sandbox)
- **Deno:** 2.9.5 (downloaded from GitHub Releases this session — `deno.land/install.sh` returned HTTP 403 in this sandbox's network environment, worked around via direct GitHub release download, documented here rather than silently substituted)

None of these are the project's source-controlled pins (Bun `8326d1b…`, Node `ad7a5b8…`) — building from source was not attempted for this experiment, consistent with H6's protocol specifying the release track as primary. The Deno commit-pin gap (Stage 11 open item 27) remains open and unaffected by this run.

## Workload A — runtime-dominated

`GET /` → handler → `{"ok":true}`. No I/O, no DB, no middleware. Implemented via each runtime's own native HTTP server (`Bun.serve()`, `node:http.createServer`, `Deno.serve()`) — response bodies verified byte-identical across all three before data collection.

## Workload B — realistic I/O

`GET /` → handler → `SELECT id, name, email, balance_cents FROM accounts WHERE id = $1` (parameter fixed at `42` for every request, every run, every runtime) → `{"id":42,"name":"user_42","email":"user_42@example.test","balance_cents":5754}`. Query results and response bodies verified byte-identical across all three runtimes before data collection (see Pre-run validation below). Database: local PostgreSQL 16.13, 10,000 deterministic seed rows (no `random()` — fully reproducible from `benchmark/schema.sql`, checksum in `metadata.json`).

## Pre-run validation (completed before data collection)

1. ✅ Sent test requests to all six servers — all returned HTTP 200.
2. ✅ Verified HTTP responses byte-identical within each workload across runtimes (`{"ok":true}` for A; the fixed row for B).
3. ✅ Verified JSON response structure identical.
4. ✅ Verified database query results identical (same row, same field values, same types after `Number()` normalization of Postgres's `bigint`-as-string).
5. ✅ Verified connection-pool configuration identical (`pg.Pool`, `max: 10`, `idleTimeoutMillis: 30000`) across all three Workload B servers.
6. ⚠️ Load generator vs. CPU-bound: **not independently instrumented** (no per-process CPU sampling was captured during runs). Indirect evidence it wasn't badly CPU-bound: zero autocannon-reported errors/timeouts across all 60 runs, and Workload A throughput (tens of thousands of req/s) is far above what a CPU-starved load generator alone tends to produce — but this is inference, not a direct measurement, and is logged as a limitation.
7. ✅ Verified database not saturated: `max_connections = 100`; each server's pool caps at 10; only one server ever runs at a time (sequential execution, never concurrent).
8. ⚠️ Server CPU saturation: not independently instrumented for the same reason as (6) — a real gap, given the 2-core constraint (see Limitations).
9. ✅ All versions/metadata recorded (`results/metadata.json`).
10. ✅ Exact benchmark source preserved in `benchmark/` (schema, seed generator, all six server implementations, orchestration script, analysis script).

No validation step failed outright, but (6) and (8) are marked with a caveat rather than a clean pass — noted here rather than silently treated as fully verified.

## Warmup

Untimed 5s autocannon run per server, discarded, before each timed 10s run. This covers connection establishment and steady-state TCP/keep-alive behavior; for Workload B it also covers `pg.Pool` reaching steady state (pool connections are opened lazily on first query and then reused — a 5s warmup at concurrency 20 issues well over 1,000 queries before timing starts, more than enough to fill a 10-connection pool). JIT warmup (Section 6 of the design doc) is a secondary concern for handlers this simple; not separately verified via a flat-trend check, logged as a minor protocol gap relative to the ideal spec.

## Raw-data integrity

- 60/60 runs completed successfully. **Zero failed runs, zero errors, zero timeouts, zero non-2xx responses** across all 60 timed windows.
- Every run's full, unaggregated autocannon JSON output preserved individually: `raw/<runtime>-<workload>-run<NN>.json` (60 files).
- Consolidated run index with full metadata per run: `raw/run_index.json`.
- No data was trimmed, discarded, or replaced. No outliers were removed (none were flagged as worth removing — CV was low across the board).

## Statistical method

Per `notes/12-experiment-design.md` Section 5: median as primary throughput statistic, mean/stddev/CV as secondary, 95% CI computed (n=10 per combo, satisfies the n≥5 threshold; t-distribution, df=9, t=2.262). Latency reported as the median-across-runs of each run's own p50/p99 (autocannon reports per-run percentiles from that run's own request-latency histogram; we then take the median of those 10 per-run percentile values, per Stage 12's "median primary" rule applied consistently). **Note on p95:** autocannon's fixed percentile buckets do not include p95 exactly; `p97_5` is used as the closest available bucket and is labeled `latency_p95_proxy_p97_5_ms` throughout the raw summary — stated explicitly rather than silently mislabeled as p95.

## Results

Full machine-readable summary: `results/summary.json`. Headline numbers (median throughput, req/s, across 10 runs each):

| Runtime | Workload A (median req/s) | 95% CI | Workload B (median req/s) | 95% CI |
|---|---|---|---|---|
| Bun | 55,434 | (51,740 – 56,964) | **9,100** | (8,748 – 9,372) |
| Node | 47,398 | (45,473 – 48,949) | 8,519 | (8,363 – 8,785) |
| Deno | **58,012** | (56,855 – 59,054) | 8,053 | (7,844 – 8,281) |

Latency (median-of-per-run p50 / p99, milliseconds):

| Runtime | Workload A p50 / p99 | Workload B p50 / p99 |
|---|---|---|
| Bun | 0.0 / 1.0 | 1.5 / 6.0 |
| Node | 0.0 / 1.0 | 2.0 / 5.0 |
| Deno | 0.0 / 1.0 | 2.0 / 5.0 |

Run-to-run variance was low across all six combinations (coefficient of variation 2.7%–6.7% on throughput), indicating the measurement itself is reproducible under these fixed conditions, whatever else may be true about how well those conditions generalize (see Limitations).

### Primary derived quantity: relative gap (fastest ÷ slowest median throughput)

- **Workload A:** fastest (Deno, 58,012) ÷ slowest (Node, 47,398) = **1.224×**
- **Workload B:** fastest (Bun, 9,100) ÷ slowest (Deno, 8,053) = **1.130×**
- **Ratio of ratios (B ÷ A):** 0.923 — i.e., the relative gap in Workload B was about **92% the size** of the relative gap in Workload A. An **8% relative reduction**, not the "substantial" shrinkage the hypothesis predicts.

### Database

Query itself is a single indexed point lookup (`WHERE id = $1` on a `PRIMARY KEY` column) — cheap by realistic-application standards, contributing roughly 1–5ms to per-request latency (inferred from the ~1ms Workload A latency vs. ~2–6ms Workload B latency, not independently measured via isolated query timing — see Limitations). `pg_stat_activity` connection counts were sampled before/after each Workload B run as a saturation check (recorded per-run in `raw/run_index.json`); no sign of connection exhaustion or unbounded queueing was observed in spot checks during execution.

## Falsification status

**Falsifier (from the protocol):** "The relative gap under Workload B remains approximately the same magnitude as under Workload A."

**What was observed:** the relative gap shrank only 8% (1.224× → 1.130×) — not zero shrinkage, but far short of "substantially smaller." By the letter of the falsifier, an 8% reduction is closer to "approximately the same magnitude" than to "substantially smaller."

**Verdict: NOT SUPPORTED, for this specific controlled run, with an important scope qualification below.**

I am not calling this INCONCLUSIVE, because the measurement itself was clean (zero errors, low variance, validated identical workloads) and the falsifier's own wording ("approximately the same magnitude") is the better description of what was actually measured. But this verdict applies strictly to *this experiment, in this environment, with this workload* — see "What this does NOT establish" below for why it should not be read as a refutation of Stage 10's external evidence.

## What the result supports

- **FACT:** under this specific controlled comparison (2-vCPU sandbox, concurrency 20, a single indexed-point-lookup query, identical `pg` driver across all three runtimes), the relative throughput gap between the fastest and slowest runtime did not shrink substantially when a database round-trip was added to the request — it shrank by roughly 8%, from 1.224× to 1.130×.
- **FACT:** the *ranking* of runtimes inverted between workloads — Deno was fastest and Node slowest under Workload A; Bun was fastest and Deno slowest under Workload B. Neither runtime held the top spot in both workloads.
- **INFERENCE:** whatever mechanisms make one runtime faster than another for a trivial hello-world response are not the same mechanisms (or not weighted the same way) as whatever makes one runtime faster than another once a database call is added — the rank inversion is hard to explain otherwise.

## What it does NOT support

- Does **not** establish that Stage 10's external finding (Evert Heylen, HackerNoon — a much larger gap-shrinkage under realistic I/O) is wrong. Those sources used different hardware (not stated to be 2-vCPU-constrained), different and likely more elaborate application logic (a URL-shortener with validation; a Postgres-backed workload with more processing), and production-representative concurrency. This experiment's Workload B is a much lighter I/O step than "realistic application logic" typically implies — a single indexed lookup is close to the cheapest possible database operation, not representative of, say, a multi-table join, an ORM-mediated write, or a validation-heavy request.
- Does **not** isolate which of M9/M15–M18/M22 is responsible for anything observed here — this was a cluster-level test by design, per the protocol.
- Does **not** establish anything about Bun's, Node's, or Deno's behavior on non-shared, multi-core hardware — the 2-vCPU constraint is a first-order limitation on generalizability, discussed further below.
- Does **not** establish that Workload A's ranking (Deno fastest, Node slowest, Bun in between) reflects each runtime's "true" hello-world performance on adequate hardware — see Limitations.

## Surprising findings

1. **Bun was not the fastest runtime under the plaintext workload (Workload A).** Deno was fastest (58,012 req/s median), Bun second (55,434), Node third (47,398). This runs counter to the popular narrative Stage 10/11 already flagged as unreliable (Tier 5 "4x faster" claims), and is itself a small, controlled counter-data-point to any assumption that Bun automatically wins a bare hello-world HTTP benchmark.
2. **Bun was fastest under the I/O-bound workload (Workload B)**, inverting its Workload A position relative to Deno. This is the more interesting result for H6's actual question — but "fastest under I/O" is not the same claim as "the gap converges toward zero," and the data shows the *gap*, not just the ranking, only modestly narrowed.
3. **The rank inversion itself** (no runtime holds the top spot in both workloads) is arguably the most article-relevant finding from this single run — a concrete, measured illustration that "runtime X is fastest" is not a single fact but a workload-dependent one, consistent with Stage 11's Candidate C framing, even though the *magnitude* of gap-shrinkage predicted by Candidate C did not clearly show up here.

## Confounders / limitations (read before using this result for anything)

- **2-vCPU shared sandbox.** The single largest limitation. Server, database, and load generator all competed for the same two cores. This could compress *or* distort differences between runtimes in ways that don't reflect their behavior on adequately-provisioned hardware — and could affect Workload A and Workload B differently (e.g., if one runtime's event loop handles the "load generator also wants this core" contention better than another's, that's a sandbox artifact, not a mechanism this project has any source-level story for).
- **Uniform `pg` driver across all three runtimes, not each runtime's native/idiomatic client.** In particular, Bun's Workload B result reflects the generic `pg` package, not Bun's own `Bun.sql`. If Bun's native SQL client is meaningfully faster than `pg` (untested here), Workload B's Bun numbers could understate Bun's best-case I/O-bound performance — a possible reason the gap didn't shrink as much as external sources found, if those sources' "Bun" implementations used more Bun-idiomatic tooling.
- **A very light query.** `WHERE id = $1` on a primary key is close to the cheapest realistic database operation possible. Stage 10's external sources' workloads (a URL shortener with validation; unspecified but apparently heavier Postgres-backed logic) likely spent more wall-clock time in "realistic application logic" than this experiment's Workload B does — which directly bears on whether "I/O dominates the request" was achieved to the same degree here as in those sources. Workload B's own p50 latency (1.5–2ms) versus Workload A's (0ms, i.e., sub-millisecond) suggests the DB step added on the order of 1–2ms — real, but not necessarily "dominating" in the way a heavier real-world handler would.
- **Protocol deviations** (concurrency 20 vs. 50; 10s/5s timed/warmup vs. 60s/30s) — documented above, driven by the sandbox's hardware and this session's time constraints, not by convenience or a preference for a particular outcome.
- **No independent CPU-saturation instrumentation** for the load generator or server processes during runs (validation items 6 and 8 above) — a real gap in this run's rigor, worth closing on a re-run.
- **Single environment, single point in time.** No cross-machine or cross-day replication was attempted for this pilot; all 60 runs happened in one ~17.6-minute session on one machine.

## What this establishes

A real, honestly-measured, low-noise, zero-error controlled comparison exists showing that — under 2-vCPU-constrained conditions, with a uniform database driver and a light indexed-lookup query — the Bun/Node/Deno throughput gap narrowed only modestly (not substantially) when I/O was added, while the *ranking* of which runtime was fastest inverted entirely between the two workloads. This is genuine counter-evidence to the "substantial shrinkage" framing of H6 as tested here, and genuine supporting evidence for the weaker, still-real claim that runtime performance ranking is workload-dependent.

## What this does NOT establish

That Stage 10's external Tier-2 findings are wrong, that H6 is refuted in general, that any specific mechanism (M9/M15–M18/M22) is responsible for anything observed, or that these results would hold on non-shared, multi-core, production-representative hardware with a heavier I/O workload and each runtime's native database client. The honest state after this run is: **the specific, narrow version of H6 tested here was not supported; the broader hypothesis remains open, and this result raises real, specific, actionable questions about hardware scale and I/O-step weight that a re-run should address before this is treated as more than a pilot.**

## Evidence Map impact (targeted, not a full rewrite)

Recommend recording, once reviewed and approved:
- New benchmark evidence attached to M9/M15–M18/M22's cluster entries and to the Stage 11 "What survives"/Candidate C discussion: a first-party controlled experiment (this one) did not reproduce Stage 10's external gap-shrinkage finding at the magnitude those sources reported — logged as **genuine counter-evidence, not a rejection**, given the substantial, disclosed environmental/design limitations above.
- The rank-inversion finding (no runtime fastest in both workloads) as new, moderate-confidence support for the workload-dependency framing generally (Candidate C), independent of whether the *magnitude* of gap-shrinkage matches Stage 10's external sources.
- Explicitly do **not** promote any of M9/M15–M18/M22 from "unmeasured magnitude" to "proven" or "disproven" — this was a cluster-level experiment, and even its cluster-level verdict (NOT SUPPORTED, this run) carries the environment caveats above.
- Flag a new, concrete follow-up need: re-run H6 on non-shared hardware with (a) a heavier, more realistic query/application-logic step and (b) each runtime's native database client as a second condition alongside the uniform-driver condition already run here — both would help distinguish "the sandbox suppressed the effect" from "the effect is smaller than Stage 10's external sources suggested" from "driver choice mattered."

**I have not edited `evidence/evidence-map.md` yet** — per your instruction, Evidence Map changes happen only after your review of this report.

## Recommendation for next experiment (H4)

Proceed to H4 as planned once this report is reviewed. Two carry-forward notes for H4's execution, given what this run revealed about the environment:
1. **The same 2-vCPU constraint applies to H4.** H4 is a within-Bun comparison (sync vs. async handler), which somewhat reduces cross-runtime-driver confounds but not the hardware constraint — the same honest environment disclosure will be needed.
2. **The load-generator/server CPU-contention gap (validation items 6/8 above) should be closed for H4** if feasible (e.g., a lightweight `ps`/`pidstat` sample during the timed window) — H4's expected effect size (a small header+URL copy) is likely smaller than H6's I/O-step effect, making CPU-contention noise proportionally more dangerous to a clean result.

---

*Every number in this document is a real, measured result from the 60 runs described above. No number was invented, adjusted, or selected after the fact. Raw data: `../raw/`. Full statistical summary: `summary.json`. Benchmark source: `../benchmark/`.*
