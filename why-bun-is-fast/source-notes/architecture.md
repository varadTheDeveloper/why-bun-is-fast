# Runtime Architecture

Repo snapshot used for all citations below: `oven-sh/bun` @ commit `8326d1bd39a96f1f298c3de195aad15972d4f3b4` (main, checked out 2026-08-16). Cite as:
`https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/<path>`

Method: cloned the repo directly (`git clone --depth 1 --filter=blob:none`) rather than relying on blog summaries, and read the actual source tree, `CLAUDE.md` (the repo's own contributor-facing architecture doc), and `Cargo.toml`/`package.json`.

---

## HEADLINE FINDING (this changes the whole investigation)

**FACT — Bun's runtime is no longer written in Zig. As of v1.4.0 (current `main`), the core runtime is Rust, with C++ for JavaScriptCore bindings.** This is a huge, very recent change and it directly contradicts the "Bun is fast because it's written in Zig" claim you told me not to assume.

Evidence:
- `git grep` for `*.zig` files in `src/` returns **zero** results. `find src -name "*.rs" | wc -l` returns **1513**.
- `CLAUDE.md` (repo root, first paragraph): *"This is the Bun repository - an all-in-one JavaScript runtime & toolkit... It's written primarily in Rust with C++ for JavaScriptCore integration, powered by WebKit's JavaScriptCore engine."* — [CLAUDE.md](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/CLAUDE.md)
- Root `Cargo.toml` defines a ~200-crate Cargo workspace (`resolver = "2"`, `members = [...]`) — [Cargo.toml](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/Cargo.toml)
- Official account: Jarred Sumner (Bun creator), ["Rewriting Bun in Rust"](https://bun.com/blog/bun-in-rust), Bun Blog. Per that post (cross-checked against [The Pragmatic Engineer's independent writeup](https://blog.pragmaticengineer.com/the-pulse-what-can-we-learn-from-buns-rapid-rust-rewrite-with-ai/), which cites no disagreements with it):
  - The rewrite happened **May 3–14, 2026** (11 days), porting **535,496 lines** across **1,448 `.zig` files → `.rs` files**, ~6,500 commits.
  - Motivation was **memory safety / stability**, not raw speed: recurring use-after-free, double-free, and leak bugs in `node:zlib`, `node:http2`, `UDPSocket`, the CSS parser, crypto, and file watchers. Zig, like C, does not enforce ownership; Rust's borrow checker turns those bug classes into compile errors.
  - It was done with heavy AI assistance (parallel Claude Code agents, ~64 concurrent, adversarial-review workflow), reported cost ~$165k in API usage.
  - **Explicitly preserved, unchanged in design**: JavaScriptCore, uWebSockets/uSockets (HTTP/WS layer), the event loop design, BoringSSL, SQLite, mimalloc. The stated philosophy was a near-mechanical "transpile Zig to Rust" port, not a redesign.
  - Reported perf delta from the rewrite itself was **small and mixed** — e.g. `Bun.serve` throughput +4.8%, `node:http` compat layer +4.5%, memory usage on repeated `Bun.build()` calls dropped substantially (attributed to `Drop`-based deterministic cleanup replacing manual `defer`, not to Rust being "faster than Zig" in general). Binary size fell ~20%.

**Why this matters for the article's thesis:** if a wholesale language rewrite (Zig → Rust, two different systems languages, same team, same optimization mindset) only moved HTTP throughput by low single digits, that's strong *evidence* — not just a rhetorical claim — that **the systems language is not where Bun's advantage over Node/Deno lives.** The advantage has to live in the things that were explicitly held constant across the rewrite: JSC as the engine, uWebSockets/uSockets as the socket/HTTP layer, mimalloc as the allocator, and the shape of the native-API bindings. That reframes RQ2 from the plan: language choice is a **minor factor at best (INFERENCE, pending Stage 2/8/9 verification)**; architecture-level choices are the leading candidates.

Caveat: the perf numbers above come from Bun's own blog post, not an independently reproduced benchmark — flagged for Stage 10 (benchmark audit) and Stage 13 (we should try to reproduce at least the HTTP throughput comparison ourselves).

Open question logged: was Bun already faster than Node/Deno in the *Zig* era (pre-May-2026), with roughly the same margins as today? If yes, that further confirms language-of-implementation is not the driver. Need to check historical benchmark posts from 2023–2025 in Stage 10.

---

## Source layout (Rust era), per `CLAUDE.md` + direct directory listing

- `src/bun_core/` — foundational `bun.*` namespace: string type, formatting, logging, feature flags, allocator helpers.
- `src/sys/` — cross-platform syscall wrappers (`file.rs`, `dir.rs`, `fd.rs`, `Error.rs`).
- `src/bun_bin/` — Cargo entrypoint; builds `libbun_rust.a`, statically linked into the final `bun` executable.
- `src/jsc/` — the JS↔native boundary. Two layers:
  - `src/jsc/bindings/*.cpp` — hand-written + **generated** C++ classes that bind into JavaScriptCore's C++ API directly (not a stable ABI like Node-API; this is JSC's actual embedder API).
  - `src/jsc/*.rs` — Rust-side JSC glue: `VirtualMachine.rs`, `event_loop.rs` (JSC-flavored event loop arm), `FFI.rs`, `JSObject.rs`, `host_fn.rs` (native functions callable from JS), `CallFrame.rs`, `array_buffer.rs`.
  - Binding code is largely **generated from `.classes.ts` declaration files** (see `server.classes.ts` under `runtime/server`), not written by hand per-API — i.e. Bun has its own IDL-like codegen pipeline for the JS↔Rust/C++ boundary. (Stage 5 topic.)
- `src/event_loop/` — the event loop abstraction (`AnyEventLoop.rs`, `MiniEventLoop.rs`, `SpawnSyncEventLoop.rs`, `EventLoopTimer.rs`). Uses a `link_interface!` macro (`bun_dispatch`) to define a `JsEventLoop` trait-like interface with methods `tick()`, `auto_tick()`, `uws_loop() -> *mut bun_uws::Loop`, `enqueue_task()`, etc. — confirms the event loop is driven by **`bun_uws::Loop`**, i.e. uSockets' own loop, not libuv, on the primary path.
- `src/uws/`, `src/uws_sys/` — Rust bindings around **uWebSockets/uSockets** (the C++ HTTP/WebSocket library originally written by Alex Hultman, also used inside some other high-performance servers). This is Bun's HTTP server and low-level socket layer. `libuwsockets.cpp` / `libuwsockets_h3.cpp` are the C++ glue; `_libusockets.h` exposes the uSockets C API. There's a `quic/` subfolder — HTTP/3 support wired in at this layer.
- `src/runtime/server/` — `Bun.serve()` itself: `RequestContext.rs`, `ServerWebSocket.rs`, `NodeHTTPResponse.rs`, `FileResponseStream.rs`, `StaticRoute.rs`. This is the layer that turns a uWebSockets request into a JS `Request`/`Response` object and back.
- `src/runtime/webcore/` — spec-following Web APIs: `fetch.rs`, `streams.rs`, `Blob.rs`, `Response.rs`, `Request.rs`.
- `src/runtime/node/` — Node.js compatibility layer (fs, path, process, Buffer, etc.) implemented in Rust rather than by loading Node's own polyfill-heavy JS shims.
- `src/runtime/crypto/` — WebCrypto + `node:crypto`, backed by **BoringSSL** (vendored), not a pure-JS or Node-style OpenSSL binding path — `EVP.rs`, `HMAC.rs`, `CryptoHasher.rs`.
- `src/http/` — outbound HTTP client + `websocket_client/`.
- `src/bun_alloc/` — allocator layer: `MimallocArena.rs`, `basic.rs`, `ast_alloc.rs`, `stack_fallback.rs`, `baby_vec.rs`, `heap_breakdown.rs`. Confirms **mimalloc**, arena allocation for the AST/parser, and small-vector-style optimizations (`baby_vec`) as deliberate, code-visible performance techniques (Stage 9 topic).
- `src/js_parser/`, `src/js_printer/`, `src/transpiler/`, `src/bundler/` — the always-on JS/TS/JSX transpiler and bundler (Stage 7 topic — Bun transpiles *every* file it runs, even plain `.js`, through this pipeline).
- `src/install/` — the package manager: `lockfile/`, `npm.rs`, `lifecycle_script_runner.rs` (Stage 7 topic).
- Vendored C/C++ deps (per `CLAUDE.md`, checked into `vendor/`, not git submodules): **BoringSSL, brotli, c-ares (async DNS), highway (SIMD), libarchive, libdeflate, libuv (Windows-only event loop), lolhtml, lshpack (HTTP/2 HPACK), lsquic (HTTP/3), mimalloc, picohttpparser, tinycc (a forked TinyCC used as a JIT for `bun:ffi`), WebKit (JavaScriptCore), zlib-ng, zstd.**

That vendor list is itself informative: **libuv is present but scoped to Windows only** — on Linux/macOS the event loop is uSockets' own epoll/kqueue-based loop, not libuv. This is a real architectural divergence from Node (which uses libuv everywhere) worth digging into in Stage 4.

---

## Draft text architecture diagram (to refine in later stages)

```
                          JavaScript / TypeScript source
                                     │
                     ┌───────────────────────────────┐
                     │  src/js_parser + transpiler    │  always-on transform,
                     │  (Rust)                        │  even for plain .js
                     └───────────────┬────────────────┘
                                     │ bytecode-ready JS
                     ┌───────────────────────────────┐
                     │      JavaScriptCore (C++)      │  LLInt → Baseline JIT
                     │      vendored from WebKit       │  → DFG → FTL, GC
                     └───────────────┬────────────────┘
                                     │ JSC C++ embedder API
                     ┌───────────────────────────────┐
                     │   src/jsc/bindings (C++)        │  generated + hand-written
                     │   src/jsc/*.rs  (Rust glue)      │  host functions, classes
                     └───────────────┬────────────────┘
                                     │
        ┌────────────────────────────┼─────────────────────────────┐
        │                            │                              │
┌───────────────┐          ┌───────────────────┐          ┌──────────────────┐
│ src/runtime/   │          │ src/event_loop/    │          │ src/runtime/node/ │
│ webcore (fetch,│◄────────►│ (AnyEventLoop,      │◄────────►│ node compat        │
│ streams, Blob) │          │ ticks bun_uws::Loop)│          │ (fs, process, ...) │
└───────┬────────┘          └─────────┬──────────┘          └──────────┬────────┘
        │                             │                                 │
┌───────────────┐          ┌───────────────────┐          ┌──────────────────┐
│ src/http/      │          │ src/uws / uws_sys   │          │ src/sys/ (syscall  │
│ outbound HTTP  │◄────────►│ uWebSockets/uSockets │◄────────►│ wrappers), src/bun_│
│ + WS client    │          │ event loop, sockets, │          │ alloc (mimalloc)   │
└────────────────┘          │ HTTP/1/2/3 parsing   │          └──────────────────┘
                             └─────────┬──────────┘
                                     │ epoll / kqueue (POSIX) or libuv (Windows only)
                                     ▼
                                  OS kernel
```

This still needs Stage 4 (event loop tracing) and Stage 8 (HTTP path) to fill in syscall-level detail — treat as a skeleton, not final.

---

## What we know

- Bun's runtime, as of the current `main` branch (v1.4.0, Aug 2026), is implemented primarily in **Rust + C++**, not Zig. This is a very recent (May 2026) full rewrite. **FACT**, verified directly from source + corroborated by two independent accounts.
- JavaScriptCore, uWebSockets/uSockets, mimalloc, BoringSSL, and the event loop design were explicitly preserved across that rewrite. **FACT** (stated in the primary source blog post; consistent with what the current source tree shows).
- The event loop on POSIX is driven by uSockets' own loop (`bun_uws::Loop`), not libuv; libuv is vendored for Windows only. **FACT**, from `event_loop/lib.rs` and the vendor list in `CLAUDE.md`.
- The JS↔native boundary is JSC's own C++ embedder API, with a custom codegen layer (`.classes.ts` → generated bindings) rather than a stable-ABI approach like Node-API. **FACT** (directory structure + `CLAUDE.md`), depth TBD in Stage 5.
- Bun transpiles every file (including plain `.js`) through its own Rust-based parser/printer before execution. **FACT** (directory presence + widely documented Bun behavior), performance implication TBD in Stage 7.

## What we don't know yet

- Whether the *magnitude* of Bun's speed advantage over Node/Deno changed meaningfully across the Zig→Rust rewrite (would be strong evidence either way). Need historical benchmark data (Stage 10).
- The actual generated-binding mechanism in detail — how a `.classes.ts` file becomes a JSC-callable Rust/C++ function, and how many JS↔native crossings a typical API call costs relative to Node's N-API or Deno's ops (Stage 5).
- Precisely how the uSockets loop multiplexes timers, sockets, and filesystem callbacks together with JSC's microtask queue (Stage 4).
- Whether `unsafe` Rust usage (reported ~4% of the codebase, concentrated at FFI boundaries per the blog post) reintroduces any of the memory-safety-adjacent perf/safety trade-offs Zig had — worth a skeptical look, not taking the blog's self-report at face value.

## Evidence quality note

Primary: direct repo clone + read (highest confidence). Secondary: Bun's own blog post for historical/process claims we can't verify from a single commit snapshot (timeline, LOC counts, cost) — corroborated by one independent source (Pragmatic Engineer), not yet cross-checked against a third. Treat the *numbers* (695 commits/hour, $165k, etc.) as **FACT-per-primary-source-self-report**, not independently audited fact, and say so in the article.

## Next

Proceed to Stage 2 (JSC vs V8 — what in JS execution actually matters) and Stage 5 (FFI/binding mechanism, since it's clearly central to this codebase's design) in parallel with Stage 4 (event loop tracing). Will report back after those.
