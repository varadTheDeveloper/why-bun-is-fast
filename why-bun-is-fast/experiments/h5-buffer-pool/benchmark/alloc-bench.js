// H5 shared allocation benchmark — deliberately the SAME script for both
// Bun and Node (no runtime-specific branches), so the only variable between
// runs is which runtime executes it. Tests M20: Buffer.allocUnsafe(size)
// allocation throughput, Node's pooled path vs Bun's fresh-allocation path.
//
// Usage: <runtime> alloc-bench.js <size> <iterations> <ringSize> <phase>
//   phase = "warmup" | "timed"
// Warmup runs in NUM_WARMUP_CHUNKS chunks and reports per-chunk timing so
// the orchestrator can verify a stable steady state was reached (Stage 12
// Section 6 JIT-control requirement) rather than assuming 50,000 iterations
// is automatically sufficient.

const size = parseInt(process.argv[2], 10);
const iterations = parseInt(process.argv[3], 10);
const ringSize = parseInt(process.argv[4], 10);
const phase = process.argv[5]; // "warmup" or "timed"
const numChunks = phase === "warmup" ? 10 : 1;

if (!size || !iterations || !ringSize || !phase) {
  console.error("usage: alloc-bench.js <size> <iterations> <ringSize> <warmup|timed>");
  process.exit(1);
}

const ring = new Array(ringSize).fill(null);
let ringIdx = 0;
let acc = 0; // accumulator forces the write to be observable — prevents dead-code elimination

const perChunk = Math.floor(iterations / numChunks);
const chunkTimingsNs = [];

function runChunk(n) {
  const start = process.hrtime.bigint();
  for (let i = 0; i < n; i++) {
    const buf = Buffer.allocUnsafe(size);
    // Observable, non-constant write: value depends on the loop counter and
    // the running accumulator, so neither JSC nor V8 can constant-fold it.
    buf[0] = (i ^ acc) & 0xff;
    if (size > 1) {
      buf[size - 1] = (i + acc) & 0xff; // touch last byte too, for larger sizes
    }
    acc = (acc + buf[0] + buf[size - 1 >= 0 ? (size > 1 ? size - 1 : 0) : 0]) | 0;
    ring[ringIdx] = buf; // retain briefly in bounded ring buffer
    ringIdx = (ringIdx + 1) % ringSize; // oldest entry falls out of scope here
  }
  const end = process.hrtime.bigint();
  return Number(end - start);
}

for (let c = 0; c < numChunks; c++) {
  chunkTimingsNs.push(runChunk(perChunk));
}

const totalNs = chunkTimingsNs.reduce((a, b) => a + b, 0);
const totalIterationsRun = perChunk * numChunks;

console.log(JSON.stringify({
  size,
  iterations: totalIterationsRun,
  ringSize,
  phase,
  numChunks,
  perChunk,
  chunkTimingsNs,
  totalNs,
  allocsPerSec: totalIterationsRun / (totalNs / 1e9),
  acc, // included so the accumulator's use is visible in output too (belt & suspenders vs. elimination)
  runtimeVersion: (typeof Bun !== "undefined") ? `bun ${Bun.version}` : `node ${process.version}`,
}));
