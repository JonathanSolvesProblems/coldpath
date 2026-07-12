# Arm Create: AI Optimization Challenge — Strategy

**Deadline:** Aug 14, 2026, 4:00pm PDT. **Track:** Cloud AI (primary), Mobile AI (secondary demo).
**Prizes:** $3,000 overall / $2,000 runner-up / $1,000 best-in-category ×3.
**Field:** ~1,227 registered. Expect 150–300 submissions for 5 prizes.

## The one-sentence pitch

A verifier that proves whether an Arm AI binary can actually execute the chip's
matrix-multiply instructions, and the ecosystem scan showing that most of them can't.

## The headline metric (hypothesis, written before first commit)

> The most popular way to run an LLM on Arm cannot execute a single matrix-multiply
> instruction on hardware that has them.

Backing numbers to produce:
- % of the most-downloaded Arm AI containers / wheels / release binaries with zero `smmla` in `.text`
- Recovered speedup after rebuilding correctly, measured on Neoverse N2 in CI
- Multi-axis table: prefill t/s, decode t/s, memory, model size, quality delta

## Why this wins with THIS panel

| Judge | Their thing | How we hit it |
|---|---|---|
| **Rani Mandepudi** | Lands arm64 CI into upstream projects. Built Arm's Ecosystem Dashboard (smoke-tests 1,177 pkgs on arm64). Stated pain: *projects claim Arm64 support without testing it*; wants capability-based dispatch not arch-branching. | This is his exact problem, one level deeper: not "does it build on Arm" but "is it fast on Arm." He WILL clone and build it. |
| **Avin Zarlez** | Publicly said: *"Show us how you can get more performance, we won't be judging just by a single metric. Get nerdy in the details!"* Opens the code to check claims match. Files upstream arm64 fixes. Loves CI/tests. | Nothing but nerdy details. Naturally multi-axis. Thesis is literally "check claims against reality." |
| **Michael Hall** | Writes Arm's install guides. His own 14-year-old rubric: Appearance, Stability, Platform Integration, Innovation, **"Scratching an Itch."** Reads your README. | README must work on first clone. Origin story = "I couldn't answer a basic question about my own build." |
| **Gabriel Peterson** | ExecuTorch + Ethos-U + bare metal. Said last year's winners impressed via *creativity*. | Scanner covers ExecuTorch/XNNPACK binaries too. |
| **Disha Patil** | Physical AI, edge, energy efficiency. Newest judge, no prior Arm judging. | Weakest fit. Mitigate with a perf-per-watt axis and a legible 10-second explanation. |

## Hard constraints

- **No Arm hardware** except a Galaxy S24 (i8mm yes; SVE2 probably not if US/Snapdragon; SME2 impossible) and a Pi 4 (Armv8.0 — no dotprod, no i8mm, USELESS for this, do not use as hero device).
- **Dev machine is Intel x86.** Fine: static analysis of aarch64 ELFs runs anywhere.
- **Free Arm64 with i8mm/SVE2 = GitHub Actions `ubuntu-24.04-arm` runners** (Azure Cobalt 100 / Neoverse N2). Free for public repos only — and our repo must be public + MIT/Apache anyway. This is also how judges re-run our benchmarks by clicking one link.
- Oracle free tier was halved to 2 OCPU/12GB in June 2026, and it's Neoverse N1 (no i8mm, no SVE2) — near-useless for this.
- **Arm Performix does NOT support Android/iOS.** Cloud/Neoverse only. Use it for the Cloud track numbers.
- **Arm FVPs cannot be used for CPU perf claims** (one instruction per cycle; Arm's own docs say don't).

## The technical thesis (verified from source)

ggml/llama.cpp Arm kernel dispatch is almost entirely **compile-time**, not runtime. Runtime ISA
detection was deliberately deleted in PR #10457 after it caused a 15x regression. Therefore:

**If the `-march` flag is wrong, the fast path is not merely unused — it is not present in the binary.**
That means static analysis can *prove* a binary is incapable of ever hitting it. No profiler, no PMU,
no hardware required.

### Confirmed silent-fallback findings (the corpus)

| Target | Finding |
|---|---|
| **Ollama** | **Zero KleidiAI** anywhere (0 grep hits). On Windows-on-Arm, preset `cpu_arm64` sets `GGML_CPU_ALL_VARIANTS=OFF` + `GGML_NATIVE=OFF` and no `GGML_CPU_ARM_ARCH` ⇒ ggml appends **no `-march` at all** ⇒ baseline armv8-a ⇒ no dotprod/i8mm/SVE/SME ⇒ **Q4_0/Q4_K/Q8_0 repack fast paths cannot trigger.** Matching unexplained bug: issue #8246 (5–10 s/token on Snapdragon 855). |
| **llama.cpp** | `GGML_CPU_KLEIDIAI` defaults **OFF**. |
| **tinyBLAS** (upstreamed into llama.cpp, serves its matmuls) | NEON + dotprod + fp16 only. **No i8mm, no SVE, no SME.** |
| **vLLM** | Own Arm flags: `-march=armv8.2-a+bf16+dotprod+fp16`. **No i8mm/SVE/SME.** No Arm int4 micro-GEMM (`cpu_wna16.cpp` has x86 AMX and RISC-V RVV paths, nothing for Arm). |
| **MLX** | CPU backend: no KleidiAI, no i8mm, no SME. Dequantizes to float SIMD. |
| **KleidiAI ecosystem-wide** | **Blockwise int4 (`qsi4c32p` / `qb4w` / `8da4w`) — what every real LLM ships as — has NO SME2 microkernel anywhere.** SME2 int4 exists only for per-channel int4, which nobody uses for LLM weights. |
| **ONNX Runtime** ✅ | KleidiAI **default-ON** (build.py opt-*out* via `--no_kleidiai`). **Positive control.** |
| **ExecuTorch / XNNPACK** ✅ | KleidiAI **default-ON**. **Positive control.** |

The positive controls matter enormously: they prove the tool discriminates rather than just crying wolf,
and they turn the deliverable into a *matrix* rather than a hit piece.

### The cost of getting it wrong (published, not ours)

Mainline llama.cpp leaves **2x–7x** of prompt-processing throughput on the table on Arm for every quant
that isn't Q4_0 (ik_llama.cpp, by the author of llama.cpp's own k-quants). MXFP4 (gpt-oss) has **zero**
i8mm path on Arm — its repack PR was merged tested-on-AVX2-only. The whole IQ-quant family never
executes a single `smmla`, on any Arm chip, ever.

## Rules of engagement (from the loss post-mortems)

- **ONE flagship feature.** The scanner. Everything else is supporting or cut.
- **No hollow repo.** Avin opens the code. Every claim in the README must be checkable in the repo.
- **No overclaiming.** Rani discloses blocked tests in his own PRs and rewards that tone. State what we
  did NOT verify. Honest bounded claims score *higher* here.
- **Write the 60–90s demo script before the first commit.** Focus gate.
- **Familiar form:** "it's a linter, for whether your Arm build is actually fast." Judges have used linters.
- **Output lands where the developer already is:** a GitHub Action that fails the PR, not a dashboard.

## Poisoned ideas — do not build

- **Generic "Arm readiness checker."** Rani filed a PR to Arm's own MCP server two weeks ago adding an
  agent that auto-assesses repos for Arm readiness. His checks whether things *build* on Arm. Ours checks
  whether they're *fast* on Arm. Frame it that way explicitly and never blur the line.
- Anything on the crowded list: quantize-a-model-on-a-Pi, an Android ExecuTorch chat app, an offline
  voice assistant, a RAG bot on Graviton. These are near-verbatim clones of Learning Paths Arm itself
  linked from the track pages. Expect dozens of each.

## Open questions for the judges (office hours: Tue Jul 14, 10am PDT, Arm Discord)

See `OFFICE_HOURS.md`.
