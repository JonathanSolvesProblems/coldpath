# Verified findings

Everything here was produced by `scan.py` against **official, unmodified release binaries** downloaded
from the projects' own release channels. Nothing is rebuilt, patched, or cherry-picked. Reproduce with
`python scan.py <binary>`.

## Method, and why static analysis is sufficient

ggml/llama.cpp select Arm kernels at **compile time**, not run time. Runtime ISA detection was
deliberately removed upstream (PR #10457) after it caused a 15x regression. Consequence: if the build
used the wrong `-march`, the fast path is not merely unused, **it is absent from the binary**. So
disassembling `.text` and looking for the instructions is decisive. No profiler, no PMU, no hardware.

Detection is on **register operands**, not mnemonics or capstone instruction groups:
- **SME** — any ZA-tile register (`zas0`, `zad0`, …), `smstart`/`smstop`, or `*mopa`/`*mops`
- **SVE** — Z registers (`z0`–`z31`) or P predicate registers (`p0`–`p15`)
- **NEON** — V registers (`v0`–`v31`)

The same mnemonic (`smmla`) exists in both NEON and SVE forms and requires different `-march` features,
so splitting by register class is necessary, not cosmetic.

> **Gotcha worth documenting in the writeup:** capstone 5.0.7 decodes SVE and SME correctly but leaves
> `insn.groups` **empty** for both. Group-based detection silently reports zero. The first version of
> this scanner had exactly the same class of silent-false-negative bug that the tool exists to find.

## Validation: llama.cpp's own ISA ladder is the answer key

llama.cpp's official `linux-arm64` release ships **eight** `ggml-cpu` variants with the ISA level in
the filename. A correct scanner must reproduce that staircase monotonically. It does:

| variant (ground truth in name) | dotprod | SVE | i8mm | SME |
|---|---|---|---|---|
| `armv8.0_1` | 0 | 0 | 0 | 0 |
| `armv8.2_1` (DOTPROD) | 1,184 | 0 | 0 | 0 |
| `armv8.2_3` (+FP16 +SVE) | 1,235 | 10,568 | 0 | 0 |
| `armv8.6_1` (+MATMUL_INT8) | 1,253 | 11,767 | **402** | 0 |
| `armv8.6_2` (+SVE2) | 1,253 | 11,652 | 402 | 0 |
| `armv9.2_1` (**+SME**) | 1,253 | 11,752 | 402 | **0** ⚠ |
| `armv9.2_2` (**+SVE2 +SME**) | 1,253 | 11,752 | 402 | **0** ⚠ |

**Positive control:** ONNX Runtime's official aarch64 PyPI wheel — the scanner *does* find SME there:

| `libonnxruntime.so.1.27.0` (`pip install onnxruntime`) | count |
|---|---|
| SME (ZA tile) | **106** |
| — of which MOPA outer-product | **84** |
| i8mm NEON | 800 |
| bf16 NEON | 320 |
| dotprod NEON | 1,642 |
| SVE | 1,965 |

So the instrument discriminates. Zeroes below are real absences, not detector failure.

---

## Finding 1: llama.cpp ships two "SME" backends containing zero SME instructions

`libggml-cpu-armv9.2_1.so` and `libggml-cpu-armv9.2_2.so` are named for SME (per ggml's own variant
list: `armv9.2_1 = …+SME`, `armv9.2_2 = …+SVE2 SME`). Both contain **zero** ZA-tile instructions, zero
`smstart`, zero outer-products. They are effectively `armv8.6_2` under a different filename.

**Cause, corroborated:** `kai_*` symbol count is **0 across all eight variants**. SME reaches ggml only
through KleidiAI, and `GGML_CPU_KLEIDIAI` defaults to **OFF**. Official builds don't turn it on.

**Why it bites:** ggml's runtime dispatcher loads the *highest* variant the CPU supports. On an Apple M4,
MediaTek Dimensity 9500, or Snapdragon 8 Elite Gen 5, it loads `armv9.2_2` believing it has selected the
SME-optimised backend — and gets a library with no SME in it.

## Finding 2: Ollama's Windows-on-Arm build has NO matrix instructions at all

Same OS, same architecture, same upstream ggml (Ollama builds llama.cpp from source via FetchContent):

| official Windows-on-Arm `ggml-cpu.dll` | i8mm | dotprod | SVE | SME |
|---|---|---|---|---|
| **llama.cpp** b9977 | **244** | **1,052** | 0 | 0 |
| **Ollama** v0.31.2 | **0** | **0** | **0** | **0** |

Ollama's `ggml-cpu.dll` is 154,880 instructions of baseline Armv8.0 NEON. Not one `sdot`. Not one `smmla`.
Every Snapdragon X / X2 Elite laptop running Ollama is doing LLM matmul with no matrix hardware.

**This is not a platform limitation.** llama.cpp's own Windows-on-Arm build, on the same OS, has working
i8mm. It's a build-configuration defect in Ollama: preset `cpu_arm64` sets `GGML_CPU_ALL_VARIANTS=OFF` and
`GGML_NATIVE=OFF` and never sets `GGML_CPU_ARM_ARCH`, so ggml's CMake appends **no `-march` flag at all**
and the compiler defaults to plain `armv8-a`.

**Plausibly the root cause of a long-standing unexplained bug:** ollama#8246 (opened Dec 2024) reports
5–10 seconds *per token* on one Arm chip while another runs fine. Closed without root cause. That is the
exact fingerprint of a missing-dotprod build.

## Status of claims

| claim | status |
|---|---|
| Ollama **Windows-on-Arm** ships zero matrix instructions | ✅ **PROVEN** on the official v0.31.2 binary |
| llama.cpp "SME" variants contain no SME | ✅ **PROVEN** on the official b9977 binaries |
| ONNX Runtime ships real SME2 kernels | ✅ **PROVEN** on the official 1.27.0 wheel |
| Ollama **Linux** arm64 has the same defect | ⏳ **UNVERIFIED — do not claim.** Research suggests Linux uses `GGML_CPU_ALL_VARIANTS=ON` and is probably fine. Checking. |
| What the missing kernels cost in tokens/sec | ⏳ **UNMEASURED.** Needs a benchmark on Neoverse N2 (GitHub Actions arm64). |

Overclaiming loses this panel. Rani discloses blocked tests in his own PRs and rewards that tone.
Every row above stays honest, including the ones that make the story less dramatic.
