# Reproducibility

What's provided in this repository, and how to check any number in the article against it yourself.

## What each experiment folder contains

```
experiments/hN-.../
├── README.md          — what was tested, hypothesis, mechanism, methodology, results, limitations
├── benchmark/          — the actual scripts/programs used to run the experiment
├── raw/                 — individual per-run measurement files (one file per run)
└── results/
    ├── README.md        — a written summary of the results
    ├── metadata.json     — environment, versions, run counts, protocol details
    └── summary.json      — the computed statistics (median, mean, stddev, CV, etc.) used in the article
```

Not every experiment has all of these (H6, for instance, includes `benchmark/servers/` for the actual server implementations under test and a `schema.sql` for the database setup used in its DB-backed workload).

## Verifying a number in the article

Every benchmark figure in the article is followed by an inline citation identifying which experiment it came from, linked to that experiment's folder in this repository — for example, `(H5 — buffer allocation experiment, 10 runs × 1,000,000 allocations per size/runtime combo.)`. To verify a number:

1. Open the linked experiment folder's `results/summary.json`.
2. Find the relevant field (each experiment's README documents its own summary schema).
3. Compare against the article's stated value — article numbers are standard rounds of the exact source values, with the rounding method disclosed inline wherever it could otherwise mislead (e.g., "just over 22%" rather than silently truncating 22.4%).

## Re-running an experiment

Each experiment's `README.md` documents the exact commands used to run it, along with the runtime versions and hardware it was run on (see `methodology/environment.md`). Re-running on different hardware, or with updated runtime versions, will very likely produce different absolute numbers — this is expected, and is itself informative, since it's a direct test of how sensitive these results are to environment. If you do reproduce this research, the project would be genuinely interested to hear whether the relative comparisons (which runtime wins under which condition) hold up on different hardware.

**A known gap:** experiments were run on release builds, not the project's originally intended source-controlled builds pinned to an exact commit (see `methodology/environment.md` for why). Some binary artifacts used in benchmarks (a compiled shared library and N-API addon for H1) are not included in this repository — their C source (`native.c`, `napi_addon.c`) is included under `experiments/h1-native-call-boundary/benchmark/`, and the exact compiler and flags used to build them are documented in that experiment's `results/metadata.json` (`gcc 13.3.0, -O2 -fPIC -shared`, identical flags for both). Rebuilding them locally with the documented compiler/flags should reproduce equivalent binaries.

## Raw data

Individual per-run measurement files are preserved under each experiment's `raw/` directory wherever they existed and were safe to publish (see the root `README.md`'s note on sanitization) — the summary statistics in `results/summary.json` are computed from these files, not asserted independently. This means the full computation is auditable end to end: raw measurement → summary statistic → article claim.
