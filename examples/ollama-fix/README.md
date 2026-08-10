# The optimization: a one-line build-flag fix for Ollama on Windows-on-Arm

coldpath found this. This directory is the fix and the measured recovery.

## What coldpath found

```
$ coldpath ollama-windows-arm64/lib/ollama/ggml-cpu.dll

lib/ollama/ggml-cpu.dll
  154,880 instructions   100.0% decoded   COLD  -- 0 matrix instructions
  sha256:536ada0d3dd642f4...
    --   SME  (ZA tile)        0
    --   i8mm (smmla)          0
    --   dotprod (sdot)        0
```

Ollama's official Windows-on-Arm build (v0.31.2) contains **zero** matrix and **zero** dot-product
instructions. Every Snapdragon X laptop running it does LLM matmul in scalar/NEON only. For contrast,
coldpath on llama.cpp's own Windows-on-Arm build, same OS, same ggml source:

```
llama.cpp win-arm64 ggml-cpu.dll   WARM   i8mm 244   dotprod 1,052
```

## Root cause (confirmed in source, not inferred)

Ollama does not fork ggml's kernels; it fetches upstream llama.cpp pinned by `LLAMA_CPP_VERSION` and
builds it. The difference is one build flag.

`llama/server/CMakePresets.json` has two CPU presets:

| preset | used for | `GGML_CPU_ALL_VARIANTS` | `GGML_CPU_ARM_ARCH` |
|---|---|---|---|
| `cpu` | Linux / x86 | **ON** (builds 8 ISA variants, dlopens the best at runtime) | unset |
| `cpu_arm64` | **Windows-on-Arm** | **OFF** | **unset** |

ggml's `GGML_CPU_ALL_VARIANTS` is not supported on Windows-on-Arm (it hits a `FATAL_ERROR` in ggml's
CMake), so Ollama correctly turns it OFF for that target. But nothing then sets a specific `-march`.
In `ggml/src/ggml-cpu/CMakeLists.txt` (current main), the non-native ARM branch is:

```cmake
if (GGML_CPU_ARM_ARCH)
    list(APPEND ARCH_FLAGS -march=${GGML_CPU_ARM_ARCH})
elseif(GGML_CPU_ALL_VARIANTS)
    ...                       # per-variant -march
endif()
                              # <-- neither set: NOTHING is appended
```

With both unset and `GGML_NATIVE=OFF`, **no `-march` is appended at all**, so the compiler defaults to
plain `armv8-a`. ggml's fast paths are `#if`-guarded on `__ARM_FEATURE_DOTPROD` / `__ARM_FEATURE_MATMUL_INT8`,
so under `armv8-a` they are compiled out. The instructions are not slow, they are **absent** — exactly
what coldpath reports.

## The fix

[`cpu_arm64.patch`](cpu_arm64.patch) adds one line to the `cpu_arm64` preset:

```json
"GGML_CPU_ARM_ARCH": "armv8.2-a+dotprod",
```

`armv8.2-a+dotprod` is the safe universal floor: **every** Windows-on-Arm device supports it (Cortex-A76
based Surface SQ1/SQ2 and Snapdragon X / Oryon all have FEAT_DotProd), so there is no SIGILL risk on any
shipped hardware. It restores the dot-product kernels that carry most of the win.

For a Snapdragon-X-targeted build, `armv8.7-a` additionally enables i8mm (`smmla`), matching what
llama.cpp's own Windows-on-Arm release ships, at the cost of dropping pre-Snapdragon-X SQ-series
compatibility. That is the aggressive option; the patch here takes the conservative, zero-regression one.

## The measured recovery

Same model (Qwen2.5-0.5B, Q4_0), same Arm hardware (Neoverse N2), changing only `-march`. Produced by
[`../../.github/workflows/benchmark.yml`](../../.github/workflows/benchmark.yml), re-runnable from the
Actions tab. coldpath scans each build to prove what landed in it; `llama-bench` measures the cost.

| build | what coldpath sees | prompt processing (pp512) | vs COLD |
|---|---|---:|---:|
| **COLD** `armv8-a` (Ollama's current WoA build) | i8mm 0, dotprod 0 | 94.67 tok/s | 1.0x |
| **TEPID** `armv8.2-a+dotprod` (this patch) | dotprod 1,044 | 543.94 tok/s | **5.75x** |
| **WARM** `armv8.6-a+i8mm` (Snapdragon X option) | i8mm 268, dotprod 1,044 | 659.71 tok/s | **6.97x** |

Token generation (decode) is memory-bandwidth-bound and rides on dot-product, not the matrix unit:
54.54 -> 122.20 tok/s (2.24x) from the dotprod fix, and essentially flat from i8mm on top (that is
expected: `smmla` is an outer product that pays off in the compute-bound prefill GEMM, not in batch-1
decode). Prompt processing is where the cold path costs the most, and it costs ~6-7x.

This likely also explains [ollama#8246](https://github.com/ollama/ollama/issues/8246): 5-10 seconds per
token on one Arm chip, fine on another, closed with no root cause. That is the fingerprint of a build
that fell back to scalar because the fast-path instructions were compiled out.

## Status

The patch is proposed upstream to Ollama. The measurement above is reproducible in CI on free Arm64
hardware; the root cause is confirmed against Ollama's and ggml's current source.
