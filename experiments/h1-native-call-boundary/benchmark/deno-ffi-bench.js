// H1 Deno.dlopen() benchmark — Deno's own FFI mechanism, NOT
// apples-to-apples against the Bun/Node N-API comparison (different
// binding technology — see README.md "Equivalence validation"). Two
// variants of the SAME trivial increment operation:
//   fast:     native_increment_i32 — i32 param/result, synchronous.
//             Per documented Deno/V8 Fast API behavior, small integer
//             types (i8/u8/i16/u16/i32/u32/f32/f64/pointer/buffer) on a
//             synchronous (non "nonblocking") symbol are eligible for
//             V8's Fast API call optimization.
//   nonfast:  native_increment_i64 — i64 (BigInt) param/result, still
//             synchronous (same blocking semantics as the fast variant,
//             isolating the TYPE-based eligibility specifically, not
//             conflating it with async/thread-pool dispatch). V8's Fast
//             API does not support 64-bit integers without boxing, so
//             this should not be fast-path-eligible.
// DISCLOSED METHODOLOGY GAP: fast-path eligibility here is based on
// documented Deno/V8 Fast API type support, not a source-level trace of
// Deno's FFI fast-call dispatch logic (Deno's source was not cloned for
// this check, given the session's time budget) — it is corroborated
// empirically via the measured timing gap between the two variants (see
// Results), per Section 12's requirement to verify from source OR
// behavior.
//
// Usage: deno run --allow-ffi deno-ffi-bench.js <control|fast|nonfast> <iterations>

const mode = process.argv[2]; // "control" | "fast" | "nonfast"
const iterations = parseInt(process.argv[3], 10);

if (!mode || !iterations) {
  console.error("usage: deno-ffi-bench.js <control|fast|nonfast> <iterations>");
  Deno.exit(1);
}

let lib = null;
if (mode === "fast" || mode === "nonfast") {
  lib = Deno.dlopen("./libnative.so", {
    native_increment_i32: { parameters: ["i32"], result: "i32" },
    native_increment_i64: { parameters: ["i64"], result: "i64" },
  });
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
  const t0 = process.hrtime.bigint();
  let sinkNum = 0;
  let sinkBig = 0n;
  if (mode === "control") {
    for (let i = 0; i < n; i++) {
      sinkNum = (i & 0x7fffffff) + 1 | 0;
    }
  } else if (mode === "fast") {
    for (let i = 0; i < n; i++) {
      sinkNum = lib.symbols.native_increment_i32(i & 0x7fffffff);
    }
  } else {
    for (let i = 0; i < n; i++) {
      sinkBig = lib.symbols.native_increment_i64(BigInt(i));
    }
  }
  const t1 = process.hrtime.bigint();
  return { ns: Number(t1 - t0), sink: mode === "nonfast" ? sinkBig.toString() : sinkNum };
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
    sink: finalSink,
    runtimeVersion: `deno ${Deno.version.deno}`,
  };
  console.log(JSON.stringify(out));
}

main();
