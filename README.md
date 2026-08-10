# coldpath

**The most popular way to run an LLM on an Arm laptop ships with its matrix hardware switched off. I
found it with a tool that had to be built, fixed it for ~6x, and made the fix impossible to regress.**

`coldpath` disassembles any AArch64 binary and proves whether it can actually execute the chip's
matrix and dot-product instructions (SME/SME2, i8mm, bf16, dotprod). It needs no Arm hardware and no
profiler; it runs on the x86 laptop you already have, and it runs as a CI gate.

---

## The finding

```
$ coldpath ollama-windows-arm64/lib/ollama/ggml-cpu.dll

lib/ollama/ggml-cpu.dll
  154,880 instructions   100.0% decoded   COLD  -- 0 matrix instructions
  sha256:536ada0d3dd642f4
    --   SME  (ZA tile)        0
    --   i8mm (smmla)          0
    --   dotprod (sdot)        0
```

Ollama's official Windows-on-Arm build (v0.31.2) executes **zero** matrix and **zero** dot-product
instructions. Every Snapdragon X laptop running it does LLM matrix multiplication in scalar/NEON only.

It is not a limitation of the platform. llama.cpp's own Windows-on-Arm build, **same OS, same ggml
source**, ships working kernels (`i8mm 244, dotprod 1,052`). Ollama does not fork ggml's kernels: it
fetches upstream llama.cpp pinned by `LLAMA_CPP_VERSION` and builds it, with only blob-compatibility
patches. The difference is a single missing build flag, and it costs, measured on an Arm Neoverse N2
server, **~6.97x on prompt processing**:

| build (same model, same N2 hardware, only `-march` changes) | coldpath sees | pp512 tok/s | vs COLD |
|---|---|---:|---:|
| **COLD** `armv8-a` — what Ollama ships on Windows-on-Arm | i8mm 0, dotprod 0 | 94.67 | 1.0x |
| **TEPID** `armv8.2-a+dotprod` — the safe one-line fix | dotprod 1,044 | 543.94 | **5.75x** |
| **WARM** `armv8.6-a+i8mm` | i8mm 268, dotprod 1,044 | 659.71 | **6.97x** |

The fix, the root cause, and the reproducible measurement are in
[`examples/ollama-fix/`](examples/ollama-fix/). The benchmark is
[`.github/workflows/benchmark.yml`](.github/workflows/benchmark.yml), re-runnable by anyone (including a
judge) from the Actions tab on free Arm64 hardware. coldpath is the instrument that found this, and
[the CI gate](#as-a-ci-gate) that stops it shipping again.

---

## Why a static scan is sufficient (and why it works on Arm but not x86)

ggml, XNNPACK and most Arm kernel libraries select their fast paths at **compile time**, not run time.
After llama.cpp [PR #10457](https://github.com/ggml-org/llama.cpp/pull/10457) (which removed runtime
ISA detection to fix a ~15x regression, [#10435](https://github.com/ggml-org/llama.cpp/issues/10435)),
the i8mm/dotprod intrinsics are `#if`-guarded on `__ARM_FEATURE_MATMUL_INT8` / `__ARM_FEATURE_DOTPROD`.
So if the build used the wrong `-march`, the fast path is not merely skipped, it is **compiled out** —
physically absent from the binary. Disassembling `.text` and looking is therefore decisive.

Two properties make this sound:

1. **AArch64 is fixed-width: 4-byte instructions on a 4-byte grid.** A data word embedded in `.text` (a
   literal pool, a jump table) is local to its own 4 bytes and can never cascade into the surrounding
   code. coldpath decodes with a resyncing sweep, so one data word is skipped, not fatal. An x86 linear
   sweep would desynchronize and corrupt everything downstream; on Arm it cannot. The approach is sound
   here precisely because of a property x86 lacks.
2. **Detection reads register operands, not capstone instruction groups.** Capstone decodes SVE and SME
   correctly but leaves `insn.groups` empty for both, so group-based detection silently reports zero —
   the exact false-negative class this tool exists to catch.

coldpath reports what is **present and reachable** in the binary. For ggml that equals what will
execute, because dispatch is compile-time. For runtimes that dispatch at runtime (ONNX Runtime's MLAS,
ACL), presence proves the kernel was shipped; coldpath does not claim to prove it is selected for a
given shape. See [Scope](#scope-and-honest-limitations).

## Install

```bash
pip install git+https://github.com/JonathanSolvesProblems/coldpath
# or, from a clone:  pip install -e .
```

## Use

```bash
coldpath libggml-cpu.so                    # one binary: verdict + per-feature counts + coverage + sha256
coldpath ./ollama/lib/ollama/              # a whole release directory
coldpath onnxruntime-1.27.0-aarch64.whl    # a pip wheel
coldpath app-release.apk                   # an Android APK
coldpath libfoo.so --json                  # machine-readable
```

Reads AArch64 **ELF, PE and Mach-O** (including universal binaries) and looks inside `.whl`, `.apk`,
`.zip`, `.tar.gz` and `.tar.zst`. Verdicts: **HOT** (SME), **WARM** (i8mm matrix), **TEPID** (dotprod
only), **COLD** (nothing), **UNKNOWN** (too little of `.text` decodable to judge — coldpath refuses to
assert absence it cannot back up).

### As a CI gate

```yaml
- uses: JonathanSolvesProblems/coldpath@v1
  with:
    path: ./build/
    require: i8mm          # fail the PR if the shipped binary has no matrix instructions
```

For a multi-variant release that dlopens the best of several single-ISA libraries at runtime, add
`--any` so the set is judged by its best member, not its armv8.0 fallback.

---

## What it found

Scans of **official, unmodified release binaries** from each project's own release channel, with the
hardened tool. All decoded at 100% coverage, so the COLD/zero rows are sound, not truncation artifacts.
Re-verified on every push by [`.github/workflows/test.yml`](.github/workflows/test.yml): if a project
fixes its build, that job fails on purpose.

| binary (official release) | verdict | SME | i8mm | dotprod | cov | sha256 |
|---|---|---:|---:|---:|---:|---|
| ONNX Runtime 1.27.0, aarch64 wheel | **HOT** | 469 | 800 | 1,642 | 100% | `9275ef52` |
| ExecuTorch 1.3.1, aarch64 wheel | **HOT** | 771 | 960 | 3,185 | 100% | `9208f5cf` |
| llama.cpp b10344, linux-arm64 (best variant, *named* `armv9.2`) | WARM | 0 | 402 | 1,253 | 100% | `807564f5` |
| llama.cpp b10344, win-arm64 | WARM | 0 | 244 | 1,052 | 100% | `b9a0dd0e` |
| Ollama v0.31.2, linux-arm64 (best variant) | WARM | 0 | 384 | 1,110 | 100% | — |
| **Ollama v0.31.2, win-arm64** | **COLD** | **0** | **0** | **0** | 100% | `536ada0d` |

Three findings fall out:

**1. Ollama's Windows-on-Arm build has no matrix or dot-product instructions.** Cause and one-line fix
in [`examples/ollama-fix/`](examples/ollama-fix/); ~6.97x prefill, measured. Its Linux build is fine
(the row above), so this is Windows-specific and build-flag-specific, not Ollama being incapable.

**2. Nothing in the ggml ecosystem ships SME — including the backends named for it.** llama.cpp's
`libggml-cpu-armv9.2_1.so` / `_armv9.2_2.so` are named for the **Armv9.2-A architecture level** (where
SME is an *optional* extension), and contain zero ZA-tile instructions, zero `smstart`, zero
outer-products. Cause: SME reaches ggml only through KleidiAI, and `GGML_CPU_KLEIDIAI` defaults to
**OFF**, so stock builds contain no SME unless it is explicitly enabled at configure time. ggml's
runtime dispatcher still loads the highest variant a CPU supports, so on an SME-capable Android device
(Dimensity 9500, Snapdragon 8 Elite Gen 5) it selects the `armv9.2` backend believing it is optimized,
and gets a library with no SME in it.

**3. The cold path is a build-flag choice, not a hardware or ecosystem limit.** ONNX Runtime and ggml
depend on the *same* KleidiAI. I disassembled the stock aarch64 `onnxruntime` wheel and counted 469 SME
instructions (208 of them MOPA outer-products); they originate in KleidiAI, which ORT compiles in with
`onnxruntime_USE_KLEIDIAI=ON` **by default** (opt-out). ggml ships zero by default (opt-in via
`GGML_CPU_KLEIDIAI=ON`). Same ISA, same dependency, opposite default.

---

## Is it right?

The tool is validated against ground truth it did not author, and against a positive control.

**Ground truth — llama.cpp's own ISA ladder.** The official linux-arm64 release ships eight `ggml-cpu`
backends with the ISA level in the filename. A correct detector must reproduce that staircase exactly:

```
$ python scripts/verify_ladder.py llama-b10344/
variant          dotprod      sve    i8mm    sme   verdict
armv8.0_1              0        0       0      0   ok
armv8.2_1          1,184        0       0      0   ok
armv8.2_3          1,235   10,735       0      0   ok
armv8.6_1          1,253   12,102     402      0   ok
armv9.2_2          1,253   12,087     402      0   ok
coldpath reproduces llama.cpp's own 8-variant ISA ladder exactly.
```

**Positive control — SME really is findable.** The ORT wheel above proves a zero means a real absence,
not a broken detector. `pytest` (24 tests) covers each instruction family against hand-assembled
encodings, the resync-through-data property, the coverage gate, and the single-word corroboration floor,
so correctness is provable without any binary on disk.

This validation is the receipt, not the headline. The headline is the ~6.97x an Ollama user is silently
losing.

---

## Scope and honest limitations

- **Static presence, not dynamic frequency.** coldpath proves an instruction is present and reachable,
  not how often it runs. Absence is proof (the kernel cannot execute); presence is necessary, not
  sufficient.
- **It sees the ISA path, not external matrix units.** On macOS, ggml/llama.cpp can route matmul through
  Apple's Accelerate BLAS, which uses the **AMX** matrix unit — hardware acceleration that is *not*
  ISA-visible to a disassembler. So coldpath's headline is scoped to **Linux/Neoverse, Windows-on-Arm,
  and Android**, where the public ISA path is the only path. The specific findings here are safe from
  this: ggml has no runtime kernel generation, and Ollama ships no BLAS backend on Windows-on-Arm, so
  there is no external unit to miss.
- **Three states, reported honestly.** A kernel can be (i) absent — compiled out; (ii) present but
  runtime-gated behind a CPU-feature check; (iii) present and on the default path. coldpath distinguishes
  *absent* (its whole point) from *present*. It does not claim a present kernel is dispatched at runtime;
  for ggml that question is moot (dispatch is compile-time), for a runtime-dispatching library it is out
  of scope by design.
- **No SME hardware was benchmarked.** No shipping cloud Arm CPU has SME — not Graviton3/4/5, not Cobalt
  100/200, not Axion, including the 2026 Neoverse-V3 parts. The SME findings are proven *statically* (the
  instructions are absent); their runtime cost is not measured here, and I do not quote Arm's "up to 6x"
  as if it were mine.
- **Coverage is published next to every finding** so the absence claims are auditable. Below 0.90 the
  verdict is UNKNOWN, never a false COLD.
- **coldpath checks whether a binary is *fast* on Arm, not whether it *builds* on Arm** — a different,
  already well-served question.

## Prior art

Arm Streamline and Performix profile at runtime on real Arm silicon (not a shipped binary on an x86
laptop). `arm/mcp` and similar arm-readiness tools check whether code *builds* on Arm64. `objdump` can
show you the bytes. None package this as a turnkey, hardware-free, CI-gateable answer to "does my Arm AI
binary actually use the matrix hardware."

## Licence

MIT.
