# JS ↔ Native Boundary (FFI)

Numbered to match the plan's stage 5, investigated third (after Stage 2 surfaced the GC/handle-scope lead). Repo pin: `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4`.

## The question

Stage 2 produced a specific, testable claim: JSC's non-moving, conservative-stack-scanning GC means native code can hold a raw `JSValue` with no handle-scope bookkeeping, while V8's moving GC requires every native reference to go through a tracked `Local<T>` handle. This stage traces the *actual call path* in all three runtimes to see whether that theoretical difference shows up as a real, per-call structural cost difference — not just a plausible story.

## How Bun's JS→native calls actually work (bindgen)

**FACT**, traced directly from source, three layers deep:

1. **API declared once, in TypeScript, as data — not hand-written per binding.** A `.bind.ts` file declares a function's argument/return schema using a small DSL (`fn({ args: {...}, ret: t.i32 })`). Example straight from Bun's own docs, matching the actual `bindgen_test.bind.ts` file in the repo:
   ```ts
   export const add = fn({
     args: { global: t.globalObject, a: t.i32, b: t.i32.default(-1) },
     ret: t.i32,
   });
   ```
   [`docs/project/bindgen.mdx`, oven-sh/bun@8326d1b](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/docs/project/bindgen.mdx) and [`src/jsc/bindgen_test.bind.ts`](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/jsc/bindgen_test.bind.ts).

2. **Codegen (`src/codegen/bindgen.ts` + `bindgen-lib.ts`) emits a C++ thunk** that validates/coerces JS arguments per the WebIDL-flavored rules in the schema (e.g. `t.i32` wraps out-of-range numbers via modulo; `t.DOMString`/`t.UTF8String` control string conversion), then calls straight into the Rust implementation. No IPC, no separate process, no intermediate serialization format (like JSON or a wire protocol) — it's a same-process, same-address-space function call with argument coercion inlined into the generated C++.

3. **The Rust function receives a `&CallFrame`, and arguments are read directly out of JSC's own call-frame register layout — not deserialized.** From `src/jsc/CallFrame.rs`:
   ```rust
   pub fn arguments(&self) -> &[JSValue] {
       unsafe {
           core::slice::from_raw_parts(
               self.as_unsafe_js_value_array().add(OFFSET_FIRST_ARGUMENT),
               self.arguments_count() as usize,
           )
       }
   }
   ```
   The comment block above it in the same file lays out JSC's actual call-frame stack layout (`argN … arg1, arg0, this, argumentCountIncludingThis, callee, codeBlock, return-address, callerFrame`) — this is a pointer straight into the interpreter/JIT's live stack frame, not a copy. [`src/jsc/CallFrame.rs`, oven-sh/bun@8326d1b](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/jsc/CallFrame.rs)

4. **The host function itself is a raw C-ABI function pointer JSC calls directly**, not a table lookup through a dispatcher: `pub type JsHostFn = unsafe extern "C" fn(*mut JSGlobalObject, *mut CallFrame) -> JSValue;` (with a `sysv64` variant on Windows x64, because JSC always uses System V calling convention there). [`src/jsc/host_fn.rs`, oven-sh/bun@8326d1b](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/jsc/host_fn.rs)

5. **No exception-unwinding machinery crosses the boundary.** Bun builds with `panic = "abort"`, and the source comment is explicit about why that's safe here: *"JSC does not throw C++ exceptions across its public API, so there is no foreign unwind to catch either."* [`src/jsc/host_fn.rs`, same file, comment block above `to_js_host_call`]. JS-level exceptions are instead signaled through JSC's own exception-check-scope mechanism, which is a correctness auditing tool (see below), not a stack-unwinding mechanism.

6. **The "exception scope" that does exist is a near-zero-cost, debug-oriented correctness check, not a GC handle.** `src/jsc/TopExceptionScope.rs` defines a scope guard whose size is `56` bytes under debug/ASAN builds but shrinks to **`8` bytes in release builds** (`#[cfg(not(any(debug_assertions, bun_asan)))] const SIZE: usize = 8;`). Its job (per Bun's `CLAUDE.md`, which documents `BUN_JSC_validateExceptionChecks=1` as an opt-in debug flag) is to catch places where code fails to check for a pending JS exception before continuing — a correctness tool descended from JSC's own internal `ExceptionScope` idiom, unrelated to V8's GC-rooting `HandleScope`. Important not to conflate the two just because the names rhyme.

**Net effect, stated carefully:** in the common case, a Bun native API call is: JS calls a function → JSC hands the interpreter/JIT's own call-frame pointer to a plain C function pointer → that function reads arguments via pointer arithmetic into JSC's live stack → does work → returns a `JSValue` (itself a NaN-boxed 64-bit scalar, cheap to return in a register) directly. There is no dedicated "handle" object created, tracked, or torn down for the call itself.

## How Node's core native bindings actually work (V8 C++ API, not N-API)

**FACT**, verified by fetching the live file from Node's own repo, not a summary. `src/node_file.cc`, the C++ backing for `node:fs`, `require("node:fs").accessSync` et al.:

```cpp
void Access(const FunctionCallbackInfo<Value>& args) {
  Environment* env = Environment::GetCurrent(args);
  Isolate* isolate = env->isolate();
  HandleScope scope(isolate);
  ...
  BufferValue path(isolate, args[0]);
```
[`src/node_file.cc`, nodejs/node@main](https://github.com/nodejs/node/blob/main/src/node_file.cc), confirmed present at multiple call sites in the same file (`Access`, `ExistsSync`, `InternalModuleStat`, `FileHandle::New`, etc. — all open with `HandleScope scope(isolate)` or equivalent before touching any `v8::Local<>`).

This confirms the Stage 2 hypothesis concretely: **every V8-facing native binding function in Node's own core creates a `HandleScope` on entry**, because `args[0]` is a `v8::Local<Value>` — a handle into V8's handle table that the (moving) GC needs to be able to find and rewrite. This is not N-API overhead (a separate, even heavier compatibility shim for third-party native addons) — this is Node's *own internal* binding layer for its own built-ins, and it still pays the handle-scope cost, because that's how you're required to touch a V8 object from C++ at all.

**What a `HandleScope` costs:** conceptually, entering a `HandleScope` pushes a new block onto V8's handle stack (so handles created within it can be bulk-invalidated on scope exit) — cheap relative to, say, a memory allocation, but non-zero, and it exists purely because of V8's moving-GC design (Stage 2), not because of anything about the operation being performed. **INFERENCE**: this is real, structural per-call overhead that Bun's binding path does not have an equivalent of — but see "what this does NOT mean" below before over-claiming magnitude.

## How Deno's ops (op2) actually work — and why this is not a strawman comparison

This is the part of the investigation most likely to produce a lazy, wrong conclusion if we're not careful, so it gets its own subsection. **Deno's own binding layer is not naive.** Deno.core.ops (the `#[op2]` proc macro in `deno_core`) was, in the crate's own words, **"designed to provide an extremely fast V8→Rust interface layer"** [docs.rs/deno_core, `op2` macro documentation — primary source, official crate docs]. Concretely:

- `op2` supports **V8 Fast API Calls** (`#[fast]` attribute) — the same V8 mechanism Chrome itself uses for hot DOM bindings — which lets JIT-compiled JS call into Rust through *"a very thin Fast API trampoline function"* bypassing the general/slow V8 calling convention entirely for eligible signatures. Per an independent technical resource on Deno internals (Denonomicon, written by a Deno contributor, covering the *Fast API path used for `Deno.dlopen`-style FFI* — a related but distinct fast-call mechanism from op2's own use of the same underlying V8 feature): fast-path calls can run in **under 10 nanoseconds**, versus roughly **100–150 ns** for the generic/slow-path binding fallback. [denonomicon.deno.dev/performance] — **treat this specific ns figure as describing Deno's `Deno.dlopen` FFI fast path specifically, not proven identical for every `op2`-based builtin; logged as a distinct-but-related mechanism, not conflated.**
- Arguments prefer a **scopeless conversion trait (`FromV8Scopeless`)** by default, explicitly to avoid the cost of scoped (`#[scoped]`, `FromV8`) conversions where possible — the docs literally flag scoped conversion as *"may be slow"* — meaning Deno's own binding layer is itself trying to dodge V8 handle-scope-adjacent costs wherever the argument shape allows it.
- Known, documented limitations of the fast path: **structs aren't supported over Fast API, non-blocking/async calls can't use it, and fast-call string args are restricted to Latin-1** — meaning many realistic calls (async ones especially — a large fraction of a JS runtime's I/O-facing API surface) still fall back to the slower, scope-carrying generic path.

**Fair conclusion, not a strawman one:** Deno's engineering intent is the same as Bun's here — minimize the cost of crossing into native code — but Deno is doing this *despite* V8's moving-GC design, opportunistically, for the subset of calls that qualify for Fast API. Bun doesn't need an opportunistic fast path for this, because JSC's non-moving/conservative GC means the "slow path" V8 would otherwise fall back to (full handle-scope-carrying call) simply isn't a cost JSC's calling convention imposes in the first place. **This is the most defensible, specific version of the "Bun has fewer JS/native boundary costs" claim this project has found: not "Bun has fewer abstractions" in the abstract, but "Bun's default call path is structurally what V8's opportunistic fast path is" — because of the GC design, not because of Zig/Rust or raw effort.**

## Counter-evidence and things that complicate a clean story

- **We have not measured this.** Everything above is a structural/architectural comparison from source, not a benchmark. A HandleScope push/pop and a Fast-API-ineligible V8 call are not free, but they are also not necessarily *large* relative to the actual work most bindings do (a real fs syscall, a crypto operation, a network read). If the binding's own work dominates wall-clock time, the calling-convention delta could be noise. **This must be an actual experiment (Stage 13), not asserted.**
- **Async operations weaken this story for all three runtimes roughly equally.** Deno's own docs admit non-blocking calls can't use the Fast API path at all — they go through the slower path regardless. A large share of real-world Node/Deno/Bun API usage (fs, network, timers) is async, where the call itself is a small fraction of the total operation cost (queuing, event-loop scheduling, the actual I/O) — meaning this finding is **most relevant to synchronous, high-frequency native calls** (e.g. `Buffer` operations, `crypto.createHash().update()` in a loop, JSON operations that dip into native code, `fs.*Sync`), and **much less relevant to a single `await fetch()`** where the call-boundary cost is dwarfed by network latency.
- **N-API (third-party native addons) is a different, heavier comparison** that we have not yet examined for any of the three runtimes — Node's N-API adds its own stable-ABI indirection on top of the V8 HandleScope cost; Bun and Deno both have their own N-API-compatibility shims (Bun's is visible in `test/napi/` per `CLAUDE.md`) that may reintroduce comparable overhead for that specific compatibility path even if their "native" bindings don't have it. Not yet investigated — logged as open.
- **JSC is not free of all safety bookkeeping** — the exception-check-scope mechanism exists and does real work (auditing exception-check discipline), it's just cheap in release builds (8 bytes) rather than absent. Fair to say "cheaper," unfair to say "zero cost, full stop."
- We have exactly one clean, apples-to-apples source snippet per runtime (Bun's `bindgen_test`, Node's `Access` in `node_file.cc`, Deno's `op2` docs). We have not yet sampled multiple call sites per runtime to check this pattern holds broadly rather than being cherry-picked. **Reasonable next check, logged.**

## What we know

- Bun's native-API calls read arguments directly out of JSC's live call-frame via pointer arithmetic, with no serialization step and no per-call handle object. **FACT**, verified from `CallFrame.rs` and `host_fn.rs` source.
- Node's own core bindings (not just N-API/third-party addons) require an explicit `HandleScope` per call because `v8::Local<>` values must be tracked for V8's moving GC to update on relocation. **FACT**, verified directly against live `node_file.cc` source, multiple call sites.
- Deno's `op2` binding layer is a deliberately-engineered fast path (V8 Fast API calls where eligible) that tries to minimize the same cost Node's plain V8 C++ bindings pay in full — it is not a naive/slow implementation, and should not be portrayed as one. **FACT**, from `deno_core`'s own official docs.
- Bun's exception-propagation mechanism across the JS/native boundary relies on JSC not throwing C++ exceptions across its public API at all (avoiding unwind-table cost), backstopped by a debug-only correctness scope that's 8 bytes in release builds. **FACT**, from source + code comments.

## What we don't know yet

- The actual measured nanosecond cost of a Bun bindgen call vs a Node HandleScope'd V8 call vs a Deno op2 fast-call vs a Deno op2 slow-call, for a comparable operation. **Needs a Stage 13 microbenchmark** — this is exactly the kind of claim that's cheap to test and easy to get wrong by intuition alone.
- Whether this per-call delta is large enough to matter at the scale of a realistic API (e.g. does it explain any measurable fraction of an HTTP-server or JSON-parsing benchmark gap), or whether it's dwarfed by the actual work being done. **This is probably the single highest-value experiment for the whole article** — logged as top priority for Stage 13.
- N-API/native-addon-compatibility-path costs for all three runtimes (separate from their "native" binding paths).
- Whether Bun's bindgen path has any hidden cost we haven't found yet (e.g. GC safepoint checks, argument-count/type validation cost in the generated C++ thunk) that could offset part of the theoretical advantage. We've read the *rationale* comments but not exhaustively profiled the generated C++ thunk code itself.

## Confidence

**Medium-high on the architectural claim** (Bun's default call path structurally resembles what V8 treats as an opportunistic fast path, because of JSC's GC design) — this is well-sourced from primary source on all three sides and internally consistent with Stage 2's GC findings. **Low, currently, on the performance-magnitude claim** — we have zero measured data point for this specific mechanism yet. The article should present the mechanism as demonstrated and the magnitude as an open, testable hypothesis, exactly per your evidence-labeling rules: this section is **FACT** (mechanism) leading to **HYPOTHESIS** (this meaningfully affects real-world benchmarks), not **FACT** all the way down.

## Next

Stopping here per your review-each-stage instruction. This stage also generated a concrete, high-value candidate for Stage 13 (experiments): a microbenchmark that calls a trivial native-bound function (e.g. an integer add, or a no-op) in a tight loop, in Bun vs Node (via a small N-API or internal binding) vs Deno (via a `op2`-backed Deno API), to isolate calling-convention overhead from actual-work cost. Recommend queuing that specific experiment design for Stage 13 rather than running it ad hoc now.
