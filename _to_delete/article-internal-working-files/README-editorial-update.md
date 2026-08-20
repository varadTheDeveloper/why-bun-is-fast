# README Editorial Update

**File modified:** `README.md` (repository root)
**Type of change:** Editorial only — no experiment results, benchmark numbers, H1–H6 conclusions, counter-evidence, article content, raw data, or source files were touched.

## What changed

Six additions and one section rewrite, in order of appearance:

1. **New "## The short answer" section**, added immediately after the title/tagline/intro paragraph and thumbnail, before "## Research question." States the actual thesis plainly — "There is no single fastest JavaScript runtime" — using the wording provided in the brief.

2. **New "### Who wins where?" table**, immediately below it. Seven rows, using the exact content specified: Bun `bun:ffi` → Bun, Deno Fast API → Deno, same N-API addon → Node, Buffer < 4,096 B → Node, Buffer ≥ 4,096 B → Bun, plaintext HTTP → Deno, DB-backed HTTP → Bun with a smaller spread. Followed by the required disclaimer sentence ("These are results from specific controlled workloads, not general claims...") plus a short note explaining why H3 and H4 are deliberately excluded from this table (H3's result doesn't reduce to a single winner; H4 compares two code paths within Bun, not across runtimes).

3. **New benchmark-environment note**, as a blockquote immediately below the table, using the exact wording provided: shared 2-vCPU VM, release builds, internally consistent but not hardware-independent, different hardware can produce different absolute results.

4. **"## Main conclusion" rewritten to open with the workload/path-dependence framing** ("The strongest conclusion... is not that Bun is universally faster. It is that runtime performance is workload- and path-dependent"), followed by an added sentence clarifying that each mechanism found is real but conditional. The existing bullet list of dependency factors is preserved unchanged. The existing pointer sentence to the article's own "Who wins where?" table and §10/§11 was kept and extended with a link to this README's own new table.

5. **New "## Where Bun doesn't win" section**, added directly after "Main conclusion." Lists all six required counter-evidence points explicitly (not just linked): Node wins small buffers, Deno wins plaintext HTTP, Deno's Fast API beats `bun:ffi`, Bun's N-API compatibility layer loses to Node's native N-API, Deno has fewer major page faults (with H3's mixed-result nuance spelled out inline, not hidden), and H4's concurrency-dependent reversal. Links out to the article's full §11 for detail.

6. **New "## The database-backed workload (H6)" section**, walking through Workload A vs. B, the ranking inversion, the exact 1.224× → 1.130× (~8%) narrowing, the explicit comparison to larger convergence reported elsewhere, and an explicit disclaimer that this doesn't mean all real applications converge.

7. **New "## What this research does not claim" section**, with the eight required guardrail points: no universal-fastest claim, no single-mechanism explanation, no microbenchmark-to-application multiplier, and the four experiment-specific non-claims for H1/H2/H3/H4, plus one for H6.

## Why each change was made

Direct response to reader feedback that the article's actual conclusion — no single fastest runtime, performance is workload- and path-dependent — was easy to miss, and that a casual reader could walk away thinking "Bun is faster," which the research does not support. The README is often the first thing a visitor to the repository reads, before the article or any experiment folder, so it needed to state the real thesis and show the counter-evidence before a visitor starts exploring — exactly as requested.

## What was deliberately left unchanged

- "## Research question," "## What we investigated" (including the H1–H7 experiment list and links), "## Important limitation" (the existing, more detailed hardware-limitation paragraph), "## What we did," "## Reproducibility," "## Repository structure," "## A note on how this repository was prepared," and "## License" — all byte-for-byte identical to the previous README.
- The H7 deferred-status line and its link — unchanged, still clearly marked "deliberately deferred."
- All existing H1–H6 experiment links (`experiments/h1-native-call-boundary/` through `experiments/h6-realistic-io-convergence/`) — kept as the repository's existing relative links rather than switched to the absolute GitHub URLs given in the brief. Relative links are the convention already used throughout this README and the article, they resolve correctly both locally and on GitHub, and they point at the same folders the absolute URLs specify — so no link target changed, only the link format was left as-is for internal consistency.
- No number, run count, threshold, percentage, or conclusion anywhere in the file was altered from its previously-verified value. Every figure used in the new sections (4,096-byte threshold, "nearly 4× slower," 1.224× → 1.130× / ~8%, 10.5%) is copied from the article and experiment results already verified in prior audits, not recomputed or re-derived.
- The article, experiment folders, raw data, and all other source files — not touched at all in this pass.

## Verification results

1. **No benchmark numbers changed from the experiment files** — PASS. Every number in the new README content is copied from previously-verified article/experiment figures; nothing was recomputed.
2. **No H1–H6 conclusion was altered** — PASS. No experiment folder or its README was touched; the main README's new text describes existing conclusions, it doesn't restate them differently.
3. **Counter-evidence is visible** — PASS. All six required counter-evidence points are stated explicitly in the new "Where Bun doesn't win" section, not just linked.
4. **"There is no single fastest JavaScript runtime" appears near the top** — PASS. First sentence of "The short answer," the second section in the file.
5. **"Who wins where?" table is present** — PASS. Immediately below "The short answer."
6. **Shared 2-vCPU limitation is visible near the top** — PASS. Blockquote directly below the table, in addition to the existing detailed "Important limitation" section further down.
7. **H6's DB-backed workload is explained accurately** — PASS. Dedicated section with Workload A/B, ranking inversion, exact 1.224×/1.130×/~8% figures, and explicit non-claim about general convergence.
8. **No universal performance multiplier appears** — PASS. Checked directly for "3x/5x/10x faster," "Bun is the fastest," "Bun wins everything," "Bun destroys" — zero matches. The only multiplier used is the existing, scoped "nearly 4× slower" (H1's same-binary result), not a universal claim.
9. **H7 remains clearly marked DEFERRED / not executed** — PASS. Line unchanged.
10. **All experiment links still work** — PASS. All six links point at the same, unchanged experiment folder paths as before.
11. **No private filesystem paths are present** — PASS. Checked directly for `C:\xa\`, `/home/claude/`, `/root/`, `cowork` — zero matches.
12. **No raw/internal audit paths are exposed** — PASS. The new content links only to the article and to public experiment folders already part of the repository structure; no internal working file, draft, or audit document is referenced.

---

### PASS

Only `README.md` was modified. The article, experiments, raw data, figures, methodology, and source-notes are untouched. Not committed, not pushed, not published.
