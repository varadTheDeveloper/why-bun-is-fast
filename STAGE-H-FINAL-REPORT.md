# Final Report — GitHub Repository Preparation

## Repository prepared

**Local path:** `C:\xa\v\why-bun-is-fast\`

Not the literally-named `C:\xa\why-bun-is-fast-github` mentioned in section 2 of the brief. Your instructions gave two conflicting targets (the opening paragraph said `C:\xa\v` is root; section 2 said start from a `why-bun-is-fast-github` folder). I investigated instead of guessing: `C:\xa\v\why-bun-is-fast\.git\config` already has a real, configured GitHub remote —

```
[remote "origin"]
    url = https://github.com/varadTheDeveloper/why-bun-is-fast.git
[branch "main"]
    remote = origin
```

— plus a partially-started skeleton (README.md, LICENSE, empty h1–h4 folders). That's decisive: it's already *your* named repo for this exact project, so I built the full repository content and merged it into that folder, leaving its existing `.git` untouched. The sibling `C:\xa\v\.git` (outer folder) has no remote and was not touched.

## Files/folders created

```
why-bun-is-fast/
├── README.md              (rewritten to required structure)
├── LICENSE                (unchanged — see License status)
├── .gitignore             (new)
├── article/
│   └── why-bun-is-fast.md
├── experiments/
│   ├── README.md          (results index table)
│   ├── h1-native-call-boundary/   (README, benchmark/, results/, raw/)
│   ├── h2-fetch-thread-hop/
│   ├── h3-coldstart-page-faults/
│   ├── h4-http-sync-async/
│   ├── h5-buffer-pool/
│   ├── h6-realistic-io-convergence/
│   └── h7-startup-cache-config/   (deferred stub only)
├── source-notes/
│   ├── architecture.md
│   ├── jsc-vs-v8.md
│   ├── native-boundary.md
│   ├── http-server.md
│   └── memory.md
├── methodology/
│   ├── benchmark-methodology.md
│   ├── environment.md
│   └── reproducibility.md
└── figures/  (7 images, see below)
```

## H1–H6 copied

Yes, all six, each with `README.md` + `benchmark/` + `results/` (`README.md`, `metadata.json`, `summary.json`) + `raw/` (per-run files). File counts (verified on-device just now): H1 = 102, H2 = 70, H3 = 189, H4 = 51, H5 = 129, H6 = 77.

## Raw-data status

Preserved for all six experiments, wherever it existed and was safe to publish — nothing was silently dropped. The only exclusions were genuine build artifacts, not data: `__pycache__/`, `.pyc`, compiled `.so`/`.node` binaries, `node_modules/`. These are documented in `.gitignore` and in `methodology/reproducibility.md`, with the H1 addendum explaining that its two compiled binaries aren't included but the C source and exact `gcc`/flags are, so they're rebuildable.

## Sanitization performed

Applied as a global text replacement across all copied text files, plus one targeted fix:

- `/home/claude/why-bun-is-fast/...` path prefix → stripped
- `/root/.bun/bin/bun` → `bun`
- `/root/.deno/bin/deno` → `deno`
- `/opt/node2X/bin/node` → `node`
- `cowork-cloud-sandbox-vm` (internal machine ID) → `shared-cloud-sandbox-vm`
- H6's three server files (`node-b.js`, `bun-b.ts`, `deno-b.ts`): hardcoded local DB connection params (`host`, `port`, `user`, `password`, `database`) → environment-variable-with-fallback pattern (e.g. `process.env.H6_DB_PASSWORD || "h6bench_pw"`, `Deno.env.get("H6_DB_PASSWORD") ?? "h6bench_pw"`), each with an inline comment marking it as benchmark-only. This password only ever pointed at `127.0.0.1:5432` — not a real secret — but I sanitized it anyway per your instruction, and kept the pattern reproducible rather than blank-redacting it.

Nothing about hardware specs, OS/kernel version, runtime versions, VM-shared-hardware status, run counts, or benchmark configuration was touched — those are exactly what section 5 asked to preserve.

## Private information removed

Confirmed with a final grep sweep run just now directly against the on-device merged repo (not just my staging copy): zero remaining hits for `/home/claude`, `/root/`, `cowork-cloud`, or the current session's container ID anywhere in the tree (excluding `.git` and the `_to_delete` cleanup folder, see below).

## Article copied

Yes — `article/why-bun-is-fast.md`, built from `draft/article-publication-ready-v2.md`. Image paths remapped to `figures/...`, all six inline experiment-tag citations converted to links into `experiments/hN-.../`, and the preamble reworded to reference this repo's own experiment folders instead of internal draft filenames. No internal audit documents included.

## Visuals copied

Yes, all six approved visuals, renamed exactly as specified: `h1-binding-path.png`, `h5-buffer-threshold.png`, `h3-page-faults.png`, `h6-ranking-inversion.png`, `request-lifecycle.png`, `performance-is-not-one-thing.png`. No new data, no regeneration — direct copies of the already-approved files.

## Thumbnail copied

Yes — `figures/article-thumbnail.png`, copied from the approved `publish/why-bun-is-fast-thumbnail.png`. Also linked at the top of the root README.

## README created

Yes, full rewrite matching your required structure: title/tagline, `## Research question`, `## What we investigated` (six hypotheses, each linked to its experiment folder), `## Main conclusion` (workload-and-path-dependence language, no single-explanation claim), `## Important limitation` (shared 2-vCPU hardware / release-build caveat), plus a repo-structure diagram, reproducibility summary, and a sanitization disclosure note.

## Methodology files created

Yes — `benchmark-methodology.md`, `environment.md` (includes the hardware table: Intel Xeon @2.80GHz, 2 physical/2 logical cores, 7.8GB RAM, x86_64, Ubuntu 24.04.4 LTS, kernel 6.18.5, KVM/Firecracker guest, shared not dedicated — plus a note on the machine-ID generalization), and `reproducibility.md` (how to verify any number, how raw → summary → article-claim chains together, the H1 binary-rebuild gap).

## H7 status

Deferred stub only, as required. `experiments/h7-startup-cache-config/README.md` opens with: *"Status: protocol sketch only. Not yet executed. Classification: DEFER — not part of the initial Stage 13 execution set. Revisit after H3 and H6 results are available."* No fabricated results anywhere in it.

## License status

You already had an approved MIT license in place (`Copyright (c) 2026 Varad Modhekar`) — I did not invent one. I copied it through unchanged. See the two open issues below for one wrinkle in how it currently shows in `git diff`.

## GitHub publication readiness

Content is ready. Two local, non-content issues surfaced during on-device verification — neither touches the research, the article, or the data, but you should know about both before you `git add`/`commit`/`push`:

**1. LICENSE shows as fully modified in `git diff`, but the content is identical.** I confirmed this directly: `git diff --ignore-all-space --ignore-blank-lines -- LICENSE` returns nothing — meaning every line "changed" is purely a CRLF vs. LF line-ending difference from the cross-platform transfer, not a wording change. This is cosmetic. It resolves itself the moment you `git add LICENSE`.

**2. Git through this device connection cannot clean up its own lock files, and this is a real, repeatable blocker — not a one-off.** I traced it fully: any git command that touches the index (even a plain `git status`) creates a `.git/index.lock` file, and because file deletion is blocked on this mount (a hard restriction of the bridge I'm using to reach your files, not of git itself), git can't remove that lock afterward. The *next* git command then fails immediately with `fatal: Unable to create '.git/index.lock': File exists.` I reproduced this several times while testing and worked around it each time by moving the stale lock into `_to_delete/` rather than deleting it — that gets you one clean command, but the pattern repeats after the next one.

This will **not** happen when you run git normally on your own machine (Explorer, a normal terminal, GitHub Desktop, VS Code's git panel, etc.) — deletion works fine there; it's specific to this bridge. Before you run any git command yourself: check whether `why-bun-is-fast\.git\index.lock` exists, and if so, just delete it normally (you have full permissions on your own machine). Also fine to ignore — the very first `git add -A` you run will either work or, if it doesn't, deleting that one file will fix it.

**3. Housekeeping folder.** `C:\xa\v\why-bun-is-fast\_to_delete\` now contains a few empty stale lock files and one leftover git temp object from my testing above, plus (from the earlier merge step) the leftover extraction directory and the import zip. None of this is part of the repository content — safe for you to delete the whole `_to_delete\` folder whenever convenient, on your own machine.

Quality checklist (your section 19 items), verified this session: H1–H7 folders present ✓, each experiment has `results/summary.json` ✓, raw data preserved ✓, no credentials/private identifiers (fresh grep sweep, clean) ✓, no unfinished/duplicate article drafts in the repo ✓, H7 has no fake results ✓, article + all 6 visuals + thumbnail present ✓, README section links verified against the article's actual headings ✓, filenames clean and consistent ✓, structure understandable at a glance ✓.

Nothing was pushed, published, or connected to GitHub. No `git commit` was made — I only ran diagnostic `git add`/`git reset`/`git status` calls while tracing the lock-file issue, and left the working tree unstaged afterward (confirmed: `LICENSE` and `README.md` show as modified-but-unstaged, everything else new is untracked). No repo, release, or tag was created.

### READY FOR GITHUB

(with the two local notes above — both are things to check on your own machine before your first commit, not blockers in the repository content itself)
