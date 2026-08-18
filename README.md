# Why Is Bun So Fast?

A source-level and experimental investigation into the performance
differences between Bun, Node.js, and Deno.

This repository contains the research behind the article:

**Why Is Bun So Fast?**

## What we did

We traced Bun, Node.js, and Deno through their source code and then
tested six specific performance hypotheses:

- H1 — native JS → native call boundary
- H2 — fetch() thread-hop overhead
- H3 — cold-start page faults
- H4 — Bun.serve() sync vs async path
- H5 — Buffer allocation/pooling
- H6 — realistic I/O HTTP workload

The experiments were run on a shared 2-vCPU cloud environment using
release builds. They should therefore be treated as controlled
experimental evidence, not production benchmark results.

## Reproducibility

Each experiment contains:

- benchmark source
- exact methodology
- runtime versions
- raw measurements
- metadata
- analysis
- final results

The raw data is preserved so the reported numbers can be independently
checked.

## Main conclusion

The investigation did not find a single explanation for Bun's
performance.

Instead, performance was strongly workload- and implementation-path
dependent.

The winner changed depending on factors such as:

- native binding path
- allocation size
- concurrency
- HTTP path
- startup behavior
- workload composition

## Experiments

| Experiment | Question |
|---|---|
| H1 | Does the native binding path have measurable per-call overhead differences? |
| H2 | Does Bun's fetch thread-hop create measurable latency overhead? |
| H3 | Does Bun's startup binary layout reduce page faults? |
| H4 | Does Bun's sync HTTP path outperform a genuinely suspending async path? |
| H5 | Does Node's Buffer pooling create an allocation advantage? |
| H6 | Does the HTTP performance gap shrink with database I/O? |
