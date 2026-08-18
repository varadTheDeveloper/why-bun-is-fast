// H1 N-API benchmark — the SAME script AND the SAME compiled napi_addon.node
// binary run under both Node (its actual native-call boundary) and Bun
// (via Bun's Node-API compatibility layer). Only the host runtime differs.
//
// Usage: <runtime> napi-bench.js <control|test> <iterations>
//   control: pure JS loop, sink = (sink_prev_input + 1)|0 — no native call.
//            Isolates loop/timer overhead per Section 11.
//   test:    sink = addon.nativeIncrement(i) each iteration — crosses the
//            N-API boundary once per iteration.
//
// Timing is chunked (10 chunks) so the timer itself is amortized over
// large batches (Section 8) rather than timing each individual call.
// Warmup uses the same chunked-stability-check design as H2/H5, requiring
// TWO CONSECUTIVE passing attempts (H2 found a single-attempt check can be
// fooled by an intermediate JIT-tiering plateau).

const mode = process.argv[2]; // "control" | "test"
const iterations = parseInt(process.argv[3], 10);

if (!mode || !iterations) {
  console.error("usage: napi-bench.js <control|test> <iterations>");
  process.exit(1);
}

let nativeIncrement = null;
if (mode === "test") {
  const addon = require("./napi_addon.node");
  nativeIncrement = addon.nativeIncrement;
}

const NUM_CHUNKS = 10;
const WARMUP_START = 100000;
const WARMUP_MAX_EXTENSIONS = 7;
const WARMUP_EXTENSION_MULTIPLIER = 2;
const REQUIRE_CONSECUTIVE_STABLE_PASSES = 2;
const CONSECUTIVE_PASS_MEAN_TOLERANCE = 0.20;

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

function runChunk(n) {
  let sink = 0;
  const t0 = process.hrtime.bigint();
  if (mode === "control") {
    for (let i = 0; i < n; i++) {
      sink = (i & 0x7fffffff) + 1 | 0; // same logical op as the native fn, done in JS
    }
  } else {
    for (let i = 0; i < n; i++) {
      sink = nativeIncrement(i & 0x7fffffff);
    }
  }
  const t1 = process.hrtime.bigint();
  return { ns: Number(t1 - t0), sink };
}

function warmupWithStability() {
  let iters = WARMUP_START;
  const attempts = [];
  let consecutivePasses = 0;
  let firstPassMean = null;
  for (let attempt = 0; attempt <= WARMUP_MAX_EXTENSIONS; attempt++) {
    const perChunk = Math.floor(iters / NUM_CHUNKS);
    const chunkTimingsNs = [];
    for (let c = 0; c < NUM_CHUNKS; c++) chunkTimingsNs.push(runChunk(perChunk).ns);
    const check = stabilityCheck(chunkTimingsNs, NUM_CHUNKS);
    const attemptMeanPerCallNs = mean(chunkTimingsNs) / perChunk;
    attempts.push({ attempt, iterations: iters, stabilityCheck: check, attemptMeanPerCallNs });

    if (check.stable) {
      if (consecutivePasses === 0) {
        firstPassMean = attemptMeanPerCallNs;
        consecutivePasses = 1;
      } else {
        const drift = firstPassMean ? Math.abs(attemptMeanPerCallNs - firstPassMean) / firstPassMean : 1;
        if (drift <= CONSECUTIVE_PASS_MEAN_TOLERANCE) consecutivePasses++;
        else { firstPassMean = attemptMeanPerCallNs; consecutivePasses = 1; }
      }
      if (consecutivePasses >= REQUIRE_CONSECUTIVE_STABLE_PASSES) {
        return { finalStatus: "STABLE", attempts, finalIterations: iters };
      }
    } else {
      consecutivePasses = 0; firstPassMean = null;
    }
    iters *= WARMUP_EXTENSION_MULTIPLIER;
  }
  return { finalStatus: "UNSTABLE_AFTER_MAX_EXTENSIONS", attempts, finalIterations: Math.floor(iters / WARMUP_EXTENSION_MULTIPLIER) };
}

function main() {
  const warmup = warmupWithStability();

  const perChunk = Math.floor(iterations / NUM_CHUNKS);
  const chunkTimingsNs = [];
  let finalSink = 0;
  for (let c = 0; c < NUM_CHUNKS; c++) {
    const r = runChunk(perChunk);
    chunkTimingsNs.push(r.ns);
    finalSink = r.sink;
  }
  const totalNs = chunkTimingsNs.reduce((a, b) => a + b, 0);
  const totalIterationsRun = perChunk * NUM_CHUNKS;

  const out = {
    mode, iterations: totalIterationsRun, numChunks: NUM_CHUNKS, perChunk,
    chunkTimingsNs, totalNs,
    nsPerCall: totalNs / totalIterationsRun,
    callsPerSec: totalIterationsRun / (totalNs / 1e9),
    warmupFinalStatus: warmup.finalStatus,
    warmupFinalIterations: warmup.finalIterations,
    warmupAttempts: warmup.attempts.map(a => ({ attempt: a.attempt, iterations: a.iterations, stabilityCheck: a.stabilityCheck, attemptMeanPerCallNs: a.attemptMeanPerCallNs })),
    sink: finalSink, // printed so the loop/call can't be eliminated as dead code
    runtimeVersion: (typeof Bun !== "undefined") ? `bun ${Bun.version}` : `node ${process.version}`,
  };
  console.log(JSON.stringify(out));
}

main();
