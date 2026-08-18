# Memory / Allocation / Allocator Architecture

Status: formal, reviewable deliverable, following the Stage 5–8 standard: SOURCE → EXACT IMPLEMENTATION → WHAT IT DOES → WHY IT MIGHT MATTER → COUNTER-EVIDENCE → BENCHMARK STATUS → CONFIDENCE.

**Source pins used in this stage:**
- Bun: `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4` (same clone as prior stages)
- Node: `nodejs/node@ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85` (main, via `raw.githubusercontent.com` — `lib/buffer.js`, `src/node_internals.h`, `src/api/environment.cc`)
- Deno: `denoland/deno@main`, fresh full-history-free clone this stage (`git clone --depth 1`) — `cli/lib.rs`, `cli/Cargo.toml`, `ext/node/polyfills/`; no specific commit pin was captured for this clone (unlike Stage 8's sparse clone) — **flagged as a minor sourcing gap**: reproducibility for the Deno citations in this stage rests on "main, fetched 2026-08-16" rather than a frozen SHA. Does not affect the correctness of what was read, only exact reproducibility if `main` moves.
- V8: `v8/v8@main`, single-file fetch of `include/v8-array-buffer.h` for the `ArrayBuffer::Allocator::NewDefaultAllocator()` doc comment.

---

## What we know

### 1. Bun's allocator architecture

**1a. mimalloc is Bun's Rust-side global allocator, unconditionally.**

`src/bun_alloc/lib.rs` (~line 622): `#[global_allocator] static ALLOC: bun_alloc::Mimalloc = bun_alloc::Mimalloc;`, wired at the binary root in `src/bun_bin/lib.rs`. Every `Box`/`Vec`/`Arc`/`String` allocation anywhere in Bun's ~200-crate Rust workspace routes through `mimalloc::mi_malloc_auto_align`/`mi_zalloc_auto_align`/`mi_free`/`mi_realloc*`. This is unconditional except under `cfg(bun_asan)`, where the global allocator becomes `std::alloc::System` specifically so ASAN's interceptor can see every allocation (`src/bun_alloc/lib.rs` ~line 49–56) — a debugging/CI concern, not a production behavior difference.

**1b. On Linux specifically, mimalloc goes further and overrides the process-wide system `malloc`, including catching WebKit's own fallback allocations.**

`scripts/build/deps/mimalloc.ts` (~line 27–37), read directly: `const override = cfg.linux && !cfg.asan;` with the comment: *"Linux: ON — the main win. All malloc/free routes through mimalloc, including WebKit's bmalloc when it falls back to system malloc."* This is explicitly **not** enabled on macOS (*"overriding via zone/interpose breaks NAPI addons and system frameworks (SecureTransport etc.)"*) or Windows (*"Bun links the static CRT and calls mi_* directly"*) — a per-platform decision, not a uniform one, made and documented with explicit reasons for each exception.

**1c. mimalloc is compiled with several non-default, source-documented tuning choices — not used at upstream defaults.**

From the same file: `MI_SKIP_COLLECT_ON_EXIT=1` and `MI_NO_PROCESS_DETACH=1` (skip mimalloc's own heap-walk-on-exit and `mi_process_done()` machinery — Bun's shutdown is `_exit`-based, so the OS reclaims memory and walking every live allocation first is pure waste); `MI_DEFAULT_ALLOW_THP=0` on Linux (opts mimalloc's arenas out of Transparent Huge Pages); an `initial-exec` TLS model on ELF/Mach-O targets specifically to make mimalloc's thread-local heap access compile to a single `mov reg, fs:[OFFSET]` instead of a `__tls_get_addr` call (with a documented musl exception — `local-dynamic` — because musl's fixed-size static TLS block can crash on `dlopen` of native addons under `initial-exec`).

**1d. The THP-disable choice comes with an actual measured number, self-reported in a source comment — not a benchmark-track result, but not nothing either.**

Direct quote, `scripts/build/deps/mimalloc.ts` (~line 60–65): *"Measured on an `always` box (release, x64): bun -e 1: THP off = 30MB peak, THP on = 54MB peak. Bun.serve hello: THP off = 46MB rss, THP on = 68MB rss."* This is a **memory-usage** number (peak RSS), not a speed number, and — like M1's rewrite-percentage figures — is self-reported in a code comment with no disclosed methodology (box spec, THP mode confirmed only as "`always`", no iteration count, no third-party reproduction). It is being surfaced here as evidence of *why* the THP-disable decision was made, not as a validated performance claim.

**1e. Bun's JS object heap is NOT mimalloc — it is still JSC's own `bmalloc`, unchanged from upstream WebKit.**

`scripts/build/deps/webkit.ts` links `bmallocLib(cfg)` (a separate `libbmalloc.a`) alongside JSC's core libraries in every build configuration checked (~lines 126, 393–399, 423). This is the single most important disambiguation in this stage: **"Bun uses mimalloc" is true for Bun's own Rust-side native code, true for Linux's process-wide malloc override, and true for specific WTF utility containers opted in via `USE_BUN_MIMALLOC=1`** (see 1f) — **but it is not true for the JS object heap itself.** JSC's `MarkedSpace`/`IsoHeap`/GC-managed cell allocation continues to go through `bmalloc`, WebKit's own purpose-built, GC-tuned allocator, exactly as it does in any other JSC embedder (Safari, etc.). Bun did not replace this.

**1f. `USE_BUN_MIMALLOC=1` is a hardcoded, always-on build flag that redirects specific WTF containers — not JSC's GC heap — to mimalloc.**

`scripts/build/flags.ts` (~line 753–756): `{ flag: "USE_BUN_MIMALLOC=1", desc: "Use mimalloc as default allocator" }`, annotated in source as *"Hardcoded ON — experimental flag not exposed in config."* This flag gates `src/jsc/bindings/MimallocWTFMalloc.h`'s `Bun::MimallocMalloc` struct — a `WTF`-allocator-shaped type (`malloc`/`tryMalloc`/`zeroedMalloc`/`alignedMalloc`/`realloc`/`free`, all `#if USE(BUN_MIMALLOC)` → `mi_*`, else → `std::malloc`/`std::calloc`/`std::free`) — used as an explicit allocator-trait parameter for specific `WTF::Vector` instantiations, confirmed at one call site: `src/jsc/bindings/BunIDLTypes.h`'s `Detail::IDLMimallocSequence`, which backs `IDLArray<T>` — the C++ representation used when a WebIDL binding converts a JS array/sequence argument into a native `WTF::Vector`. This is a targeted, opt-in redirection of a specific, identifiable conversion path, not a blanket WTF-wide override.

### 2. Bun's own allocation layers on top of mimalloc/bmalloc

**2a. `MimallocArena` — a real, general-purpose, per-allocation-freeable arena, used for AST parsing.**

`src/bun_alloc/MimallocArena.rs`: wraps a dedicated `mi_heap_t` (`mi_heap_new()`), exposes `core::alloc::Allocator` so `Vec<T, &MimallocArena>`/`ArenaVec` can grow/shrink/free individual allocations within the arena (`mi_heap_realloc_aligned`/`mi_free`) — explicitly contrasted in source comments with `bumpalo::Bump`, which cannot free or resize individual allocations and would leak on every `Vec` grow. `Drop`/`reset()` bulk-free the whole heap via `mi_heap_destroy`. A documented false-start is included: a previous iteration tried layering a bump-chunk allocator with a cached `mi_theap_t*` and reverted it after a UAF bug (issue #53599) from mimalloc recycling a destroyed heap-thread slot — evidence this arena's design was iterated on for real correctness reasons, not written once and left alone.

`src/bun_alloc/ast_alloc.rs` uses this machinery for `AstAlloc`: a thread-local arena installed for the duration of parsing/transpiling one module, into which AST node `Vec`s allocate; `deallocate` is a **deliberate no-op** (freed only in bulk via arena reset), and the doc comment explains this is "Strategy B for the require-cache ESM leak" — every parsed module's AST-interior `Vec` backing storage is bulk-freed in one `arena.reset()` when the scope exits, rather than requiring individual `Drop`/`free` calls per AST node. **This directly extends the Stage 6/7 startup/transpilation findings**: it's a second, allocation-pattern-level mechanism (not just the bytecode/transpiler *cache*, M6) for making the parse/transpile path cheap on every module load, including cold-start ones the cache can't help with.

**2b. `HiveArray` — a fixed-capacity, bitset-tracked object pool, used for Bun's HTTP server request contexts, with a documented real-world regression that makes its performance relevance concrete rather than assumed.**

`src/collections/hive_array.rs`'s doc comment, quoted directly: *"[`IntegerBitSet`] is backed by a single `usize`, so for `N > 64` it silently held only 64 usable bits — every `HiveArray<_, 128/256/2048>` pool degraded to 64 effective slots and spilled to the heap fallback on the 65th in-flight item. Under HTTP load (the `Body::Value` 256-slot pool, the `RequestContext` 2048-slot pool) this turned every request into a `Box::new`."* This confirms two concrete, numbered pools in production use — a 256-slot pool for `Body::Value` and a **2048-slot pool for `RequestContext`** (the exact mechanism Stage 8's M-series already identified generically as `HiveArray::Fallback` via `server.request_pool.claim()`) — and, importantly, documents that this pooling mechanism was *previously silently broken* (degrading to 64 slots under the old bitset) and that the breakage manifested specifically as extra `Box::new` heap allocations under HTTP load. This is strong evidence the pool is genuinely performance-load-bearing (someone noticed and fixed a regression tied to allocation behavior under load), not merely a "convenience abstraction."

**2c. A separate, general free-list-style pool primitive exists (`src/collections/pool.rs`), independent of `HiveArray`.**

An intrusive singly-linked free list (`Node<T>` with a `next` pointer and `MaybeUninit<T>` payload) — a different pooling shape (unbounded/dynamic free list vs. `HiveArray`'s fixed-capacity bitset-tracked slots) for different use cases. Not fully traced to every call site this stage; flagged as an area a future stage could map exhaustively if the article needs a complete pool inventory.

**2d. `path_buffer_pool` (already noted in `src/CLAUDE.md`, re-confirmed here as allocation-relevant) avoids a 64 KB stack allocation on Windows per path operation** by pooling `PathBuffer` (`[u8; PATH_MAX_BYTES]`) instances rather than stack-allocating fresh ones — a stack-usage optimization as much as a heap-allocation one, included here because it's part of the same general "Bun pools hot-path buffers" pattern as `ByteListPool` (Stage 3, stream buffers) and `HiveArray` (2b).

**2e. `MaxHeapAllocator` and `heap_breakdown` are debug/diagnostic tooling, not production performance mechanisms.** `MaxHeapAllocator` (`src/bun_alloc/MaxHeapAllocator.rs`, doc comment: *"Single allocation only"*) is a capped single-buffer allocator; `heap_breakdown.rs` wraps macOS `malloc_zone_*` per-tag heaps for debug-build memory attribution. Neither is claimed as performance-relevant by its own source comments; both are correctly filed as **convenience/diagnostic abstractions**, explicitly not conflated with the performance-relevant pools above (per the standing instruction to distinguish the two).

### 3. Native ↔ JS memory ownership

**3a. `Buffer`/`Uint8Array`/`ArrayBuffer` backing memory is allocated through JSC's own `ArrayBuffer` machinery — not directly tagged as a mimalloc allocation, and not pooled.**

Every JS-visible typed-array/buffer creation site checked this stage (`src/jsc/bindings/BunObject.cpp`, `JSBuffer.cpp`, `Uint8Array.cpp`, `ZigGlobalObject.cpp`) goes through `JSC::ArrayBuffer::create(...)`, `::tryCreateUninitialized(...)`, or `::createFromBytes(...)`, followed by `JSC::JSArrayBuffer::create(vm, structure, WTF::move(buffer))` or `JSC::JSUint8Array::create(...)`/`::createUninitialized(...)`. This is JSC's own `ArrayBuffer`/`ArrayBufferContents` allocation path — a WebKit/`bmalloc`-family mechanism (see 1e) — **not** a direct `mi_malloc` call, and not explicitly re-routed via `USE_BUN_MIMALLOC` (that flag's one confirmed call site, 1f, is IDL-sequence `WTF::Vector` conversion, a different code path from `ArrayBuffer` creation).

**3b. `Buffer.allocUnsafe()`/`Buffer.alloc()`/`Buffer.allocUnsafeSlow()` all allocate a fresh buffer per call — Bun implements no shared-pool/slab mechanism for small `Buffer`s.**

Traced directly: `jsBufferConstructorFunction_allocUnsafeBody` (`JSBuffer.cpp` ~line 537) → `allocBufferUnsafe()` (~line 249) → `createUninitializedBuffer()` (~line 504) → `JSC::JSUint8Array::createUninitialized(...)`. No pool, slab, or offset-tracking structure appears anywhere in this call chain. Bun does expose a `Buffer.poolSize` property (`JSBuffer.cpp` ~line 3289: `putDirectWithoutTransition(..., "poolSize"_s, jsNumber(8192))`) for Node API-surface compatibility, but this is a **static reported number with no discovered backing implementation** — nothing in `JSBuffer.cpp` reads or uses it to carve buffers from a shared region. **This is stated as FACT for "no pooling logic was found in this file," not as FACT for "no pooling logic exists anywhere in Bun" — the search was scoped to `JSBuffer.cpp`, the file that implements the `Buffer` constructor functions, which is the correct and complete location for this specific claim.**

**3c. Node, by contrast, has a real, documented, 64 KB shared `Buffer` pool with a 32 KB carve-vs-standalone threshold.**

`lib/buffer.js`, confirmed via direct fetch this stage: `Buffer.poolSize = 64 * 1024`; the carve-vs-standalone decision is `size < (Buffer.poolSize >>> 1)` (i.e., under 32 KB); `createPool()` allocates one over-sized `ArrayBuffer` via `createUnsafeAlignedBuffer(poolSize, kPoolAlignment)`, marks it untransferable (so a worker `postMessage` transfer can't steal the shared region), and tracks a `poolOffset` that advances as 8-byte-aligned slices are carved off for each small `Buffer.allocUnsafe()` call; `createPool()` re-runs to get a fresh pool once the current one is exhausted.

**This is a direct, verifiable counter-example to "Bun allocates less than Node" as a blanket claim**, for this specific, extremely common operation (allocating a small `Buffer`): Node amortizes one large allocation across many small `Buffer`s; Bun performs one full `ArrayBuffer`-machinery allocation per `Buffer.allocUnsafe()` call, every time.

**3d. `JSValue`/native object lifetime crossing the Rust↔JSC boundary is managed via `Strong`/`Weak` GC handles (already established in Stage 5/8), not via Rust ownership alone.**

Re-confirmed, not re-derived this stage: `bun_jsc::Strong::create`/`drop` for JS-thread-only strong GC roots, `Weak<T>` for GC-clearable references — this is JSC's conservative-stack-scanning GC (M2) plus an explicit handle layer for anything that needs to survive past the native call frame that created it, not a Rust-ownership replacement for GC.

### 4. Node's allocator configuration

**4a. Node does not ship or configure a custom global allocator. `ArrayBuffer` backing memory goes through V8's own default allocator, which V8 itself documents as `malloc`/`free`-based.**

Traced this stage: `node_internals.h` declares `class NodeArrayBufferAllocator : public v8::ArrayBuffer::Allocator` with a member `std::unique_ptr<v8::ArrayBuffer::Allocator> allocator_{v8::ArrayBuffer::Allocator::NewDefaultAllocator()};`. `src/api/environment.cc`, confirmed directly: `NodeArrayBufferAllocator::Allocate`/`AllocateUninitialized`/`Free` each do bookkeeping (`COUNT_GENERIC_USAGE`, a `total_mem_usage_` atomic counter) and then delegate straight through: `allocator_->Allocate(size)`, `allocator_->AllocateUninitialized(size)`, `allocator_->Free(data, size)`. V8's own header (`v8-array-buffer.h`, `v8/v8@main`), the doc comment for `NewDefaultAllocator()`, quoted directly: *"When the sandbox is enabled, this allocator will allocate its backing memory inside the default global sandbox. Otherwise, it will rely on malloc/free."* Node does not enable V8's pointer-compression sandbox by default as far as this stage's sourcing shows (not independently re-verified this stage — flagged as open item). **Net: Node's `ArrayBuffer`/`Buffer` backing memory, apart from the pool in 3c, is standard system `malloc`/`free` — the same general allocator family a naive C program would use, with no custom global-allocator equivalent to Bun's mimalloc.**

**4b. This directly falsifies "Bun is fast because it uses mimalloc, and Node doesn't have anything like that" as a complete explanation** — Node's `ArrayBuffer` path uses plain system malloc, which is *slower* in the general case than mimalloc for many allocation patterns (a real, plausible Bun advantage for this specific layer) — but Node compensates for the single most common small-allocation pattern (`Buffer.allocUnsafe`) with the pool in 3c, which Bun does not have an equivalent for. **Whether "faster allocator, no pool" beats "slower allocator, real pool" for realistic `Buffer`-heavy workloads is not resolvable from source — it is a clean, concrete Stage 13 candidate** (repeated small `Buffer.allocUnsafe()` calls, Bun vs. Node, allocator-attributable time).

### 5. Deno's allocator configuration

**5a. Deno's Rust build does not set a custom global allocator by default.**

Searched `cli/lib.rs` and the whole `cli/`/`runtime/`/`core/` tree for `#[global_allocator]`: the only hit is gated `#[cfg(feature = "dhat-heap")] #[global_allocator] static ALLOC: dhat::Alloc = dhat::Alloc;` (`cli/lib.rs` ~line 84–86) — `dhat` is a heap-profiling instrumentation allocator (part of the `dhat-rs`/Valgrind-DHAT-compatible profiling ecosystem), used to generate memory-profile reports for developers who opt into the `dhat-heap` Cargo feature, not a production performance allocator. No `jemallocator`/`tikv-jemallocator`/`mimalloc` dependency was found in `Cargo.toml`. **By default, Deno's Rust-side allocations (including its own native/op-layer code) use the Rust toolchain's default system allocator** (glibc `malloc` on Linux, the platform allocator elsewhere) — the same family as Node's default, not Bun's.

**5b. Deno's V8 `ArrayBuffer` allocator was not found to be customized either** — no `ArrayBufferAllocator`/`array_buffer_allocator`/`new_default_allocator` override was found anywhere in the `deno_core`/`cli`/`runtime` Rust source searched this stage, consistent with Deno also relying on V8's built-in default (malloc/free-based) `ArrayBuffer` allocator, same as Node (4a). **Node and Deno converge on the same underlying `ArrayBuffer` allocator family (V8's own default), despite having unrelated codebases** — a real point of similarity, not something to be spun as a Deno-specific weakness relative to Node.

**5c. Deno's Buffer-pool equivalent was not found.** `ext/node/polyfills/` (Deno's Node-compatibility layer) was grepped for `poolSize` with no hits — this stage did not confirm whether Deno's `node:buffer` polyfill replicates Node's 64 KB pool mechanism, defers to a different Buffer implementation entirely, or has no equivalent. **Flagged as UNKNOWN, not FACT of absence** — a `grep` miss on one search term is weaker evidence than the positive confirmations elsewhere in this stage, and is stated with that caveat rather than promoted to a claim.

### 6. Testing the six candidate claims, explicitly

- **"Bun is fast because it uses mimalloc."** — Partial/imprecise as stated. Mimalloc is real (1a–1d) and does replace Node's/Deno's default-malloc `ArrayBuffer`/native allocation path *for Bun's own Rust-side code and, on Linux, process-wide malloc* — but it does **not** back Bun's JS object heap (1e, still `bmalloc`) or Bun's `ArrayBuffer`/`Buffer` backing memory (3a, still JSC's own machinery). The claim conflates "Bun's Rust glue code allocates via mimalloc" (true) with "the memory a JS program actually touches is mimalloc-backed" (not established, and the most JS-program-visible allocation path checked — `Buffer` — is not the layer where mimalloc's advantage would show up).
- **"Bun allocates less than Node."** — Not established as a general claim, and directly contradicted for one concrete, common case: small `Buffer` allocation (3b vs. 3c), where Node's pool avoids repeated `ArrayBuffer`-machinery allocations that Bun's implementation performs on every call. Aggregate allocation-count comparison was not measured (no source-level count exists to check this against) — this is a UNKNOWN, not a FACT in either direction, except for the one specific counter-example found.
- **"Bun uses pools everywhere."** — False as an unqualified claim. Real pools exist and are load-bearing (2b's `HiveArray` for HTTP request contexts and body values, 2d's path-buffer pool, Stage 3's `ByteListPool` for streams) — but a specific, high-traffic path (`Buffer` allocation, 3b) has **no** pool. "Bun pools its own hot internal native-side structures where it has identified the need; it does not pool JS-visible buffer allocations" is the accurate, narrower version.
- **"Rust gives Bun cheaper memory management."** — Weak as a differentiator specifically because **Deno is also written in Rust** for its native/op layer (established in prior stages and reconfirmed here via the same source tree). Whatever generic benefit Rust's ownership model provides for native-side memory management (no tracing-GC pauses in native code, RAII-driven deterministic frees) is shared by Deno, not exclusive to Bun. It is also not clearly relevant to the JS-visible allocation cost that dominates most JS workloads, since JS objects are GC-managed by JSC/V8 in all three runtimes regardless of the host language.
- **"V8's GC is the main reason Node allocates more."** — Untestable from source alone; this stage found no way to measure "allocates more" without running code. What IS established (M2, prior stages) is a *mechanism* difference — V8's moving GC requires `HandleScope` bookkeeping that JSC's conservative-stack-scanning GC does not — but that is a per-native-call bookkeeping cost, not a claim about allocation *volume*. Conflating the two would be exactly the "mechanism confidence promoted to magnitude confidence" error this project is built to avoid. Stays UNKNOWN pending a Stage 13 experiment that actually counts allocations (e.g., via each runtime's heap-profiling hooks) for an identical workload.
- **"Bun avoids GC because its native code uses manual allocation."** — False as stated, in the way that matters. Bun's *native Rust code* has no tracing GC (true, but see the Rust-parity point above — Deno's native code doesn't either, and Node's C++ internals don't either). Bun's **JS-visible values are still garbage-collected**, by JSC, exactly as Node's and Deno's are by V8 (M2 territory) — "manual allocation" in the Rust layer has no bearing on whether a user's JS objects are GC'd, and they are. This claim would mislead a reader into thinking Bun's JS programs somehow escape GC pauses; they do not.

---

## What we don't know

- Aggregate allocation *count* or *volume* differences between the three runtimes for any realistic workload — nothing in this stage measured this, and source reading alone cannot establish it (per the "V8's GC" claim above).
- Whether V8's pointer-compression sandbox (which *would* change `ArrayBuffer` allocation to a sandboxed region rather than plain `malloc`) is enabled by default in current Node or Deno builds — not independently verified this stage; flagged as an open item since it would qualify claim 4a/5b if enabled.
- The full call-site inventory for `src/collections/pool.rs`'s free-list pool (2c) — confirmed to exist and be structurally distinct from `HiveArray`, not exhaustively mapped to its usage sites.
- Whether Deno's `node:buffer` polyfill has any pooling equivalent to Node's (5c) — search came back empty, not confirmed absent.
- The actual cost, in practice, of JSC's `bmalloc`-backed `ArrayBuffer` path relative to mimalloc or Node's pooled/malloc path — no benchmarking done.
- Whether Bun's mimalloc THP-disable choice (1d) generalizes to the same magnitude on non-`always`-mode THP systems (most modern Linux distros default to `madvise`, where the source comment itself notes "nothing in bun asks for huge pages anyway" — meaning the reported numbers may be a best-case-for-the-optimization measurement, not a representative default-config one).
- Reproducible commit pin for the Deno clone used this stage (sourcing-transparency gap noted at the top of this document).

---

## Evidence

Every FACT claim above cites a specific file and, where given, line range, from one of the four pinned/dated sources listed at the top. Quoted comments and code were read directly this stage. Two items rest on general/prior-established knowledge without fresh re-verification this stage and are marked as such inline: Node's V8 pointer-compression-sandbox default-off status, and the completeness of the `Buffer.poolSize` non-usage claim (scoped explicitly to `JSBuffer.cpp`).

---

## Counter-evidence

Actively hunted, per standing instruction:

- **Bun's own JS object heap undermines the simplest form of "Bun uses mimalloc" as a speed explanation** — the allocator most directly responsible for the memory a running JS program's objects live in is `bmalloc`, unchanged from upstream WebKit, in all three runtimes' JSC-equivalent... except Node and Deno don't use JSC at all, they use V8. So the fairer framing is: JS-object-heap allocation is engine-owned (`bmalloc` for Bun/JSC, V8's own generational-heap allocator for Node/Deno) in all three cases, and mimalloc's role is confined to each runtime's *native/embedding* layer — which is a real but narrower claim than "Bun's fast because of its allocator."
- **Node's Buffer pool is a real, concrete case of Node doing MORE allocation-avoidance engineering than Bun for a common operation** — directly contradicts any framing that assumes Bun is simply doing less work everywhere. This is the same shape of finding as Stage 8's "Node already batches response writes" — a recurring pattern across this whole project of "the obvious performance story turns out backwards for at least one concrete mechanism."
- **The Rust-gives-cheaper-memory-management claim doesn't distinguish Bun from Deno** (both Rust-native), which is itself a finding worth stating plainly rather than letting the claim pass unexamined.
- **No counter-evidence found against** the core disambiguation that mimalloc and bmalloc are doing genuinely different jobs in Bun (1e/1f) — this is a clean, unambiguous structural fact, not a magnitude claim, and nothing found this stage complicates it.

---

## Confidence

| # | Claim | Mechanism confidence | Magnitude confidence |
|---|---|---|---|
| 1a | mimalloc is Bun's unconditional Rust-side global allocator | High | N/A (structural fact) |
| 1b | mimalloc overrides system malloc on Linux only, by design | High | N/A |
| 1c | Non-default mimalloc build tuning (THP, exit-skip, TLS model) | High | Not measured, except 1d's narrow self-reported figure |
| 1d | THP-disable reduces peak RSS on an `always`-THP box | Medium (single self-reported comment, no disclosed methodology, not independently reproduced) | Explicitly not a speed claim — RSS only |
| 1e | JS object heap is bmalloc, not mimalloc | High | N/A (structural fact) |
| 1f | `USE_BUN_MIMALLOC` redirects specific WTF containers, not the GC heap | High | N/A |
| 2a | `MimallocArena`/`AstAlloc` bulk-frees AST allocations per parse | High | Not measured |
| 2b | `HiveArray` pools (256/2048 slots) back HTTP request/body contexts, previously regressed | High | Not measured (though the documented regression is suggestive that the mechanism matters under load) |
| 3a | Buffer/ArrayBuffer backing goes through JSC's own machinery | High | N/A |
| 3b | Bun's `Buffer.allocUnsafe` has no pool | High (scoped to `JSBuffer.cpp`) | N/A (absence, not magnitude) |
| 3c | Node's 64 KB/32 KB-threshold Buffer pool | High | Not measured |
| 4a | Node's ArrayBuffer allocator is V8's default (malloc/free) | High | N/A |
| 5a | Deno has no custom global allocator by default | High | N/A |
| 5b | Deno's V8 ArrayBuffer allocator is also unmodified | Medium (absence-of-evidence from grep, not a positive confirmation) | N/A |

---

## Next

Stage 9 complete, pending review. Per standing instruction: **do not start Stage 10, do not start Stage 13 benchmarks.** Waiting for approval.

---

### Evidence Map changes

**New:**
- **M19 — mimalloc vs. bmalloc: Bun's allocator story is two allocators, not one, doing two different jobs.** High confidence for mechanism (multiple independent source confirmations: build config, linked libraries, WTF-container-level flag). Not a magnitude claim — this is the stage's central disambiguating finding and top article-centerpiece candidate (see below).
- **M20 — Bun's `Buffer.allocUnsafe()` has no pooling; Node's does (64 KB pool, 32 KB threshold).** High confidence for both halves (Bun: absence confirmed in `JSBuffer.cpp`; Node: presence confirmed in `lib/buffer.js`). Not a magnitude claim without Stage 13. This is the stage's strongest counter-evidence finding and a strong myth-busting/article candidate.
- **M21 — Bun's AST-parsing arena (`AstAlloc`/`MimallocArena`) bulk-frees per-module parse allocations via arena reset, avoiding per-node `Drop`/`free` calls.** High confidence for mechanism. Extends M5/M6 (startup/transpile-cost territory) with a second, allocation-pattern-level mechanism alongside the bytecode cache. Not measured for magnitude.
- **M22 — `HiveArray`'s documented real-world pooling regression (silent degradation to 64 slots, `Box::new` fallback under HTTP load) is direct evidence the request-context pool is genuinely performance-load-bearing, not a convenience abstraction.** High confidence (self-documented bug/fix in source). Article-worthy specifically because it's evidence of engineering intent, not inferred importance.
- **M23 — Node's and Deno's `ArrayBuffer` allocators both trace back to V8's own default (malloc/free-based); neither runtime customizes it. Bun customizes its equivalent layer (mimalloc) but that layer doesn't back the JS object heap either (see M19).** High confidence for the Node/Deno half (direct source chain to V8's own doc comment); Medium for the Deno half specifically (absence-of-evidence). Presented as a three-way convergence/divergence map, not a ranking.

**Strengthened:**
- **M2** (JSC non-moving GC vs. V8 handle-scope cost) — the "V8's GC is the main reason Node allocates more" claim was explicitly tested this stage and found to conflate GC *mechanism* (M2's actual scope) with allocation *volume* (untested) — this sharpens M2's boundary rather than expanding its claims, reinforcing the standing rule against promoting mechanism confidence to magnitude confidence.
- **M13/M14** (Bun's installer clonefile/hardlink strategy, DirInfo resolver cache) — indirectly reinforced by this stage's general finding that Bun's "default-on, pre-built pooling/caching infrastructure" pattern (M6, M13, M14, now M21/M22) recurs across parsing, resolution, and HTTP-serving subsystems — consistent engineering habit, cross-referenced not re-scored.

**Weakened:**
- None outright, but **M1's general framing benefits from M19's sharper allocator disambiguation** — anyone tempted to read "Bun rewrote in Rust + uses mimalloc" as a unified performance story now has a documented reason that story is incomplete (the JS heap isn't mimalloc-backed either way).

**Unchanged:**
- M3, M4, M5 (except the cross-reference noted above), M6 (except the cross-reference), M7, M8, M9, M10, M11, M12, M15, M16, M17, M18 — not touched by this stage's scope.

**Open items added:**
24. **[New, Stage 9]** Whether V8's pointer-compression sandbox is enabled by default in current Node/Deno release builds (would change the `ArrayBuffer`-allocator-is-plain-malloc claim, 4a/5b, if so).
25. **[New, Stage 9]** Full call-site inventory for `src/collections/pool.rs`'s free-list pool.
26. **[New, Stage 9]** Whether Deno's `node:buffer` polyfill implements a pooling equivalent to Node's 64 KB Buffer pool.
27. **[New, Stage 9]** Reproducible commit pin for the Deno clone used in this stage (sourcing-transparency gap — should be closed if Deno's memory internals are revisited).
28. **[New, Stage 9]** Actual allocation-count/volume comparison across the three runtimes for a representative workload — the only thing that can resolve the "does Bun allocate less" and "does V8's GC cause more allocation" claims — Stage 13 candidate.
29. **[New, Stage 9]** Small-`Buffer`-allocation throughput comparison, Bun vs. Node — isolates M20's pool-vs-no-pool trade-off — clean, cheap Stage 13 candidate.

### Ranking will be revisited after
- ~~Stage 9 (memory/allocation)~~ — **done, pending review.** M19–M23 added; M2 boundary sharpened via explicit conflation-testing of the "V8's GC" claim; M1's framing indirectly qualified.
- Stage 10 (benchmark methodology audit) — not started.
- Stage 13 (experiments) — the only stage that can resolve open items 24, 28, 29 above and the magnitude column for M19–M23.

---

## Article-strategy recommendation per finding

| Finding | Article-worthy? | Benchmark-worthy? | Notes |
|---|---|---|---|
| M19 (mimalloc vs. bmalloc split) | **Both.** | Yes (Stage 13: does the Linux malloc-override measurably help vs. macOS/Windows without it?) | Strongest candidate for a "here's what 'Bun uses mimalloc' actually means" explainer section — precise, falsifiable, surprising. |
| M20 (Buffer pool asymmetry) | **Both.** | Yes — cheap, clean, high-signal-to-noise microbenchmark. | Best myth-busting candidate this stage; directly, concretely contradicts "Bun allocates less than Node." |
| M21 (AST arena bulk-free) | Article-worthy as supporting detail, not a standalone section. | Low priority — likely dominated by the already-established transpiler cache (M6) for any real measurement. | Strengthens the startup/transpile narrative without needing its own centerpiece. |
| M22 (HiveArray regression story) | **Article-worthy specifically as narrative color** — "here's proof the pool matters, from the runtime's own bug tracker" is a compelling, honest way to substantiate a pooling claim without overclaiming magnitude. | Not benchmark-worthy on its own (the bug is already fixed; nothing to measure now). | Use as supporting evidence for M2b/pooling claims generally, not a numbered claim of its own in reader-facing prose. |
| M23 (Node/Deno ArrayBuffer convergence) | Article-worthy as a fairness/context beat — shows Node and Deno aren't naive, they share V8's own tooling. | Not really benchmark-worthy by itself (it's an absence-of-customization finding). | Good for the "don't strawman Node/Deno" obligation. |
| Six-claims section generally | **Highly article-worthy** — this is exactly the "claim → source-check → survives in a narrower/reframed form or doesn't" structure the whole project is built around. | Three of the six (allocation volume, Buffer pool throughput, GC-attributable allocation) point directly at Stage 13 experiments. | Consider structuring an article section explicitly around these six claims, mirroring this stage's own myth-audit structure. |
