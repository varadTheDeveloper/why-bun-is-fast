# Why Is Bun So Fast?

A source-level and experimental investigation into Bun's performance compared with Node.js and Deno.

This repository contains the complete public research behind the article **[Why Is Bun So Fast?](article/why-bun-is-fast.md)**: the source-code reading that generated each hypothesis, the six controlled experiments that tested them, the raw measurements, the analysis, and the final results.

![Why Is Bun So Fast?](figures/article-thumbnail.png)

## The short answer

**There is no single fastest JavaScript runtime.**

Our experiments found that performance depends heavily on the workload and the implementation path. Bun wins some paths, Node wins others, and Deno wins others.

The interesting question isn't simply "which runtime is fastest?" It's **where the performance differences actually come from, when they matter, and where they disappear.**

### Who wins where?

| Test / workload | Winner | What it shows |
|---|---|---|
| Native call via Bun `bun:ffi` | Bun | Bun's chosen binding path is fast |
| Native call via Deno Fast API | Deno | Another optimized native-call path can beat Bun |
| Same compiled N-API addon | Node | Bun's N-API compatibility layer has substantial overhead |
| Buffer allocation below 4,096 B | Node | Node's pooling helps in the small-buffer regime |
| Buffer allocation at/above 4,096 B | Bun | The winner flips at the measured threshold |
| Plaintext HTTP | Deno | Raw HTTP has no universal winner |
| DB-backed HTTP | Bun, with a smaller spread | Runtime differences shrink when external I/O is added |

These are results from specific controlled workloads, not general claims about which runtime is fastest overall. (H3 and H4 are deliberately not in this table — H3's result doesn't reduce to a single winner without distorting it, and H4 compares two code paths within Bun, not across runtimes. Both are covered in full below.)

> **Benchmark environment:** H1–H6 were run on the same shared 2-vCPU VM using release builds. This keeps the comparisons internally consistent, but the absolute numbers should not be treated as universal hardware-independent benchmarks. Different hardware, especially dedicated/bare-metal systems, can produce different absolute results.

## Research question

Why does Bun perform differently from Node.js and Deno — and is there one underlying reason, or several?

## What we investigated

We traced Bun, Node.js, and Deno through their source code and then tested six specific, source-motivated performance hypotheses:

- **[H1](experiments/h1-native-call-boundary/)** — native call boundary: does the binding path (not just the JS engine) affect the cost of calling native code from JavaScript?
- **[H2](experiments/h2-fetch-thread-hop/)** — `fetch()` thread-hop overhead: does Bun's dedicated background thread for `fetch()` cost more than it's worth?
- **[H3](experiments/h3-coldstart-page-faults/)** — cold-start page faults: does Bun's documented startup binary layout reduce page faults, and does that make it start faster?
- **[H4](experiments/h4-http-sync-async/)** — `Bun.serve()` sync vs. async path: does the header/URL copy on request suspension show up as a measurable cost?
- **[H5](experiments/h5-buffer-pool/)** — Buffer allocation/pooling: does Node's buffer-pooling strategy (which Bun doesn't replicate) create a measurable allocation advantage?
- **[H6](experiments/h6-realistic-io-convergence/)** — realistic, database-backed HTTP workload: does adding real I/O change which runtime wins, and does the gap between runtimes shrink?

A seventh hypothesis, H7 (Bun's startup cache vs. Node's opt-in equivalent), was deliberately deferred — see [`experiments/h7-startup-cache-config/`](experiments/h7-startup-cache-config/).

## Main conclusion

The strongest conclusion from the experiments is not that Bun is universally faster. It is that **runtime performance is workload- and path-dependent**.

The winner can change when you change the binding path, allocation size, concurrency, HTTP workload, or the amount of external I/O. Each of these is a real, source-verified mechanism — but every one of them wins under some conditions and loses under others, which is exactly what the six experiments below (H1–H6) each test individually. Factors we found the winner to depend on:

- which native binding path a call takes
- allocation size
- request concurrency
- connection state (cold vs. keep-alive)
- startup binary layout vs. actual measured startup time
- workload composition (plaintext vs. database-backed)

For a quick, at-a-glance summary of every result, see the ["Who wins where?"](#who-wins-where) table above, or the article's own ["Who wins where?"](article/why-bun-is-fast.md#who-wins-where) table and ["The short answer"](article/why-bun-is-fast.md#the-short-answer) section. For the full synthesis — including every case where Bun did *not* win — see [§10, "So what actually makes Bun fast?"](article/why-bun-is-fast.md#10-so-what-actually-makes-bun-fast) and [§11, "Where Bun doesn't win"](article/why-bun-is-fast.md#11-where-bun-doesnt-win).

## Where Bun doesn't win

The research went looking for cases where Bun loses, specifically because a runtime that never loses in its own testing usually means the testing wasn't thorough enough. It found several, real and reproducible:

- Node wins every buffer-allocation size tested below 4,096 bytes — Node's pool helps at small sizes.
- Deno wins the plaintext HTTP workload outright.
- Deno's V8 Fast API beats Bun's own preferred native-call path (`bun:ffi`).
- Run the exact same compiled N-API addon through Bun's Node-compatibility layer, and it loses badly to Node's native N-API implementation — nearly 4× slower.
- Deno has fewer of the expensive "major" page faults than Bun at cold start ([H3](experiments/h3-coldstart-page-faults/)) — though this one doesn't reduce to a single winner: Bun has fewer *total* page faults, Deno has fewer *major* faults, and Bun's and Deno's cold-start times were statistically indistinguishable.
- Bun's one clearly-measured server-side optimization — the synchronous-handler advantage in [H4](experiments/h4-http-sync-async/) — reverses at higher concurrency.

None of this is included reluctantly. See the article's [§11, "Where Bun doesn't win"](article/why-bun-is-fast.md#11-where-bun-doesnt-win) for the full detail.

## The database-backed workload (H6)

[H6](experiments/h6-realistic-io-convergence/) is the one experiment built to look like a real backend rather than a microbenchmark, so it's worth walking through explicitly:

- **Workload A** was plain "hello world" HTTP — no I/O, the kind of benchmark most public runtime comparisons run.
- **Workload B** was the same server, but with a single indexed PostgreSQL lookup added before responding, using the same generic database driver on all three runtimes.
- On Workload A, the ranking was Deno, Bun, Node. Add the database call, and it inverts completely: Bun, Node, Deno.
- The fastest/slowest spread also narrowed — from about 1.224× on plaintext to about 1.130× with the database call added, roughly an **8% narrowing**.
- That's smaller than the convergence some other, independently published benchmarks have reported under heavier realistic workloads — this test used a deliberately light, single indexed lookup, not a full application.

This does not mean all real applications converge to the same performance, or that database I/O generally erases runtime differences — only that, in this specific tested workload, it measurably narrowed the gap. See the article's [§9, "The big real-world test"](article/why-bun-is-fast.md#9-the-big-real-world-test) for the full result and its limitations.

## What this research does not claim

- Bun is not universally faster than Node or Deno.
- No single mechanism explains all of Bun's performance — the six experiments each isolate a different one, and each is conditional.
- An individual microbenchmark win (e.g., `bun:ffi`'s per-call overhead) is not a general-purpose application-speed multiplier.
- H1 does not prove that JavaScriptCore's garbage-collector design (vs. V8's) caused the observed native-call gap — only that binding-path implementation, not engine identity alone, explains its direction.
- H2 does not prove that the `fetch()` thread hop is itself beneficial — only that Bun was faster in the tested conditions despite paying for it.
- H3 does not experimentally isolate Bun's `#[cold]` binary-layout technique as the cause of its page-fault advantage — it measured the resulting profile, not an ablated version of the technique.
- H4 does not isolate the header/URL copy specifically — the measured 10.5% figure also includes the general cost of suspending and resuming a request.
- H6 does not prove that all real applications converge in performance when a database is added — only that this specific tested workload narrowed the gap by about 8%.

## Important limitation

All six controlled experiments were performed on shared 2-vCPU cloud hardware using release builds of all three runtimes, not dedicated benchmarking hardware or the project's originally intended source-controlled builds. These results should therefore be treated as controlled experimental evidence about *why* the measured differences exist, not as universal production benchmarks you should expect to reproduce exactly on your own hardware. Full detail: [`methodology/environment.md`](methodology/environment.md).

## What we did

We traced Bun, Node.js, and Deno through their source code and then tested six specific performance hypotheses (listed above), each designed around a falsifier decided *before* the experiment was run. Where a result contradicted the hypothesis, it's reported and preserved — see each experiment's README for its own counter-evidence.

## Reproducibility

Each experiment contains:

- benchmark source code
- exact methodology
- runtime versions and environment details
- raw measurements (one file per run, where safe to publish)
- computed statistics (`results/summary.json`)
- a written results summary

The raw data is preserved so every reported number can be independently checked. See [`methodology/reproducibility.md`](methodology/reproducibility.md) for exactly how.

## Repository structure

```
article/            the article itself, with links back into experiments/ for every claim
experiments/         all seven hypotheses (H1–H6 executed, H7 deferred) — source, raw data, results
source-notes/        source-code research notes on runtime architecture, JSC vs. V8, native bindings, HTTP, memory
methodology/          shared benchmark methodology, environment details, reproducibility guide
figures/              the six article visuals plus the article thumbnail
```

## A note on how this repository was prepared

Private infrastructure identifiers (internal machine IDs, internal filesystem paths) were sanitized out of the raw metadata before publication. No benchmark number, run count, hardware specification, runtime version, or experimental conclusion was changed in that process — see [`methodology/environment.md`](methodology/environment.md) for exactly what was generalized and why.

## License

[MIT](LICENSE).
