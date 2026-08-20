*Methodology: every benchmark figure below comes from six controlled experiments (H1–H6) we designed, ran, and independently audited ourselves, on a shared 2-vCPU cloud sandbox using release builds of Bun, Node, and Deno — not dedicated hardware. That limitation applies to every number in this piece and is stated once, here, rather than re-qualified in every sentence. Every source-code claim below links to the exact file (and, where practical, line range) in [Bun's public GitHub repository](https://github.com/oven-sh/bun), read at commit [`8326d1b`](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/). Every benchmark claim is labeled with its originating experiment (H1–H6) so its sample size and result table can be identified precisely, even though the raw result files themselves are this project's own private data, not separately published. Every claim about someone else's benchmark or Bun's own marketing is linked and clearly marked as external — never presented as something we measured.*

---

## Everyone knows Bun is fast. The strange part is why.

Type `bun install`, or spin up a `Bun.serve()` server, and the reaction from a developer used to Node is usually some version of: *wait, that's it?* Bun has built a real reputation for speed. Ask why, though, and the answers get vague fast.

Bun claims it "runs up to 4x faster" than Node in places — a claim we're going to leave exactly where it is, as a claim, since it [traces to a single social-media post](https://bun.sh/blog/bun-v1.0) with no disclosed methodology. What's not in dispute: Bun was written in Zig, until [Bun's own team quietly rewrote most of it in Rust](https://bun.com/blog/bun-in-rust), while Bun's own reported performance changes from that rewrite were modest. It runs on JavaScriptCore, the engine inside Safari, instead of V8, the engine inside Chrome and Node. Its HTTP server is built on a C++ networking library called uWebSockets.

Those facts about what Bun *is* are all accurate. None of them, on its own, explains anything about why it's fast. So instead of asking Bun, we read the source code — Bun's, Node's, and Deno's, side by side — and then built six controlled experiments to test the specific mechanisms that source code implied should matter.

What we found broke the obvious story almost immediately. It kept breaking it, all the way through.

---

## The short answer

Here's the short version, before the six-experiment walkthrough: **there is no single fastest JavaScript runtime.**

Across six controlled experiments, performance depended on the workload and the specific implementation path a call took through it — not on which runtime's name was on the process. Bun won some paths outright. Node won others. Deno won others. And when a real external dependency — a database — became part of the request, the gap between all three got smaller.

So this isn't building toward proving Bun is universally faster. It's about tracing where the actual performance differences come from, on which paths they show up — and where they start to disappear.

*A quick methodology note, up front: all six experiments ran on the same shared 2-vCPU cloud VM, using release builds of Bun, Node, and Deno — not dedicated hardware. That keeps every comparison below internally consistent, but it means the absolute numbers here shouldn't be read as universal benchmarks. Different hardware, especially dedicated or bare-metal machines, can produce different absolute results. (Full methodology detail is in the note at the very top of this piece.)*

### Who wins where?

A quick look at how each of the six experiments actually landed:

| Test / workload | Winner | What it tells us |
|---|---|---|
| Native call via Bun's own binding (`bun:ffi`) | Bun | Bun's own binding path is genuinely fast |
| Native call via Deno's V8 Fast API | Deno | A well-built fast-call path can beat Bun's, too — dramatically |
| Same compiled N-API addon, loaded via Bun's compatibility layer vs. Node's native implementation | Node | Bun's N-API compatibility layer adds substantial overhead — same code, slower door |
| `fetch()` request, cold and keep-alive | Bun | An extra architectural cost (a dedicated thread hop) didn't stop Bun from winning here |
| Buffer allocation below 4,096 bytes | Node | Node's pool wins at small sizes |
| Buffer allocation at/above 4,096 bytes | Bun | The winner flips exactly at the measured pooling threshold |
| Plaintext HTTP (no I/O) | Deno | Raw HTTP throughput has no universal winner |
| Database-backed HTTP | Bun (spread narrows to ~1.13×, from ~1.22× on plaintext) | Adding real I/O shrinks — but doesn't erase — the gap between runtimes |

*Each row is one experiment's result under its specific tested conditions, not a general claim about the runtime. The full numbers, methodology, and caveats for every row are in the matching section below.*

---

## 1. The obvious answers are wrong

Start with the language. Bun wasn't just *inspired by* a Rust rewrite — in May 2026, [Bun's team replaced roughly 535,000 lines of Zig with Rust](https://bun.com/blog/bun-in-rust), across 1,448 files, in about eleven days. If implementation language were the main driver of Bun's speed, swapping the entire language of a production runtime should have moved performance dramatically. Bun's own reported numbers for the rewrite: `Bun.serve` throughput up about 4.8%. Barely a shrug.

Rust doesn't help distinguish Bun from Deno either — Deno's native layer has been Rust since it was created. And in all three runtimes, the objects your JavaScript code actually creates are still managed by a garbage collector inside the engine, not by Rust's ownership rules. Rust runs underneath the JS you write; it doesn't reach into it.

What about the engine itself — JavaScriptCore instead of V8? This is a real, structural difference, and we'll come back to it directly. For now: an independent, publicly run benchmark suite that scores JS engines head-to-head found JavaScriptCore placing *second or third*, not first, depending on the platform ([ahaoboy/js-engine-benchmark](https://github.com/ahaoboy/js-engine-benchmark)). If "JSC is just faster" were the whole story, that shouldn't happen. We're going to leave this one open — we found the sharpest answer to it later, in a place we didn't expect.

uWebSockets, the C++ library behind `Bun.serve()`? Real, and genuinely fast. But an independent benchmark found a *Node.js* binding to that same library outperforming every Bun-based framework tested, including Bun's own best ([SaltyAom/bun-http-framework-benchmark](https://github.com/SaltyAom/bun-http-framework-benchmark)). The library's speed is the library's — not Bun's alone.

"Fewer abstractions everywhere"? We'll show you, directly, a case where Bun adds an abstraction — a whole extra thread — that neither Node nor Deno bothers with for the same job. And a fast `bun install` says nothing about `Bun.serve()`'s request-handling speed; they're unrelated subsystems that happen to ship in the same binary.

None of this means Bun isn't fast. It means the popular explanations don't hold up under a direct look. So we stopped asking "what is Bun made of" and started asking a different question.

---

## 2. How Bun actually runs

The more useful question isn't *which language* or *which engine*. It's: **where does the work actually happen, and when?**

Every JavaScript runtime is a stack of layers:

```
JavaScript (your code)
        ↓
JavaScriptCore (Bun) / V8 (Node, Deno) — the engine that runs your JS
        ↓
The runtime itself (Bun / Node / Deno) — HTTP, file I/O, timers, native bindings
        ↓
Native subsystems — parsers, allocators, thread pools, sockets
        ↓
The operating system
```

A request, a `fetch()` call, or a `Buffer.alloc()` doesn't just "happen in JavaScript." It crosses these layers, and at every crossing, someone made a specific engineering decision: copy this data or don't, allocate fresh memory or reuse a pool, do this on the calling thread or hand it to another one. Those decisions — not the language on the tin — are what the rest of this piece is actually about.

---

## 3. Follow one request through Bun

Here's what that looks like concretely. When a request hits a `Bun.serve()` server, it travels roughly like this:

```
socket
   ↓
uWebSockets (C++ HTTP parser, owns the raw connection)
   ↓
Bun's request object (wraps uWebSockets' data for JavaScript)
   ↓
your JS handler runs
   ↓
sync or async? — a fork in the road
   ↓
response is written back
   ↓
socket
```

![Bun request lifecycle: socket → uWebSockets → Bun's request object (pooled via HiveArray) → JS handler → sync/async fork (headers+URL cloned on suspension) → response written back (corked writes) → socket](visuals/visual-6-request-lifecycle.png)
*Visual 6 — Bun request lifecycle. Verified structural elements only; no performance multipliers implied.*

A few things are worth knowing about this path before we start measuring it. uWebSockets owns a small, reused chunk of memory for each connection — reused, meaning the *next* request on that connection can overwrite it. If your handler responds immediately (synchronously), Bun can read straight out of that memory with no copying at all. If your handler does something asynchronous first — `await`s a database call, say — Bun can't guarantee that memory will still hold the right data by the time your code resumes. So it copies exactly two things before handing control back to your `await`: the request's headers, and its URL. Not the body. Not everything. Just those two, and only once.

Elsewhere on this same path, Bun keeps a reusable pool of internal objects for in-flight requests instead of allocating a fresh one for every single connection — and, tellingly, [a bug that once silently shrank that pool under load](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/collections/hive_array.rs#L9-L23) was found and fixed, which is itself decent evidence that pooling here is something engineers actually cared about, not incidental plumbing. And when Bun writes a response back to the socket, it can ["cork"](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/runtime/server/NodeHTTPResponse.rs#L2527) several writes together into one flush instead of making several small ones.

We want to be honest about something here: at this point, we know these mechanisms *exist*. We do not yet know how much any of them actually matters. That's the difference between reading source code and measuring it — and it's exactly what the rest of this piece tries to close.

---

## 4. The first big surprise: the binding path matters

The oldest, sharpest version of "why is Bun fast" points straight at the engine: JavaScriptCore versus V8. There's a real, specific theory behind this. V8 requires native code to track every JavaScript value it touches inside something called a "handle scope" — bookkeeping that exists so V8's garbage collector, which physically moves objects in memory, can find and update every reference to them. JavaScriptCore's collector doesn't move objects the same way, so in theory, a native function written for JSC doesn't need that bookkeeping at all.

It's a clean story. We wanted to actually test it.

The cleanest way to isolate "crossing from JavaScript into native code" is a single native function: take an integer, add one, return it, called ten million times in a loop. We ran this through every binding mechanism we could get our hands on: Bun's own native-call system (`bun:ffi`), Node's standard mechanism for loading compiled C addons (called N-API), and — this is the important part — the *exact same compiled N-API addon*, unmodified, loaded into both Node and Bun. Bun has its own compatibility layer that lets it load ordinary Node addons, which meant we could run identical machine code through both runtimes and see what happened.

This "same binary" trick is the whole point of the design. Any comparison that lets each runtime use its own preferred way of calling native code is really comparing two things at once — the engine, and the binding technology built on top of it — with no way to tell which one is doing the work. Compiling one addon once and loading it into both runtimes held the compiled native machine code fixed. It did *not* hold the binding path fixed: Node loaded it through its own native N-API implementation, while Bun loaded the identical binary through its own N-API compatibility layer — a separate piece of engineering built to emulate Node's interface, not Node's own code. So this design isolates one variable precisely (the code being called never changes) while leaving another free to vary (how each runtime actually gets a call into it) — which is exactly what makes the result below informative, rather than a clean, apples-to-apples JSC-vs-V8 test. We also included Deno, which uses a different mechanism entirely (V8's own "Fast API" for simple calls), labeled separately since it isn't the same kind of comparison at all.

![H1 native call boundary overhead by binding path: Bun bun:ffi 14.538ns, Node N-API 22.105ns, Bun N-API compatibility layer on the same binary 81.901ns, Deno Fast API 4.278ns, Deno ordinary path 23.984ns](visuals/visual-2-h1-binding-path.png)
*Visual 2 — H1 binding-path comparison, with comparison groups (primary / same-binary control / Deno) distinguished by color.*

| Path | Overhead per call |
|---|---|
| Bun, its own binding (`bun:ffi`) | 14.538 ns |
| Node, its own binding (N-API) | 22.105 ns |
| **Bun, running Node's exact N-API addon (via Bun's N-API compatibility layer)** | **81.901 ns** |
| Deno, V8's "fast" call path | 4.278 ns |
| Deno, V8's ordinary call path | 23.984 ns |

*(H1 — native call boundary experiment, 10 runs × 10,000,000 timed calls per combo.)*

Using its own preferred mechanism, Bun beats Node — consistent with the popular story. But run the *identical compiled code* through Bun's Node-compatibility layer instead, and Bun is nearly **four times slower** than Node running that same code through its own native implementation. Same machine code. Same operation. Opposite result, depending entirely on which door it walked through.

We want to be careful about what this does and doesn't show. It does not prove JavaScriptCore's garbage collector is what caused this — we didn't instrument the collector itself, and this experiment can't separate "engine design" from "how well this particular compatibility layer was built." It's entirely possible Bun's Node-compatibility layer is simply a less-optimized piece of code than its `bun:ffi` path, independent of anything about JSC versus V8 at all — we can't rule that out with this data, and we're not going to pretend we can.

What it does show, cleanly, is that **binding-path implementation materially affects the observed cost — dramatically in this benchmark.** Bun's real advantage, where it has one, isn't "JSC is faster." It's that Bun built a fast door for itself — and a much slower one for code that expects Node's door. If you'd only run the first comparison — each runtime using its own preferred mechanism — you'd have walked away with exactly the wrong lesson.

---

## 5. The second big surprise: the thread hop did not lose

Here's a mechanism that sounds like an obvious disadvantage. Most of the time, a JavaScript runtime does everything — running your code and watching the network — on one operating-system thread, using a mechanism like `epoll` (Linux) or `kqueue` (macOS) to get notified the instant a socket has data. Node works this way. Deno works this way. Bun's server does too.

Bun's `fetch()` doesn't. It [hands the request off to a second, dedicated thread](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/http/HTTPThread.rs#L1280) that runs its own event loop and keeps a cache of TLS connection setup, then signals back to the main thread — an extra hop, with its own message queue and its own wake-up call — when the response is ready. Nobody else pays this specific cost for this specific call.

We measured single `fetch()` requests, loopback, both a fresh connection every time ("cold") and a reused, persistent one ("keep-alive"). Before the real numbers, two things almost invalidated the results: an artifact from Nagle's algorithm (a decades-old TCP behavior that can silently add tens of milliseconds to small requests) turned out to be inflating latency until we explicitly disabled it on the test server, and Node needed far more warm-up requests than Bun or Deno before its numbers stabilized — both caught and fixed before any numbers we're about to show you were collected.

| | Bun | Deno | Node |
|---|---:|---:|---:|
| Cold connection | **400.6 μs** | 639.3 μs | 1,143.3 μs |
| Keep-alive | **155.1 μs** | 164.1 μs | 191.0 μs |

*(H2 — fetch thread-hop experiment; 10 runs/runtime, 100 timed cold samples and 100,000 timed keep-alive samples per runtime.)*

Bun — the one runtime paying for an extra thread hop on this path — was the fastest in both conditions.

The conclusion here isn't "thread hops are good." We can't isolate what the hop itself cost or saved; Bun's win could come entirely from other parts of its fetch path (like that TLS-context cache) outweighing the hop, not from the hop being free. The real lesson is narrower and more useful: **a visible architectural cost doesn't tell you the net outcome.** You have to measure the whole path, not reason about one piece of it in isolation.

---

## 6. The cleanest mechanism result: Buffer allocation

Of everything we tested, this is the one result that resolved completely — no ambiguity, no "we couldn't isolate X." It's also the best single illustration of the article's actual argument.

Node's `Buffer.allocUnsafe()` — a very common operation any time you're handling raw bytes, like a chunk of an HTTP body, a short string being encoded, or a piece of a file being streamed — doesn't always ask the operating system for fresh memory. Below a certain size, it hands you a slice of one large chunk it pre-allocated and keeps around, called a pool, and just moves an internal pointer forward. [Bun's equivalent has no such pool](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/jsc/bindings/JSBuffer.cpp#L249); every call asks for fresh memory from the engine's own machinery, every time, regardless of size. Two clean, opposite strategies for the exact same operation — which makes this the rare case where we could isolate one variable (allocation size) and expect the source code to predict the result in advance.

Before trusting any of this, we hit a small but telling snag: the specific size at which Node's pool stops applying is supposed to be documented in its source, and the version we had access to said 32 KB. We measured Node's actual, shipped binary directly instead of trusting that number — allocating a handful of buffers at different sizes and checking, in code, whether they came back sharing the same underlying memory — and found the real threshold on the actual release build was **4,096 bytes**. Source code tells you what a system was designed to do; it doesn't always match what's actually running in a given release. We trusted the measurement, not the comment.

![H5 Buffer allocation throughput vs. size, Bun and Node, log-log scale, showing the winner flip at the 4,096-byte pooling threshold](visuals/visual-3-h5-threshold.png)
*Visual 3 — H5 4KB threshold reversal. Below 4,096 B, Node's pool wins; at and above it, Bun wins.*

| Size | Bun (allocs/sec) | Node (allocs/sec) | Winner |
|---:|---:|---:|---|
| 16 B | 11,801,970 | 13,260,126 | Node |
| 64 B | 9,578,974 | 11,782,221 | Node |
| 256 B | 6,983,603 | 7,587,909 | Node |
| 1,024 B | 2,829,340 | 3,647,748 | Node |
| **4,096 B** | **2,524,779** | 835,888 | **Bun** |
| 16,384 B | 2,203,141 | 568,822 | **Bun** |

*(H5 — buffer allocation experiment, 10 runs × 1,000,000 allocations per size/runtime combo.)*

Below 4,096 bytes, Node's pool wins every time — by 8% to just over 22%. At exactly 4,096 bytes and above, where Node's pool stops applying and it has to allocate fresh memory just like Bun does, Bun wins by just over 200% to nearly 290%. The reversal happens exactly where the source code says it should.

The correct claim here is not "Bun's allocator is 3x faster." It's much narrower, and much more interesting: **whoever wins this depends entirely on how big your buffers are** — a single, measurable, source-explained line, not a property of either runtime in general. A workload dominated by small `Buffer.allocUnsafe()` allocations would be on Node's side of that line in this benchmark. Once allocations reach the non-pooled regime we tested, Bun wins. We did not test complete HTTP, file, or stream workloads here — only direct allocation throughput at fixed sizes.

---

## 7. Page faults are not the same as speed

This one is subtle, and it's the article's best correction to a certain kind of reasoning: *"we found a favorable internal number, so we must be faster."*

[Bun's own source code documents a deliberate design choice](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/bun_bin/lib.rs#L19-L30) aimed at reducing page faults at startup — moments where the operating system has to load a new chunk of the program's code into memory for the first time. The source places code the interpreter never touches on a normal `bun run` (installer logic, the bundler, error-report formatting) away from the hot startup path in the compiled binary, in an arrangement intended to reduce the number of chunks that need to be loaded before your script actually starts running.

We dropped the Linux page cache before each cold run and measured this with `perf`, Linux's own performance-counting tool, across 30 fresh cold starts per runtime.

![H3 page faults vs startup time: Bun has fewest total page faults but startup time is statistically tied with Deno](visuals/visual-4-h3-faults-vs-startup.png)
*Visual 4 — H3 page faults vs. startup time, shown as two separate panels deliberately, since combining them into one score would hide the finding.*

| | Bun | Node | Deno |
|---|---:|---:|---:|
| Total page faults | **1,562** | 2,498 | 2,619 |
| Major faults | 27 | 32 | **18** |
| Cold startup time | **49.1 ms** | 77.6 ms | 49.2 ms |

*(H3 — cold-start page-fault experiment, 30 fresh cold starts per runtime, page cache dropped before each.)*

Bun really does have far fewer total page faults than either competitor — a real, large, highly reproducible gap. And yet Bun's actual cold-start time was statistically indistinguishable from Deno's — 49.1 ms versus 49.2 ms — despite Deno having *more* total faults than Bun. Deno, in turn, had *fewer* of the more expensive "major" faults than Bun did.

We measured the resulting page-fault profile and the resulting startup time — we did not run a version of Bun with that specific code-placement layout switched off to isolate its individual contribution, so we can't say this experiment proves the layout technique itself is what causes Bun's fault-count advantage, only that the advantage exists. What we can say is the headline finding: **a real, honestly-measured internal metric that looks favorable does not automatically explain the number a user actually experiences.** Fewer page faults did not mean a faster start.

---

## 8. HTTP server: the "zero-copy" story has conditions

Back to the fork in the road from section 3: what actually happens, measured, when a handler suspends instead of responding immediately?

The first version of this test didn't work, and it's worth telling you why. Our first plan was to compare a synchronous handler against one that did `await Promise.resolve()` before responding — the simplest possible "async" handler. [Tracing through Bun's source](https://github.com/oven-sh/bun/blob/8326d1bd39a96f1f298c3de195aad15972d4f3b4/src/runtime/server/RequestContext.rs#L2207-L2220), we found this doesn't actually test what we thought: Bun fully processes any pending microtasks — which is exactly what an already-resolved promise creates — *before* it checks whether the handler is still waiting on something. By the time that check runs, the promise has already resolved, and Bun takes the same fast path a synchronous handler would. We were about to run an experiment that measured nothing. We rebuilt it using `await new Promise(resolve => setImmediate(resolve))` — a delay long enough to force a genuine suspension — and confirmed the difference behaviorally before collecting any real data.

With that fixed, we measured throughput for a fully synchronous handler against a genuinely-suspending one, at two different concurrency levels.

| | Sync | Async | Difference |
|---|---:|---:|---|
| Concurrency 1 | **19,289 req/s** | 17,455 req/s | sync **+10.5%** |
| Concurrency 50 | 69,969 req/s | **71,742 req/s** | async **+2.5%** |

*(H4 — sync/async handler experiment, 10 runs per combo, 4 combos, 40 runs total.)*

At low concurrency, the sync path really is faster, cleanly and repeatably. At the higher tested concurrency of 50, the difference doesn't just shrink. It flips: the async path came out very slightly *ahead*.

We're not going to claim "the header-and-URL copy costs Bun 10.5% throughput." We can't cleanly separate that specific copy's cost from the general cost of actually suspending and resuming a request — the measurement necessarily includes both. What we can say: **a real optimization, confirmed in the source code, produced a measurable effect at one concurrency level and vanished — even nominally reversed — at another.** The code didn't change between those two conditions. The concurrency did.

---

## 9. The big real-world test

Everything so far has isolated one narrow mechanism at a time — a single native call, a single buffer allocation, a single `fetch()`. That's useful for finding where overhead actually lives, but real applications don't run in isolation like that. They also spend time waiting on databases, networks, filesystems, and other systems outside the runtime's control. So we tested what happens once a database becomes part of the request path — and whether anything from sections 4 through 8 survives contact with a workload that isn't just measuring the runtime anymore.

We built two versions of the same server in Bun, Node, and Deno. Workload A: a plain "hello world" HTTP response, no I/O — the kind of benchmark most public runtime comparisons actually run. Workload B: the same server, except it now does a single indexed lookup against a real PostgreSQL database before responding — a deliberately light database call, deliberately using the same generic driver package on all three runtimes rather than each runtime's own fastest, most specialized client, to keep the database-client implementation as constant as possible across the three runtimes.

![H6 workload ranking inversion: Deno-Bun-Node on plaintext fully inverts to Bun-Node-Deno on the database-backed workload](visuals/visual-5-h6-ranking-inversion.png)
*Visual 5 — H6 workload ranking inversion, with actual throughput alongside the rank change.*

| | Plaintext (Workload A) | Database-backed (Workload B) |
|---|---:|---:|
| Bun | 55,434 req/s | **9,100 req/s** |
| Node | 47,398 req/s | 8,519 req/s |
| Deno | **58,012 req/s** | 8,053 req/s |

*(H6 — realistic I/O convergence experiment; concurrency 20, 10-second timed runs.)*

The interesting result here isn't simply which runtime won. It's that the ranking changed when the workload changed, and the gap between the runtimes got smaller in the process.

On plaintext, Deno was fastest, Bun second, Node third. Add one database call, and the order fully inverts: Bun fastest, Node second, Deno last. **No runtime won both workloads.** The overall spread between fastest and slowest also narrowed — from a 1.224x gap on plaintext to a 1.130x gap with the database call added, roughly an 8% reduction.

We want to be direct about something: some other, independently published benchmarks — [one](https://evertheylen.eu/p/node-vs-bun/), [another](https://hackernoon.com/myth-vs-reality-real-world-runtime-performance-of-nodejs-deno-and-bun) — have reported realistic workloads erasing the gap between runtimes almost entirely — heavier application logic, more validation, more processing per request than a single indexed lookup does. Our result did not reproduce that magnitude. An 8% narrowing is real, but it isn't "substantial convergence," and we're not going to describe it that way just because a bigger number would make a better story. We ran this on a shared 2-vCPU sandbox, with a genuinely light query — a single indexed lookup, deliberately chosen to be minimal — the same non-specialized database driver across all three runtimes, at a modest concurrency of 20, in shortened 10-second runs, on a machine also running the load generator itself. Not dedicated production hardware, and not necessarily the heaviest realistic application logic. Any one of those could be part of why our number is smaller than others'; we don't know which, and we're not going to guess in print.

The conclusion we can actually stand behind: **workload composition can change which runtime wins, and did here — completely** — but we can't tell you this experiment proves the gap disappears in general. That's a claim a different, larger experiment would have to make.

---

## 10. So what actually makes Bun fast?

Six experiments. Not one of them found a single dominant explanation. All six found the *same shape* of result.

![Summary diagram: six experiments, six different variables that flip which runtime wins](visuals/visual-1-not-one-thing.png)
*Visual 1 — Bun performance is not one thing.*

The binding path a native call takes materially affects its cost — the same compiled code was nearly 4x slower through one door than another, across two different runtimes. Buffer allocation strategy flips its winner at a precise, measurable byte threshold. Connection state — cold versus warm — changes the ranking of who's fastest at `fetch()`. Concurrency changes whether a real, source-verified copy shows up as a cost at all. Workload composition inverts which runtime wins an HTTP benchmark outright. Even the *metric* you choose to look at — page faults instead of wall-clock time — can point you at the wrong runtime.

That's not six unrelated findings. It's one pattern, six times: **the outcome depends on what the workload actually asks the runtime to do, and which specific implementation path handles that ask.**

So the honest answer to "what makes Bun fast" isn't a mechanism. It's a list of specific, real, source-verified choices — which binding mechanism to expose as the default, how (and whether) to pool allocations, how long a request object needs to live, which code gets physically placed where in the binary, whether a network call gets its own thread — each of which wins under some conditions and loses under others. Bun's reputation for speed is real. It's just not the reputation of one trick. It's the accumulated result of a lot of narrow, specific bets, some of which pay off more than others depending on what you're actually running.

---

## 11. Where Bun doesn't win

If this were a piece written to make Bun look good, this section wouldn't exist. It exists because it's the part of the evidence that makes the rest of it trustworthy.

Node beats Bun outright on small buffer allocations — every size we tested under 4 KB, by 8% to 22%. Deno beat Bun on plaintext HTTP throughput in our real-world test. Deno's fastest native-call path beat Bun's own preferred binding mechanism (`bun:ffi`) outright — 4.3 ns versus 14.5 ns per call. Deno had fewer of the expensive "major" page faults than Bun at cold start. Run the exact same compiled addon through Bun's own Node-compatibility layer, and it loses badly to Node running it through its own native implementation — nearly 4x slower. And Bun's one clearly-measured server-side optimization, the sync-handler advantage, reverses at the higher concurrency we tested.

None of this is a footnote we're including reluctantly. We went looking for it, specifically, because a runtime that never loses in your own testing usually means you didn't test hard enough.

---

## 12. What we still don't know

A few things this investigation deliberately leaves open rather than papering over.

We don't know the exact, isolated contribution of JavaScriptCore's garbage-collector design to the native-call gap in section 4 — only that a large gap exists and that binding-path implementation, not engine identity alone, explains its direction. We don't know how much of Bun's `fetch()` win in section 5 comes from the thread hop itself versus other parts of the same code path. We don't know how to cleanly separate the header/URL copy in section 8 from the general cost of suspending a request. We don't know whether Bun's binary-layout choice specifically — as opposed to some other startup difference — is what drives its page-fault advantage in section 7; we measured the outcome, not an ablated version of the technique. We didn't measure total allocation volume across the three runtimes, only allocation throughput for one operation. A comparison involving Node's shared thread pool for filesystem and DNS work remains incomplete on the Bun and Deno side. A planned seventh experiment, testing Bun's default-on startup cache against Node's opt-in equivalent, was deliberately not run — the two most closely related questions were already answered well enough by sections 7 and 9 to make it unlikely to change the conclusion. And every number in this piece comes from one shared, non-dedicated 2-vCPU machine; we don't know how any of it looks on real production hardware, or across a wider range of realistic workloads than the six we built.

---

## 13. Final answer

So — why is Bun fast?

Not because of one magic engine. Not because it started life in Zig, and not because it's now written in Rust. Not because uWebSockets is some universal secret only Bun knows about.

Bun is fast, where it's fast, because of a long list of specific, low-level choices: which door a native call walks through, how long a request object needs to stay alive, whether memory gets pooled or allocated fresh, which code sits where in the compiled binary, whether a network call gets handed to its own thread. Each of those is a real, traceable decision, and each one shows up in our numbers.

And every one of them, measured honestly, stopped winning somewhere. Change the size of the buffer, the concurrency of the server, the state of the connection, or which binding path a call takes, and the winner changes with it. Its binding choices matter. Its allocation strategy matters. Its HTTP request lifecycle matters. But every one of those advantages is conditional — that's the pattern this whole piece has been tracing, one experiment at a time.

That's not a hole in the explanation — it's the explanation. The most accurate answer was never going to be "Bun is the fastest JavaScript runtime." It's narrower than that, and more useful: Bun can be extremely fast on the paths it has optimized — but runtime performance, on this evidence, is a property of the workload and the implementation path, not a single universal number. Once you've actually followed the code and measured what it does, that's the clearest explanation we've found that survives contact with the evidence.
