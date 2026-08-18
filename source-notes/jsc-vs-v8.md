# JavaScript Execution: JavaScriptCore vs V8

Scope check per your brief: going deep enough to establish *which* JSC/V8 differences plausibly matter for Bun vs Node/Deno, not a full engine-internals tour.

## Both engines are 4-tier, more similar than popular articles suggest

**FACT.** Both are modern multi-tier JIT engines with an interpreter, a fast baseline compiler, a mid-tier, and a peak optimizer:

| Tier | JavaScriptCore | V8 |
|---|---|---|
| 0 — Interpreter | LLInt (Low Level Interpreter) | Ignition |
| 1 — Baseline JIT | Baseline JIT | Sparkplug (added 2021) |
| 2 — Mid JIT | DFG (Data Flow Graph) | Maglev (added Chrome M117, ~2023) |
| 3 — Optimizing JIT | FTL ("Faster Than Light", built on B3/Air, itself derived from LLVM-style SSA) | TurboFan |

Sources: [WebKit JSC architecture docs](https://docs.webkit.org/Deep%20Dive/JSC/JavaScriptCore.html) (primary); [V8 Maglev blog post](https://v8.dev/blog/maglev) (primary).

Tier-up in JSC: LLInt → Baseline at roughly **6 invocations or 100 loop iterations**; Baseline → DFG at roughly **60 invocations or 1000 loop iterations**; DFG → FTL for code that's "extremely hot" (thousands of invocations / tens of thousands of loop iterations). [docs.webkit.org, JavaScriptCore.md] — **FACT**, numbers as stated in WebKit's own docs (should be treated as approximate/tunable, not hard constants — Stage-10 candidate to verify against current `Options.h` if we want exact current thresholds).

V8's design rationale for adding Maglev, in V8's own words: Sparkplug alone left "a large gap between Ignition+Sparkplug and Ignition+TurboFan" because "Sparkplug imposes a relatively low upper limit on the speedup it can provide," and Maglev compiles ~10x slower than Sparkplug but ~10x faster than TurboFan, letting V8 "afford to wait longer before compiling functions with TurboFan." — **FACT**, quoted from [v8.dev/blog/maglev].

**Inference:** the tier architectures are conceptually convergent — both teams independently arrived at "interpreter → fast-compile baseline → mid-tier speculative → heavyweight optimizer." This makes "JSC's JIT pipeline is just better than V8's" an unlikely sole explanation for Bun's edge; if anything, V8's optimizing tier (TurboFan) has had more total engineering-years and is generally regarded (uncertain — no controlled primary-source benchmark found yet) as at least as strong at *sustained peak throughput* for long-running, hot code. Where JSC's design plausibly matters more is the **cheap tiers**, not the expensive one — see below.

## Where the interpreter design plausibly matters: LLInt's near-zero startup cost

**FACT**, from WebKit's own docs: *"The LLInt is intended to have zero start-up cost besides lexing and parsing."* [docs.webkit.org/Deep Dive/JSC/JavaScriptCore.html]

LLInt is written in a portable macro-assembly ("offlineasm") that's compiled once, ahead of time, into real machine code for each supported architecture — so *running* bytecode in LLInt is calling into pre-compiled native routines, not a bytecode-dispatch loop in the traditional sense, and there is no JIT-compile step before a function can run at all. This is architecturally different from needing any warm-up compilation to execute at a reasonable baseline speed.

**Inference (not yet independently benchmarked by us — Stage 10/13 candidate):** for short-lived processes — CLI tools, one-shot scripts, many serverless invocations, `bun run` of a small script — a large fraction of total wall-clock time is spent in code that never gets hot enough to leave the interpreter/baseline tier. If JSC's baseline tiers have a lower constant-factor cost than V8's equivalent tiers (Ignition/Sparkplug) for this regime, that would show up specifically in **startup-class and short-script benchmarks**, and would *not* show up in long-running, CPU-bound loop benchmarks — which is a testable, falsifiable prediction we should check in Stage 13. This is exactly the kind of workload-dependent result the "credible benchmark" literature (Stage 10) needs to isolate, rather than a blanket "JSC is faster."

## Garbage collection: a real, verifiable architectural difference

This is the clearest, most source-verified divergence between the two engines, and it has second-order effects beyond raw GC pause time — specifically on **native-binding cost** (relevant again in Stage 5).

**JavaScriptCore — "Riptide" lineage, current design:** non-compacting (non-moving), generational, mostly-concurrent. Heap split into a small **eden** (young objects) and **old space**; the generational hypothesis (most objects die young) lets eden collections run frequently and cheaply. Marking is concurrent and parallel (mutator, compiler threads, and marking threads run simultaneously); only brief phases require stopping the mutator. Root scanning is **conservative**: JSC suspends the mutator thread via UNIX signals and scans its stack/registers for anything that looks like a pointer, rather than requiring precisely-typed roots. Because it never moves live objects, JSC avoids compaction machinery entirely, using "logical versioning" (bump a global counter instead of physically clearing mark bits) to make sweeping cheap.
Sources: ["Understanding Garbage Collection in JavaScriptCore From Scratch", WebKit blog](https://webkit.org/blog/12967/understanding-gc-in-jsc-from-scratch/) (primary, official); ["Introducing Riptide", WebKit blog](https://webkit.org/blog/7122/introducing-riptide-webkits-retreating-wavefront-concurrent-garbage-collector/) (primary, official, original 2017 design doc — the 2022 post above describes the current evolution of the same lineage).

**V8 — "Orinoco":** generational and **moving/compacting**. Young generation uses a **semi-space Scavenger** (copying collector: half of young-gen memory is always empty as a copy destination; survivors of a second GC get promoted into old space). Old generation uses **Mark-Compact**: mark reachable objects from precise roots, sweep dead space onto free-lists, and *compact* (copy survivors out of) the most-fragmented pages. V8 layers parallel, incremental, and fully concurrent scheduling on top of this, with the old-gen major GC doing concurrent marking in the background followed by a parallel compact/sweep pause.
Source: ["Trash talk: the Orinoco garbage collector", V8 blog](https://v8.dev/blog/trash-talk) (primary, official).

**Consequence that matters beyond GC pause time — FACT, sourced from Bun's own engineering blog:** because V8's collector *moves* objects, V8's embedder API requires **handle scopes** — every native (C++/N-API) reference to a JS value must go through a `Local<T>` handle that V8's GC knows how to find and rewrite when it relocates the object. JSC's non-moving, conservative-stack-scanning collector means a raw `JSValue` can be handed to native code directly, with **no handle-scope bookkeeping required**, because the GC will find the reference by scanning the native stack itself if needed. Quoting Bun's engineering post on implementing V8-API compatibility on top of JSC: *"V8 employs a 'moving, precise' garbage collector requiring handle scopes... JSC uses 'non-moving, conservative' collection via stack-scanning, eliminating the need for explicit handle management."* [bun.com/blog/how-bun-supports-v8-apis-without-using-v8-part-1] — **FACT**, but note this is Bun's own characterization of V8, written for a compatibility-shim article, not a V8-team statement; conceptually consistent with the independently-sourced Orinoco description above, so I'm treating it as reliable, but flagging the single-source-for-this-specific-comparison caveat.

**This is the single most promising lead so far for Stage 5 (FFI/native boundary):** if every native call in V8-land pays for handle-scope creation/teardown and every native call in JSC-land doesn't, that's a structural, per-call cost difference — not a vague "fewer abstractions" claim — and it would matter most exactly where Bun's own APIs (fs, crypto, fetch internals) cross into native code frequently. **Flagged as the top thing to verify with source-level detail in Stage 5.**

## Object representation: NaN-boxing vs pointer tagging (real difference, unclear perf impact)

**FACT**, from the same Bun engineering post: JSC represents JS values via **NaN-boxing** — a 64-bit encoding that hides pointers/ints/specials inside the unused bit patterns of IEEE-754 NaN values. V8 uses **pointer tagging** — the low bits of a machine word indicate the value's type, with small integers ("Smis") stored directly in the tagged word. Both are well-known, mature techniques for avoiding a memory allocation for every number; neither has a clear, source-backed "faster" verdict from what we've found so far. **Logged as an unresolved question** — flagging rather than guessing.

## Hidden classes / shapes: both engines have them, so this is *not* a differentiator

JSC's equivalent of a V8 "hidden class"/"Map" is called a **Structure** (`JSC::Structure`, confirmed directly from source: [`Source/JavaScriptCore/runtime/Structure.h`, WebKit/WebKit@main](https://github.com/WebKit/WebKit/blob/main/Source/JavaScriptCore/runtime/Structure.h)), with a `StructureTransitionTable` for the classic "object shape transitions on property addition" mechanism, and inline caches (both Baseline JIT and DFG use polymorphic inline caching for property access, per the WebKit docs quoted above). This is architecturally the same idea V8 uses (Maps + transition trees + inline caches). **Inference: shape/hidden-class-based property access is not a meaningful point of difference between the two engines** — both converged on it decades ago (JSC's Structure/IC system and V8's Maps/IC system are both well over a decade old). This directly answers part of your "don't assume it's just JSC" instruction: JSC doesn't have some unique hidden-class trick V8 lacks.

## A concrete Bun-specific mechanism worth flagging here (overlaps with Stage 6): on-disk bytecode + transpiler cache

While reading `src/jsc/` for this stage I found something more specific than "JSC vs V8" that plausibly affects real execution/startup speed and is unambiguous from source: Bun has a **versioned, hash-keyed, on-disk cache of both transpiler output and JSC bytecode**.

- `src/jsc/CachedBytecode.rs` wraps `generateCachedModuleByteCodeFromSourceCode` / `generateCachedCommonJSProgramByteCodeFromSourceCode` — C++ JSC calls that produce serialized bytecode for a module or CommonJS program, cached as an opaque `CachedBytecode` (`RefPtr<CachedBytecode>` on the C++ side). [`src/jsc/CachedBytecode.rs`, oven-sh/bun@8326d1b]
- `src/jsc/RuntimeTranspilerCache.rs` maintains a versioned on-disk cache (`.pile` files, keyed by Wyhash) of transpiled output, with an explicit changelog of 21+ cache-format versions in the source comments (e.g. *"Version 19: Sourcemap blob is InternalSourceMap... Version 21: ModuleInfo records a phase byte per requested module"*), showing this is an actively maintained, non-trivial subsystem, not a stub. [`src/jsc/RuntimeTranspilerCache.rs`, oven-sh/bun@8326d1b]

**FACT** that this exists and is wired into the module-load path (by directory placement and naming — `RuntimeTranspilerStore.rs` sits alongside it and does the cache directory lookup). **Not yet verified:** exactly which cache (transpile-only vs full bytecode) is on by default, its hit-rate in a typical `bun run`, and how it compares to V8's/Node's own code-cache mechanisms (Node has had a V8 code cache for `require()` since Node 12, and `--build-snapshot`/single-executable snapshots since Node 18+; Deno does its own eszip/V8 code caching too). **This needs a head-to-head, not an assumption — logged for Stage 6.**

## What we know

- JSC and V8 have structurally similar 4-tier JIT pipelines; neither has an obvious tier-count or tier-strategy advantage. **FACT.**
- JSC's interpreter tier (LLInt) is explicitly designed for near-zero startup cost, executing pre-compiled bytecode handlers rather than requiring any JIT warm-up. **FACT**, plausible driver of short-script/startup wins specifically (not general throughput). **INFERENCE** on the magnitude/scope.
- JSC's GC is non-moving + conservative-stack-scanning; V8's is moving + precise-handle-based. This is the most concrete, source-backed difference we've found, and it has a direct, named consequence: V8 native bindings need handle scopes, JSC ones don't. **FACT**, high-value lead for Stage 5.
- Both engines use hidden-class/shape + inline-cache systems for property access; this is not a point of differentiation. **INFERENCE** from both engines' own docs/source showing equivalent mechanisms.
- Bun maintains its own on-disk bytecode/transpile cache, independent of anything JSC vs V8 gives you for free. **FACT** of existence; performance contribution **not yet quantified**.

## What we don't know yet

- Whether JSC's baseline/interpreter tiers are actually faster in wall-clock terms than V8's Ignition/Sparkplug for equivalent short scripts — we have architectural rationale (LLInt's zero-startup-cost design) but no controlled benchmark yet. **Needs Stage 13 experiment.**
- Whether V8's TurboFan produces faster steady-state machine code than JSC's FTL for long-running CPU-bound loops — no primary-source benchmark found. Popular claims exist in both directions; none met this project's sourcing bar (methodology-disclosed, primary or credible-secondary). **Logged as open, will actively search again in Stage 10.**
- The actual current numeric tier-up thresholds in Bun's shipped JSC build (WebKit docs give "roughly" numbers; Bun could in principle tune `JSC::Options` — no evidence found yet that they do, but not ruled out).
- Quantified NaN-boxing vs pointer-tagging performance delta, if any.
- Real-world hit rate and time savings of Bun's bytecode/transpile cache vs Node's V8 code cache vs Deno's caching.

## Next

Per your instruction to go one stage at a time — pausing here. Recommend Stage 5 (FFI/native boundary) next, since the handle-scope finding above is the strongest concrete lead this project has produced so far and it's explicitly one of your priority areas ("especially important"). Alternatively Stage 3 (native API implementations) or Stage 4 (event loop) if you'd rather follow the plan's numeric order — your call.
