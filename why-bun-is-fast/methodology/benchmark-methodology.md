# Benchmark Methodology

How the six experiments (H1–H6) behind ["Why Is Bun So Fast?"](../article/why-bun-is-fast.md) were designed, run, and analyzed.

## Why controlled experiments, not a single blanket benchmark

Public discussion of Bun's speed tends to cite one of two things: a single end-to-end throughput number ("Bun is 4x faster") or a plausible-sounding source-level explanation ("JavaScriptCore is faster than V8"). Neither actually tells you which mechanism is responsible for what you observe. This project instead reads Bun's, Node's, and Deno's source code first to find *specific, testable mechanisms* that source code implies should matter, then designs one narrow controlled experiment per mechanism. Six mechanisms were promoted to full experiments (H1–H6); a seventh (H7, Bun's startup cache vs. Node's opt-in equivalent) was deliberately deferred — see `experiments/h7-startup-cache-config/README.md`.

## Falsification-first design

Each experiment's README states an explicit falsifier — the specific result that would count against the hypothesis — decided *before* the experiment was run. Falsifiers were never adjusted after seeing results. Where a result contradicted the hypothesis (Node beating Bun on small Buffer allocations, Deno beating Bun's own `bun:ffi`, Bun's N-API compatibility layer losing badly to Node's native N-API, H4 reversing at higher concurrency), that result is reported and kept in the article and in each experiment's own README, not discarded.

## Statistical method

Each experiment ran multiple independent trials per condition (10–40 runs depending on the experiment; exact counts are in each experiment's `results/metadata.json`) and reports the **median** as the primary statistic, with mean, standard deviation, and coefficient of variation (CV) also recorded in each `results/summary.json` for readers who want to assess noise directly. Medians were used over means specifically because they're less sensitive to the occasional outlier run caused by shared-hardware scheduling noise (see "Environment," below).

## Warmup handling

For latency-sensitive experiments (H2, H4), a fixed number of warmup requests were sent and discarded before timed measurement began, to avoid including JIT warmup and connection-setup costs in the timed samples. Node in particular required more warmup requests than Bun or Deno before its latency numbers stabilized — this is documented explicitly in H2's README as a methodology finding in its own right, not smoothed over.

## Cold-cache methodology (H3, and available to H7 if it is ever run)

H3 (cold-start page faults) explicitly measures the *cold* condition: the Linux page cache was dropped before every cold-start run, and warm runs (immediately following, cache intact) were captured separately as a sanity check that the cache-drop procedure actually worked (warm fault counts should be, and were, materially lower than cold). See `experiments/h3-coldstart-page-faults/README.md` for the exact drop-cache procedure.

## Same-binary / same-driver controls

Two experiments used a deliberate control-variable design rather than a straightforward "run each runtime's own preferred path" comparison:

- **H1** compiled one native addon once and loaded the identical compiled machine code into both Node (via its native N-API implementation) and Bun (via Bun's N-API compatibility layer), so the comparison isolates binding-path implementation from the code being called — while explicitly *not* holding the underlying JS engine fixed, since Bun runs JavaScriptCore and Node runs V8 regardless of binding path. See the article's §4 and `experiments/h1-native-call-boundary/README.md` for the full reasoning.
- **H6** used the same generic, non-specialized PostgreSQL driver package across all three runtimes, rather than each runtime's own fastest specialized client, specifically to keep the database-client implementation as constant as possible — though this does not make the comparison perfectly driver-neutral, since driver code still interacts differently with each runtime's event loop and native-binding layer.

## Falsification and disclosure rules

- No benchmark number in the article or in any experiment's `results/` was altered after being generated.
- Where a planned protocol deviated from what was actually run (H6's concurrency and run-duration, in particular — see `environment.md`), the deviation is disclosed in that experiment's `results/metadata.json` and README, not silently absorbed into the reported numbers.
- Every number quoted in the article was checked directly against its source experiment's `results/summary.json` before publication — not trusted from any intermediate planning or drafting document.
