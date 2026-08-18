# H7 — Startup cache / default-configuration comparison

**Status: protocol sketch only. Not yet executed. Classification: DEFER — not part of the initial Stage 13 execution set. Revisit after H3 and H6 results are available.**

## Purpose

Test whether Bun's default-on transpiler/bytecode cache (M6) produces a measurable cold-vs-warm startup difference that Node's actual shipped default (`NODE_COMPILE_CACHE` off, opt-in) does not match — explicitly as a *default-configuration* comparison, not a claim about either engine's caching mechanism in the abstract.

## Why this is deferred

Per this project's internal experiment-design notes: this hypothesis bundles two mechanisms (M5 — JSC's near-zero-startup interpreter design; M6 — Bun's default-on cache), requires the highest setup cost of the seven (a realistic multi-module project, not a trivial script), and its two cleanest sub-questions are already substantially covered by H3 (page faults, a cleaner isolation of the startup-cost story) and H6 (whether the broader HTTP/runtime gap survives realistic conditions). It remains a real, thesis-relevant question — startup is a headline Bun claim — but is better picked up once H3's and H6's results can inform whether the added setup cost here is still worthwhile.

## Hypothesis (H7, from Stage 11 — unmodified)

Bun's default-on transpiler/bytecode cache (M6) produces a measurable cold-vs-warm startup difference that Node's opt-in `NODE_COMPILE_CACHE`, left at its default (off), does not match.

## Mechanism

M5 (JSC's LLInt, near-zero startup cost by design) and M6 (Bun's default-on bytecode/transpiler cache) together, contrasted with Node's opt-in-only equivalent (`NODE_COMPILE_CACHE`, added v22.1.0).

## Runtime versions / commits (sketch)

Release comparison is the primary track (default-configuration behavior is inherently about what a user actually gets, not a pinned commit). Source-controlled pins available if a mechanism-level cross-check becomes necessary: Bun `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4`, Node `nodejs/node@ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85`.

## Hardware / environment (sketch)

Per Section 4, plus the same page-cache-drop capability H3 requires (cold-start measurement here has the identical cache-state-control requirement as H3, and should reuse H3's validated cache-drop procedure once H3 has run).

## Design (sketch — to be finalized if/when promoted out of DEFER)

A realistic multi-module project (proposed: a small but non-trivial project with 10–20 modules and at least one external dependency import per runtime's package-management convention) — explicitly **not** a single-file script, per Stage 10's own finding that trivial-script benchmarks risk measuring CLI/process-dispatch overhead rather than cache behavior (the exact confusion that undermined Bun's own headline "4x faster startup" claim).

Five measured conditions:
1. Cold start (page cache dropped, first run).
2. Warm start (immediately following run, cache intact).
3. Bun at default configuration.
4. Node at default configuration (`NODE_COMPILE_CACHE` unset — i.e., off).
5. Node with `NODE_COMPILE_CACHE` explicitly enabled — **this condition exists specifically to separate the configuration difference (what ships by default) from the mechanism difference (what the cache does when both are actually turned on)**, per the requirement not to conflate the two.

Deno included only if its cache's default-on status for the shipped binary (open item 12) can be confirmed and controlled equivalently well; otherwise Deno is excluded from this experiment rather than guessed at.

## Metrics (sketch)

- **Primary:** cold startup time, warm startup time, cold→warm delta.
- **Secondary:** page faults (reusing H3's measurement approach), filesystem reads (via `strace -c` or platform equivalent, counting `open`/`read`/`stat` calls during startup), CPU.

## Statistical method (sketch)

Same as H3/general Section 5 methodology: median + mean + stddev + CV, ≥10 independent cold-start runs per condition, page-cache-drop verified before each cold run.

## Expected result (directional only)

If M5/M6 hold at the default-configuration level, Bun should show a smaller cold→warm delta than default-config Node, with condition 5 (Node + explicit `NODE_COMPILE_CACHE`) narrowing or closing that gap — which would confirm the difference is primarily about configuration defaults, not an unmatchable engine-level advantage.

## Falsifier

No meaningful difference between default-config Bun and default-config Node on a realistic cold start.

## Interpretation guard

**This experiment must not be used to support any claim of the form "Bun's engine is faster."** It tests a configuration-plus-caching effect specifically, and any citation of its results in the final article must say so explicitly.

## Confounders / risks (sketch)

- Bundling M5 and M6 into one hypothesis means a positive result cannot, on its own, attribute the effect to either mechanism individually — a limitation to state plainly if/when this experiment runs.
- Requires the same page-cache-drop environment prerequisite as H3 (see that experiment's README) — best run only after H3 has validated the cache-drop procedure on the actual execution machine.
- Deno's inclusion is conditional on resolving open item 12 (default-on snapshot status) — do not include an unverified guess.
