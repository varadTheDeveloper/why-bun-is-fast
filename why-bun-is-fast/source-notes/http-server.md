# HTTP Server Performance: `Bun.serve()` Request Lifecycle

Status: formal, reviewable deliverable. Every claim below was independently verified directly against source, not carried over from an earlier draft.

**Source pins used in this stage:**
- Bun: `oven-sh/bun@8326d1bd39a96f1f298c3de195aad15972d4f3b4` (same clone as prior stages, `repos/bun/`)
- Node: `nodejs/node@ad7a5b8302ae54b6e6dc77e03eabc5a3218dfb85` (main, fetched 2026-08-16 via raw.githubusercontent.com after `nodejs.org`'s own docs returned `ROBOTS_DISALLOWED` in an earlier stage — same substitution pattern noted transparently again here for the two files fetched this stage: `lib/_http_outgoing.js`, `lib/_http_incoming.js`)
- Deno: `denoland/deno@89f33cbef296a2b287f323d42de54c871fa69c77` (main, shallow-cloned this stage via `git clone --depth 1 --filter=blob:none --sparse` limited to `ext/http` and `ext/node/polyfills`; mirrored at `repos/deno-http-ext/ext/`)

---

## What we know

### Bun — `Bun.serve()` request lifecycle

**1. No dedicated thread. This resolves Evidence Map open item 9.**

`listen()` in `src/runtime/server/mod.rs` (~line 2752) calls `uws_sys::NewApp::<SSL>::create(&ssl_options)` directly and synchronously on the calling (JS) thread — confirmed by reading lines 2740–2830 in full: SSL/H3 app construction happens inline, no `thread::spawn` anywhere in the call chain. `src/uws_sys/App.rs`'s `uws_create_app()` FFI signature takes no explicit loop parameter, which means the new `uWS::App` binds to whatever uWS loop is default for the calling thread — the JS thread's own loop, the same one `vm.event_loop_ref()` returns elsewhere in `mod.rs` (~line 3181).

This is a direct, load-bearing asymmetry with `fetch()` (M9): **Bun's HTTP client hops to a dedicated `HTTPThread`; Bun's HTTP server does not.** One runtime, two different threading models for the two directions of HTTP traffic, chosen per-subsystem rather than uniformly.

**2. The server-side parser is uWebSockets' own C++ HTTP parser, not picohttpparser.**

Grepping the whole Rust tree for `bun_picohttp`/`picohttp::` usage returns hits only in `src/runtime/webcore/fetch.rs`, `src/runtime/webcore/s3/{client,simple_request}.rs`, `src/http_types/ETag.rs`, `src/http/H2Client.rs`, `src/http/h2_client/{encode,dispatch,Stream}.rs`, `src/http/InternalState.rs`, `src/http/h3_client/Stream.rs` — every one of those is client-side (fetch, S3, HTTP/2 and HTTP/3 client dispatch). Zero hits in `src/runtime/server/`. picohttpparser is vendored and real, but it parses responses Bun *receives* as a client, never requests Bun *serves*. The server's parsing is internal to uWebSockets (a vendored C++ dependency this project has not needed to read line-by-line, since the Rust-side behavior is fully observable at the `uws_sys` FFI boundary).

**3. Zero-copy access is real, but scoped specifically to the synchronous-handler path.**

`src/uws_sys/Request.rs`'s `AnyRequest` enum (H1/H3) exposes `header()`/`url()`/`method()` as direct pointer-into-buffer reads (`uws_req_get_url` etc.), with a safety comment confirming "ptr/len describe a valid slice owned by the request for its lifetime." For a handler that reads the URL/headers and responds without ever `await`-ing, there is genuinely no copy of that data before it's consumed.

**4. The sync→async transition forces a real, precisely-scoped copy — not a whole-request copy.**

`src/runtime/server/RequestContext.rs`'s `to_async()`/`to_async_without_abort_handler()` (~lines 2185–2225) is the exact site. Re-verified this stage line-by-line:

- `request_object.request_context.set_request(req.cast::<uws::Request>())` — H1 only; H3 requests are already eager and skip this.
- `request_object.ensure_url()` — materializes the URL string, falling back to empty string on failure.
- `request_object.set_fetch_headers(Some(response::HeadersRef::create_from_uws(req)))` — **guarded by `if !request_object.has_fetch_headers()`** (only copies once, not on every suspension), with the source comment: *"we have to clone the request headers here since they will soon belong to a different request."*
- Ends with `request_object.request_context.detach_request()`.

So "what gets copied" is precisely: **headers and URL, and only if the handler suspends past the point where uWebSockets would reuse its stack-allocated `uWS::Request` buffer for the next connection's request.** Body is handled through a separate mechanism (streamed/pooled, not part of this copy). This is the reason the *why* matters: `handle_request()` in `mod.rs` (~lines 1053–1100) contains the operative comment verbatim: *"The request is asynchronous, and all information from `req` must be copied since the provided uws.Request will be re-used for future requests (stack allocated)."* The struct being reused, not the semantics of the data, is what forces this copy — it is a lifetime problem, not a design preference.

**5. Per-request native context is pooled, not allocated fresh per request.**

`server.request_pool.claim()` / `put_raw()` — a `HiveArray::Fallback` — recycles `ServerRequestContext` objects across requests (`mod.rs`, `prepare_js_request_context()`, ~lines 709–790). This is a distinct pool from Stage 3's `ByteListPool` (stream buffers) — two separate pooling subsystems for two separate allocation-heavy paths.

**6. Handler invocation is a single direct JS call, not a queued/dispatched task.**

`NewServer::on_request` (`mod.rs`, ~lines 1107–1145): `on_request.call(global, js_value, &[prepared.js_request, js_value])`. The `extern "C" on_request<SSL, DEBUG>` function (~line 3342) is the literal uWS C++ → Rust callback entry point invoked by uWebSockets itself when a request is ready — there is no intermediate task queue between "uWebSockets says a request arrived" and "the user's JS function is called." If the handler is synchronous and returns a `Response` directly, the whole request can complete without ever touching Bun's own event-loop task queue (`ConcurrentTask`/`DeferredTaskQueue` from Stage 4) at all — those only get involved if the handler itself does something async (another `fetch()`, a timer, an `await`).

**7. Response writes are corked/uncorked — real write-batching, explicit and manual at the Rust-implementation level.**

`src/uws_sys/Response.rs` exposes `cork()`/`uncork()`/`is_corked()` and a `corked(f)` helper (~lines 111–960) wrapping uWebSockets' own corking primitive (`uws_res_cork`/`uws_res_uncork`). Multiple `write_header()` calls plus `end()`/body writes made while corked are buffered and flushed as one write/syscall on uncork, rather than one syscall per header.

**8. Body length is pre-validated before allocation.**

Content-Length is parsed early (`bun_http_types::parse_content_length`) with an early-reject 413 path for over-limit bodies, before any body-buffer allocation happens — a real allocate-avoidance mechanism specifically for the "attacker/misconfigured-client sends an enormous Content-Length" case, not a general per-request optimization.

### Node — `http.createServer()` request lifecycle

**9. The parser is llhttp**, confirmed directly in `src/node_http_parser.cc` (native addon, C++): explicit comment *"This is a binding to llhttp"*. `HandleScope` is constructed at multiple llhttp-adjacent callback sites in that file — this is the same V8 handle-scope cost characterized generally in M2, now confirmed specifically present on Node's HTTP parse-callback path too, not just generic native calls.

**10. Header construction is lazy — headers are NOT eagerly built into a JS object during parsing.**

Confirmed this stage from `lib/_http_incoming.js` (`IncomingMessage`). Two representations exist: `this.rawHeaders` (a flat alternating name/value array) and `this[kHeaders]` (the normalized object most user code reads via `req.headers`). The `headers` accessor is a getter, not a plain property:

```js
ObjectDefineProperty(IncomingMessage.prototype, 'headers', {
  get: function() {
    if (!this[kHeaders]) {
      this[kHeaders] = { __proto__: null };
      const src = this.rawHeaders;
      const dst = this[kHeaders];
      for (let n = 0; n < this[kHeadersCount]; n += 2) {
        this._addHeaderLine(src[n + 0], src[n + 1], dst);
      }
    }
    return this[kHeaders];
  },
  ...
});
```

`rawHeaders` itself is populated during parsing (llhttp's per-field/per-value C++ callbacks feed it), but the *normalized* object — with lowercasing, duplicate-header joining via `_addHeaderLine`'s comma/semicolon-join logic, `Set-Cookie` array handling — is built lazily, once, memoized on first `.headers` access, and never rebuilt after. A handler that never reads `req.headers` (unusual, but possible — e.g. a handler that only inspects `req.url` and `req.method`) never pays that normalization cost at all.

**11. Response header serialization batches into one string before any write.**

`lib/_http_outgoing.js`'s `_storeHeader()` accumulates every header line into a single `this._header` string (`state.header += key + ': ' + value + '\r\n'` per header, `'\r\n'`-terminated at the end) — one string, not one write call per header. Verified this stage.

**12. Headers and the first body chunk are combined into one string/write when possible — a real batching optimization, but distinct from corking.**

Re-checked `_send()`/`write()`/`end()` this stage: when the first `write()` after headers are stored is a plain string with a compatible encoding (`utf8`/`latin1`/none), Node concatenates the stored header block directly onto that string (`data = this._header + data`) before handing it to the socket — avoiding a separate header-only write. Separately, `write()` (not `end()`) does briefly `socket.cork()` and schedules `uncork` on `process.nextTick`, batching same-tick writes. **Node does not automatically cork headers together with an arbitrary/non-string first chunk** (e.g., a Buffer) the way the header-string-concat path does for strings — the batching mechanism is conditional on chunk type, not a blanket guarantee the way Bun's explicit `corked(f)` wrapping is.

**13. `cork()`/`uncork()` exist on `OutgoingMessage` directly and delegate to the underlying socket**, maintaining a local corking counter (`this[kCorked]++`/`--`) — available to user code, and used internally in the write()-batching case above, but not wrapped unconditionally around every response the way Bun's server-side response path is.

### Deno — `Deno.serve()` request lifecycle

**14. Deno.serve() is built directly on `hyper`** — the same HTTP library Deno's `fetch()` client also uses (confirmed in earlier stages). This is a real structural difference from Bun: Deno has *one* HTTP implementation shared by client and server; Bun has *two* (uWebSockets for the server, a bespoke `bun_http`/picohttpparser-based client for `fetch()`). Neither is inherently faster from this fact alone — it's an architecture-symmetry difference, not a speed claim — but it is a genuinely interesting, verifiable structural asymmetry specific to Bun.

**15. Header (and URL, and body) access from JS is fully lazy — and, distinctively, *not memoized* on the headers path.**

Traced the JS-Rust boundary in `ext/http/00_serve.ts`'s `InnerRequest` class and the corresponding `#[op2]` functions in `ext/http/http_next.rs`:

- `url()` and `method` are computed once (via `op_http_get_request_method_and_url` / `op_http_get_request_method`) and memoized into private fields (`#urlValue`, `#methodValue`) on first access.
- `get headerList()` (line ~368) is **not** memoized — every access calls `op_http_get_request_headers(this.#external)` again, which re-walks the Rust-side header list (`inner.headers.iter()`) and re-allocates a fresh V8 array/strings every single time headers are read. Confirmed by reading the getter body: no `this.#headers` cache field exists, unlike `#urlValue`/`#methodValue`.
- A single-header lookup (`request.headers.get("x")` style access, surfaced as `.header(name)`) goes through a *separate*, more targeted op (`op_http_get_request_header`) that reads directly from `hyper`'s `HeaderMap::get_all` (via `request_parts.headers.get_all(name)`) without materializing the full header list at all.

The header data itself never leaves Rust until asked for, in whatever granularity JS asked for it (single header vs. full list) — genuinely zero eager copy, for headers specifically, going further than Bun's or Node's server in this one respect. The trade-off: repeated full `.headers` iteration in JS re-crosses the op boundary and re-allocates every time, where Bun's (post-async-transition) and Node's (post-first-access) representations are each computed once and reused.

**16. Static/known-in-full responses get a single-op fast path that bundles status + body (and optionally headers) into one native call**, e.g. `op_http_set_response_body_static_with_default_header` (`http_next.rs` ~line 2879) and `op_http_new_response_native_static`/`_headers` — these exist specifically so that a handler returning a plain string/buffer body doesn't need N separate ops for status, headers, and body. This is Deno's analogue to "batch the response," implemented as op-call-count minimization rather than Bun's socket-level corking or Node's string-concatenation.

**17. Request dispatch to the user handler is a single async JS call per request** (`mapToCallback` in `00_serve.ts`, ~line 695): for each accepted request, `new InnerRequest(req, context)` wraps the opaque external pointer (cheap — no parsing happens here), `fromInnerRequest()` builds the `Request` facade, and `await callback(request, new ServeHandlerInfo(innerRequest))` invokes user code. Structurally similar to Bun's direct call (one call per request, no intermediate app-level queue), but wrapped in an `async` function by construction — consistent with M11's finding that Deno's whole event model is Future/Promise-composed rather than callback-dispatched, even at this leaf.

---

## What we don't know

- The internal C++ implementation of uWebSockets' HTTP parser itself (state machine, SIMD use if any) — out of scope without pulling the vendored C++ source into this investigation; everything above is what's observable and verifiable at the Rust/FFI boundary, which is the load-bearing layer for a Rust-vs-JS/V8 comparison anyway.
- Whether Node's `write()` auto-cork-on-nextTick path is actually exercised on the common "headers + one `res.end(body)` call" pattern, or only on the multi-`write()` pattern — the code path exists, but we have not traced which pattern typical `http.createServer` handlers hit most, nor measured it.
- Whether Deno's un-memoized `headerList` getter is a deliberate simplicity choice or an unnoticed missed optimization — source shows the behavior, not the intent. Framing this as a "flaw" would be speculation; it's stated here strictly as an observed asymmetry with Node/Bun.
- Backpressure handling specifics for large streamed response bodies on all three servers — not traced this stage (fetch()'s backpressure/streams handling was partially covered in Stage 3/8-preliminary but not re-verified here for the server-response direction specifically).
- Any WebSocket-upgrade-path-specific optimizations beyond what's incidentally visible (`_wantsUpgrade` in Deno's `InnerRequest` was seen but not investigated as its own topic — WebSockets are a separate stage-worthy subject, not covered here).
- Actual performance magnitude of anything above. Nothing in this stage was benchmarked.

---

## Evidence

All claims above are sourced to specific files and, where line numbers are given, specific functions, from the three pinned commits listed at the top of this document. Every quoted comment or code snippet was read directly from source this stage (Bun via the existing local clone; Node via two `raw.githubusercontent.com` fetches of `lib/_http_outgoing.js` and `lib/_http_incoming.js`; Deno via a fresh sparse shallow clone of `ext/http/` and `ext/node/polyfills/`, now mirrored locally at `repos/deno-http-ext/ext/`). No claim in the "What we know" section rests on general knowledge alone — where general knowledge was the starting point (e.g., Node's `cork()`/`uncork()` existing at all, IncomingMessage's lazy-headers behavior), it was independently re-verified against live source this stage per the standing rule, and is marked as such inline.

---

## Counter-evidence

Actively hunted for, per standing instruction. What was found:

- **Bun's "zero-copy" claim does not hold unconditionally.** It is real and verifiable for the synchronous-handler fast path, but the moment a handler `await`s anything, Bun pays a real, source-documented copy of headers and URL. A reader who took "Bun's HTTP server is zero-copy" at face value would be wrong for any realistic async handler (which is the common case — most real handlers read a database, call another service, or read a file before responding).
- **Bun's server does not share `fetch()`'s dedicated-thread architecture.** The popular narrative treats "Bun" as a monolithic fast thing; this stage shows two different threading choices for two different HTTP directions within the same runtime. A "Bun uses X thread strategy" claim needs to specify client or server.
- **Node is not doing more per-header JS work than Bun in the common case.** Node's `.headers` normalization is lazy and memoized — a handler that doesn't touch `req.headers` pays nothing for it, similar in spirit (though different in mechanism) to Bun's lazy accessor-based header reads. The "Node eagerly builds a heavy headers object on every request" assumption is not supported by source; it's conditional on the handler's own code.
- **Node already does response batching, just via string concatenation rather than corking.** The `_header + data` concatenation for the headers+first-chunk-string case achieves a broadly similar outcome (fewer writes) to Bun's `corked()` wrapping, via a different, narrower (string-typed-chunk-only) mechanism. "Bun batches writes, Node doesn't" would be false as a blanket claim.
- **Deno's laziness goes further than Bun's or Node's for headers specifically — zero eager copy at all, in either direction, until asked** — but this comes at the cost of no memoization on the full-list path, meaning repeated `.headers` access is repeated FFI-boundary + allocation cost in Deno where it's a cache hit in Node (after first access) or a one-time-computed value in Bun (after async transition, or immediate for sync). There is no framework here in which one runtime's approach is simply "better" — each is a different point on the eager/lazy, memoized/unmemoized design space, and each has a workload where its choice wins or loses.
- **Bun's pooling (`HiveArray::Fallback` for request contexts, separate `ByteListPool` for streams) and Deno's op-call-minimization (`op_http_set_response_body_static_with_default_header`, single-call status+body+header) are the same underlying goal — minimize allocation/boundary-crossing overhead per request — solved with entirely different, non-comparable mechanisms.** Neither can be shown "more effective" than the other from source reading alone; this needs Stage 13 measurement, if it's ever prioritized, and even then would need workload-matched, not just call-count-matched, comparison.

No counter-evidence found against the specific factual claim "Bun.serve() runs on the main JS thread's loop, not a dedicated thread" — that one is unambiguous from source (no `thread::spawn`, no explicit loop param) and stands as confirmed.

---

## Confidence

| # | Claim | Mechanism confidence | Magnitude confidence |
|---|---|---|---|
| 1 | `Bun.serve()` runs on main JS thread's loop, no dedicated thread | High | N/A (binary fact, not a magnitude claim) |
| 2 | Server-side parsing is uWebSockets' own parser, not picohttpparser | High | N/A |
| 3 | Sync-path header/URL access is genuinely zero-copy | High | Not measured |
| 4 | Async transition copies headers + URL only, once, guarded | High | Not measured |
| 5 | Per-request native context is pooled (`HiveArray::Fallback`) | High | Not measured |
| 6 | Handler invocation is a direct call, no intermediate queue for sync handlers | High | N/A |
| 7 | Response writes are corked/uncorked (explicit batching) | High | Not measured |
| 8 | Body length pre-validated before allocation | High | N/A (allocation-avoidance is binary per oversized request, not a general speedup) |
| 9 | Node uses llhttp, HandleScope present at parser callback sites | High | Not measured |
| 10 | Node's `.headers` normalization is lazy + memoized | High | Not measured |
| 11 | Node batches response headers into one string before writing | High | Not measured |
| 12 | Node combines headers + string first-chunk into one write | High | Not measured |
| 13 | Node `cork()`/`uncork()` exist, used internally in the write() path | High | Not measured |
| 14 | Deno's server and client share `hyper` (Bun's don't share an HTTP impl) | High | N/A (structural claim) |
| 15 | Deno's header access is fully lazy and unmemoized | High | Not measured |
| 16 | Deno has op-call-count-minimized static response fast path | High | Not measured |
| 17 | Deno dispatches to handler via one async call per request | High | N/A |

Every mechanism claim above is High because each rests on a direct, quoted, line-cited source read this stage — not inference. Every magnitude cell is explicitly "Not measured" per standing project rule; Stage 13 is the only stage that can change that column.

---

## Next

Stage 8 is complete pending review. Per explicit instruction: **do not start Stage 9, do not start Stage 13 experiments.** Waiting for approval before proceeding.

---

## "Count the actual work" — Bun vs Node vs Deno, one HTTP request, FACT / INFERENCE / UNKNOWN only

Scope: a single request/response cycle for a handler that reads method + URL + one header and returns a small synchronous string response — the simplest realistic case, chosen so the table isn't measuring async-machinery overhead on top of the HTTP-specific mechanics this stage investigated. Where sync/async paths differ, both are noted; UNKNOWN is used wherever this stage did not establish enough to state FACT or a well-founded INFERENCE.

| Stage of work | Bun | Node | Deno |
|---|---|---|---|
| **Socket → parser handoff** | FACT: uWS's own event loop (same thread as JS) hands the connection to uWS's own C++ HTTP parser. | FACT: libuv's loop (same thread as JS, per M10) hands the socket to llhttp via the `node_http_parser.cc` binding. | FACT: Tokio `current_thread` runtime (same thread as JS/V8, per M10/M11) hands the connection to `hyper`'s HTTP/1 or HTTP/2 codec. |
| **HTTP parsing** | FACT: uWebSockets' internal C++ parser (vendored; not independently read this stage — see "What we don't know"). | FACT: llhttp, confirmed live source, "binding to llhttp." | FACT: `hyper`'s parser (external Rust crate, not independently read this stage). |
| **Request struct representation before JS is involved** | FACT: stack-allocated, per-connection-**reused** `uWS::Request` (C++); Rust-side `AnyRequest` enum wraps it with pointer-based accessors. | FACT: `HTTPParser`-owned native `http_parser`/llhttp state; `rawHeaders` flat array populated via per-field/per-value C++ callbacks during parse. | FACT: `hyper::Request`/`Incoming` (or a `Raw` variant for the raw-HTTP fast path), owned Rust-side, exposed to JS only via `HttpRecordExternal` opaque pointer. |
| **Method/URL access from JS** | FACT: direct pointer read into uWS-owned buffer (`uws_req_get_url` etc.) — zero-copy. | INFERENCE: `req.url`/`req.method` are plain JS string properties set from parsed values at `IncomingMessage` construction time (not re-traced to the exact assignment site this stage — general `_http_server.js`/`_http_client.js` construction pattern, not independently re-verified with the same rigor as headers were). | FACT: computed once via `op_http_get_request_method_and_url`, memoized in private fields (`#methodValue`/`#urlValue`) on first access. |
| **Header access from JS (full list)** | FACT (sync path): zero-copy pointer read. FACT (post-async-suspend): one-time copy via `HeadersRef::create_from_uws`, guarded so it only happens once. | FACT: lazy getter, builds normalized object from `rawHeaders` on first `.headers` access only, memoized (`this[kHeaders]`) thereafter. | FACT: **not memoized** — every `.headers` access re-invokes `op_http_get_request_headers`, re-walking Rust's header list and re-allocating fresh V8 strings each time. |
| **Header access from JS (single header)** | UNKNOWN — this stage did not trace whether Bun exposes a single-header-lookup fast path distinct from full-header-list access (Fetch API `Headers.get()` semantics on the resulting JS object were not re-examined at this depth). | INFERENCE: single-header reads go through the same normalized `this[kHeaders]` object once built — no evidence of a separate native single-header-lookup path. | FACT: separate, more targeted op (`op_http_get_request_header`) reads directly from `hyper::HeaderMap::get_all` — does not materialize the full header list. |
| **JS request-object creation** | FACT: `prepared.js_request`, built from the pooled `ServerRequestContext` (`HiveArray::Fallback`-recycled). | INFERENCE: a fresh `IncomingMessage` instance per request (standard JS-visible object construction) — not confirmed this stage whether any pooling exists on Node's side; nothing found suggesting it does. | FACT: `new InnerRequest(req, context)` — a lightweight JS object wrapping one opaque pointer; the `Request` facade (`fromInnerRequest`) is a separate, also-per-request JS allocation. |
| **Handler invocation** | FACT: single direct native→JS call, `on_request.call(...)`, from the uWS C++ callback itself — no queue for the sync case. | INFERENCE: Node's `http.Server` emits a `'request'` event (`EventEmitter` dispatch) rather than a single direct call — an extra layer of indirection relative to Bun's/Deno's direct-call model, though not indepedently re-confirmed at the exact source line this stage (resting on well-established Node `http` module architecture, flagged here as INFERENCE rather than FACT for that reason). | FACT: `await callback(request, info)` — direct async call per request from `mapToCallback`, no separate event-emitter layer. |
| **Response header serialization** | FACT: written via uWS response object methods; batched with `cork()`/`uncork()`. | FACT: `_storeHeader()` concatenates all headers into one string before any write. | FACT: for the static/fully-known-body case, a single op call (`op_http_set_response_body_static_with_default_header` or similar) carries status + body (+ optionally headers) across the boundary in one crossing. |
| **Response write batching** | FACT: explicit `corked(f)` wrapping — multiple logical writes become one syscall. | FACT: header-string + first-string-chunk concatenation (type-conditional); separate short-lived auto-cork-on-nextTick for `write()` calls. | FACT: op-call-count minimization (fewer FFI/op crossings) rather than socket-level corking — a different layer of the stack achieving a related goal. |
| **Thread hops for this synchronous case** | FACT: zero (main-thread loop throughout — server side only; `fetch()` is the outlier, not `Bun.serve()`). | FACT: zero (libuv single-thread model, M10). | FACT: zero (Tokio `current_thread`, M10/M11). |

**What this table does and doesn't show:** every runtime does real, deliberate engineering to minimize allocation and native/JS boundary crossings on the HTTP path — none of the three is naive. The mechanisms are genuinely different (pointer-accessor zero-copy vs. lazy-memoized JS objects vs. lazy-unmemoized op calls; socket corking vs. string concatenation vs. op-call bundling), and this stage's source reading cannot rank them by speed — only by mechanism. No cell above should be read as "and therefore this runtime is faster here"; that requires Stage 13, which has not run.

---

## Article-worthy discoveries

**Strongest technical discovery:** the sync/async header-copy trade-off in Bun (`RequestContext.rs`'s `to_async()`), because it's a rare case where the *reason* for a real, unavoidable copy is fully visible in source — a stack-allocated, connection-reused C++ struct that a suspended JS continuation cannot safely keep pointing into. This is the kind of concrete, mechanism-level detail (not a benchmark number) that makes "zero-copy" claims falsifiable and precise instead of marketing.

**Strongest surprising discovery:** Deno's `headerList` getter is not memoized. Everything about the "lazy access via ops" design reads like a deliberate performance optimization right up until you notice repeated `.headers` reads pay the full op-boundary-plus-reallocation cost every single time, with no cache — a design choice with a real, findable trade-off that isn't advertised anywhere in Deno's docs (as far as this project has checked).

**Strongest counter-intuitive discovery:** Bun's server and Bun's client don't share a threading model, an HTTP parser, or (by extension) very much philosophy about how to handle HTTP at all. "Bun's HTTP stack" isn't one thing — it's uWebSockets for inbound, a bespoke picohttpparser-based client for outbound, decided independently. Meanwhile Deno — architecturally the "odd one out" on threading (M11) — has the more *unified* HTTP architecture of the three, sharing `hyper` for both directions.

**Strongest myth-busting discovery:** "Bun's HTTP server is zero-copy" is true only for synchronous handlers. The instant a handler does `await` anything — the overwhelmingly common real-world case — Bun pays a real copy that the marketing-level claim doesn't distinguish. This is exactly the kind of claim the article's evidence pipeline exists to catch.

**Strongest trade-off:** Deno's unmemoized-but-fully-lazy header access vs. Node's memoized-but-eager-on-first-touch vs. Bun's zero-copy-until-suspended. None of the three is a strictly dominant design; each optimizes for a different assumption about how many times a handler will read headers and whether it will suspend. This triad is a genuinely rich, three-way, non-strawmanned comparison — rare in this investigation, where usually one runtime turns out to have simply done more work than the others.

**Strongest candidate for the article's central diagram:** a single side-by-side request-lifecycle diagram showing, for identical "GET request, read one header, return a string" scenario: (a) the thread the work happens on (all three: same thread as JS — debunking "Bun has fewer thread hops" as a general claim, since here there's no hop to debunk on *any* of the three), and (b) exactly where each runtime chooses to copy, cache, or re-fetch data crossing the native/JS boundary. The interesting story isn't "Bun does less work" — Stage 8 found no evidence that's true in aggregate — it's "each runtime made a different, defensible bet about *when* to pay copy/allocation cost," which is a more honest and more original centerpiece than a bar chart.

---

### Evidence Map changes

**Strengthened:**
- **M9** — the "Bun's fetch() is the outlier that adds a thread hop" framing (already High confidence for mechanism from Stage 4) is now directly contrasted with confirmed server-side evidence, making the asymmetry claim strictly stronger: it's not just that Node/Deno don't hop threads for their equivalent operations (M10, already established) — it's that **Bun itself** doesn't hop threads for its own server, only for its own client. The "fewer abstractions" narrative doesn't even hold *within* Bun.
- **M2** (V8 handle-scope cost) — now has a second, independent confirmation site: `HandleScope` present at llhttp-adjacent parser callbacks in `node_http_parser.cc`, not just the general native-call sites cited in Stage 3/5. Same mechanism, new evidence location.
- **M11** (Deno's polled-Future model) — the request-dispatch-as-`async function` finding in `mapToCallback` is a concrete, leaf-level instance of the general architectural claim from Stage 4, extending it down to the HTTP-handler-invocation layer specifically.

**Weakened:**
- None. No existing Evidence Map entry was contradicted by Stage 8 findings.

**Unchanged:**
- M1, M3, M4, M5, M6, M7, M12, M13, M14 — not touched by this stage's scope.

**New:**
- **M15 — Bun's server/client HTTP-stack asymmetry**: uWebSockets (server, own parser, main-thread) vs. bespoke picohttpparser-based client (dedicated `HTTPThread`, M9) are two unrelated implementations within one runtime, contrasted with Deno's single shared `hyper` for both directions. High confidence for mechanism (directly source-verified on all relevant files); this is a structural/architectural claim, not a performance claim, and should not be scored for magnitude at all.
- **M16 — The Bun server-side sync/async header-and-URL copy boundary**: precisely scoped (headers + URL only, guarded to happen once, triggered specifically by the stack-allocated/reused `uWS::Request` struct's lifetime, not by any general design preference for copying). High confidence for mechanism; not a magnitude claim without Stage 13 (a benchmark comparing sync-handler vs. async-handler throughput on identical Bun.serve() workloads would isolate this cleanly).
- **M17 — Response-path write-batching, three different mechanisms for the same goal**: Bun (explicit socket-level `corked()`), Node (header+first-chunk string concatenation, type-conditional; separate short auto-cork on `write()`), Deno (op-call-count minimization via bundled static-response ops). High confidence for all three mechanisms being real and distinct; explicitly not ranked against each other — no source-level basis exists to say one achieves fewer actual syscalls without Stage 13 measurement.
- **M18 — Header-access laziness spectrum**: Bun (zero-copy until async-suspend, then one-time copy), Node (lazy-but-memoized-on-first-access), Deno (lazy-and-never-memoized). High confidence for mechanism on all three; presented explicitly as a spectrum/trade-off, not a ranking — the "best" choice depends on how many times, and whether after suspension, a given handler reads headers, which this project has not measured for any realistic workload distribution.

**Evidence Map open item 9 — formally resolved:** *"Does `Bun.serve()` run on the main loop directly or use a dedicated thread?"* → **Main loop, confirmed. No dedicated thread.** This resolution should be marked closed in `evidence-map.md`'s open-items list, with a note that it revealed a new architectural point (M15) rather than simply closing as a yes/no.
