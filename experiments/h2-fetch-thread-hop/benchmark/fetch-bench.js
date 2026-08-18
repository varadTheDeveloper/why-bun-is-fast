// H2 shared fetch() client benchmark — the SAME script for Bun, Node, and
// Deno (no runtime-specific branches except the final version-report
// field), so the only variable between runs is which runtime's fetch()
// implementation executes it.
//
// Usage: <runtime> fetch-bench.js <baseUrl> <mode> <timedCount>
//   mode = "cold" | "keepalive"
//
// IMPORTANT METHODOLOGY NOTES (found during smoke-testing, before the full
// run — documented here rather than silently fixed and hidden):
//
// 1. Warmup and timed measurement MUST happen within the SAME process
//    invocation for "keepalive" mode, or the "timed" phase starts as a
//    brand-new process with no JIT warmup and no established connection —
//    silently reintroducing the exact cold-vs-warm mismatch Section 11
//    warns against. An earlier version ran warmup and timed as two
//    separate process invocations; this produced a large (tens-of-ms),
//    slowly-decaying first-requests effect that was a warmup artifact, not
//    a real steady-state signal. Fixed: both phases now run inside one
//    process/one script invocation.
//
// 2. A fixed, small warmup count (e.g. 50-1000 requests) is NOT
//    sufficient for every runtime. Smoke-testing found Node's fetch
//    (undici) needs ~2000-5000 warmup requests before its per-request
//    keepalive latency stabilizes (dropping from ~700us-3ms down to
//    ~180-220us, matching Bun/Deno's range) — Bun and Deno stabilize much
//    sooner. Using an "equal, small" warmup count across all three
//    runtimes would have silently made Node look far slower than its true
//    steady-state behavior, for a warmup-insufficiency reason completely
//    unrelated to M9. Fixed: warmup now runs in chunks with a predefined
//    stability check (declared here, in code, BEFORE any run — same
//    mechanism/thresholds as H5's chunked-warmup stability check), and is
//    extended (bounded) until stable or the extension budget is
//    exhausted. The final warmup outcome (stable/unstable, iterations
//    used) is reported in the output for every run, per runtime, not
//    hidden.

const baseUrl = process.argv[2];
const mode = process.argv[3]; // "cold" | "keepalive"
const timedCount = parseInt(process.argv[4], 10);

if (!baseUrl || !mode || !timedCount) {
  console.error("usage: fetch-bench.js <baseUrl> <cold|keepalive> <timedCount>");
  process.exit(1);
}

const EXPECTED_BODY = '{"ok":true}';
const EXPECTED_LEN = "11";
const path = mode === "cold" ? "/cold" : "/";
const url = baseUrl + path;

const WARMUP_START = 1000;
const WARMUP_CHUNKS = 10;
const WARMUP_MAX_EXTENSIONS = 7; // 1000 -> 2000 -> 4000 -> 8000 -> 16000 -> 32000 -> 64000 -> 128000
const WARMUP_EXTENSION_MULTIPLIER = 2;
// Smoke-testing found a single attempt's stability check can be fooled by
// an intermediate JIT-tiering plateau (observed for Node's fetch: looks
// flat around 1000 iterations at ~500-800us, but keeps dropping further to
// ~180-220us by ~5000 iterations). Fixed (before the official run, not
// after seeing official results): require the stability check to pass on
// TWO CONSECUTIVE attempts, with the second attempt's mean within 20% of
// the first's, before declaring STABLE — a single locally-flat window is
// not enough on its own.
const REQUIRE_CONSECUTIVE_STABLE_PASSES = 2;
const CONSECUTIVE_PASS_MEAN_TOLERANCE = 0.20;

// 3. The escalating stability-seeking warmup above is designed for
//    KEEPALIVE mode, where a persistent-connection, low-inherent-noise
//    measurement can reasonably be expected to converge to a tight
//    steady state. COLD mode's per-request latency inherently includes
//    TCP connection-establishment overhead/noise on EVERY request (by
//    design — that's what "cold" means here), which does not converge
//    the same way. Applying the same escalating-extension algorithm to
//    cold mode during smoke-testing caused runaway warmup (one Bun run
//    reached the full extension budget, opening 3000+ connections just
//    for warmup, before the smoke test was even stopped) because cold
//    connection-setup noise can keep tripping the CV threshold
//    indefinitely — this is not a bug in the check, it's a mismatch
//    between the check's assumptions and cold mode's inherent noise
//    profile. Fixed: cold mode uses a smaller, FIXED warmup count
//    instead (JIT/process warmup only — there is no persistent
//    connection to warm in cold mode by definition), decided BEFORE the
//    official run based on this smoke-test finding, not tuned afterward.
const COLD_FIXED_WARMUP_COUNT = 300;

function mean(a) { return a.reduce((x, y) => x + y, 0) / a.length; }
function stdev(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1));
}

function stabilityCheck(chunkTimingsNs, numChunks) {
  if (numChunks < 4) return { stable: null, reason: "too few chunks" };
  const half = Math.floor(numChunks / 2);
  const firstHalf = chunkTimingsNs.slice(0, half);
  const lastHalf = chunkTimingsNs.slice(-half);
  const firstMean = mean(firstHalf);
  const lastMean = mean(lastHalf);
  const relChange = firstMean ? (lastMean - firstMean) / firstMean : null;
  const lastCV = lastMean ? stdev(lastHalf) / lastMean : 0;
  const stable = relChange !== null && Math.abs(relChange) <= 0.15 && lastCV <= 0.25;
  return { stable, firstHalfMeanNs: firstMean, lastHalfMeanNs: lastMean, relativeChange: relChange, lastHalfCV: lastCV };
}

async function doRequest() {
  const res = await fetch(url);
  const text = await res.text();
  return { status: res.status, text, contentLength: res.headers.get("content-length") };
}

async function runWarmupChunk(n) {
  const t0 = process.hrtime.bigint();
  for (let i = 0; i < n; i++) await doRequest();
  const t1 = process.hrtime.bigint();
  return Number(t1 - t0);
}

async function warmupWithStability() {
  let iters = WARMUP_START;
  const attempts = [];
  let consecutivePasses = 0;
  let firstPassMean = null;
  for (let attempt = 0; attempt <= WARMUP_MAX_EXTENSIONS; attempt++) {
    const perChunk = Math.floor(iters / WARMUP_CHUNKS);
    const chunkTimingsNs = [];
    for (let c = 0; c < WARMUP_CHUNKS; c++) chunkTimingsNs.push(await runWarmupChunk(perChunk));
    const check = stabilityCheck(chunkTimingsNs, WARMUP_CHUNKS);
    // NOTE: perChunk doubles every attempt (since iters doubles), so raw
    // chunk wall-time is NOT comparable across attempts — normalize to
    // per-request time before comparing attempt-to-attempt.
    const attemptMeanPerRequestNs = mean(chunkTimingsNs) / perChunk;
    attempts.push({ attempt, iterations: iters, chunkTimingsNs, stabilityCheck: check, attemptMeanPerRequestNs });

    if (check.stable) {
      if (consecutivePasses === 0) {
        firstPassMean = attemptMeanPerRequestNs;
        consecutivePasses = 1;
      } else {
        const driftFromFirstPass = firstPassMean ? Math.abs(attemptMeanPerRequestNs - firstPassMean) / firstPassMean : 1;
        if (driftFromFirstPass <= CONSECUTIVE_PASS_MEAN_TOLERANCE) {
          consecutivePasses++;
        } else {
          // Passed locally but drifted from the previous pass beyond tolerance —
          // treat as a new first pass, not a confirmation.
          firstPassMean = attemptMeanPerRequestNs;
          consecutivePasses = 1;
        }
      }
      if (consecutivePasses >= REQUIRE_CONSECUTIVE_STABLE_PASSES) {
        return { finalStatus: "STABLE", attempts, finalIterations: iters };
      }
    } else {
      consecutivePasses = 0;
      firstPassMean = null;
    }
    iters *= WARMUP_EXTENSION_MULTIPLIER;
  }
  return { finalStatus: "UNSTABLE_AFTER_MAX_EXTENSIONS", attempts, finalIterations: Math.floor(iters / WARMUP_EXTENSION_MULTIPLIER) };
}

async function fixedWarmup(n) {
  const chunkNs = await runWarmupChunk(n);
  return {
    finalStatus: "FIXED_NO_STABILITY_CHECK", finalIterations: n,
    attempts: [{ attempt: 0, iterations: n, chunkTimingsNs: [chunkNs], attemptMeanPerRequestNs: chunkNs / n }],
  };
}

async function main() {
  const warmup = mode === "cold" ? await fixedWarmup(COLD_FIXED_WARMUP_COUNT) : await warmupWithStability();

  // --- timed ---
  const latenciesNs = [];
  let allStatus200 = true;
  let allBodyMatch = true;
  let allContentLengthMatch = true;
  let errors = 0;

  const start = process.hrtime.bigint();
  for (let i = 0; i < timedCount; i++) {
    const t0 = process.hrtime.bigint();
    let r;
    try {
      r = await doRequest();
    } catch (e) {
      errors++;
      continue;
    }
    const t1 = process.hrtime.bigint();

    if (r.status !== 200) allStatus200 = false;
    if (r.text !== EXPECTED_BODY) allBodyMatch = false;
    if (r.contentLength !== EXPECTED_LEN) allContentLengthMatch = false;

    latenciesNs.push(Number(t1 - t0));
  }
  const end = process.hrtime.bigint();
  const totalNs = Number(end - start);

  const out = {
    mode, timedCount, errors,
    warmupFinalStatus: warmup.finalStatus,
    warmupFinalIterations: warmup.finalIterations,
    warmupAttempts: warmup.attempts.map(a => ({ attempt: a.attempt, iterations: a.iterations, stabilityCheck: a.stabilityCheck, attemptMeanPerRequestNs: a.attemptMeanPerRequestNs })),
    latenciesNs,
    totalNs,
    throughputPerSec: timedCount / (totalNs / 1e9),
    allStatus200, allBodyMatch, allContentLengthMatch,
    runtimeVersion: (typeof Bun !== "undefined") ? `bun ${Bun.version}` :
      (typeof Deno !== "undefined") ? `deno ${Deno.version.deno}` : `node ${process.version}`,
  };
  console.log(JSON.stringify(out));
}

main();
