# coldpath

[![PyPI](https://img.shields.io/pypi/v/coldpath)](https://pypi.org/project/coldpath/)
[![Python](https://img.shields.io/pypi/pyversions/coldpath)](https://pypi.org/project/coldpath/)
[![tests](https://github.com/JonathanSolvesProblems/coldpath/actions/workflows/test.yml/badge.svg)](https://github.com/JonathanSolvesProblems/coldpath/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Demo](https://img.shields.io/badge/demo-YouTube-red?logo=youtube&logoColor=white)](https://www.youtube.com/watch?v=3L2rTzTAvfk)

*A linter and CI gate for whether your Arm AI binary actually uses the chip's matrix hardware.*

**coldpath audits the binaries the Arm AI ecosystem actually ships, and it found the biggest one cold.
Ollama, the most popular way to run a local LLM, ships its Windows-on-Arm build with the chip's matrix
hardware switched off, zero matrix instructions, and any portable Arm build that misses one compile flag
(pip wheels, Docker images, cross-compiled releases, which is how most binaries reach a cloud fleet) does
the same. On Azure Cobalt 100 (Neoverse N2, a cloud Arm CPU) that flag is worth 5.75x on prompt processing
and ~2.3x on generation, roughly $0.45 versus $0.08 per million prompt tokens. I built the tool that finds
it in any shipped binary with no Arm hardware, filed the one-line fix to the top offender upstream, and
gated it in CI so a cold build can't reach your Arm fleet.**

> **$0.45 → $0.08 per 1M prompt tokens, from one build flag, measured live on Azure Cobalt 100 (Neoverse N2).**

**▶ [Watch the demo (90s)](https://www.youtube.com/watch?v=3L2rTzTAvfk)  ·  📝 [Read the write-up](https://jonathansolvesproblems.com/blog/coldpath-ollama-arm-matrix-unit-off-ci-gate/)**

`coldpath` disassembles any AArch64 binary and proves whether it *contains* the chip's matrix and
dot-product instructions (SME/SME2, i8mm, bf16 matmul, dotprod). Absence is dispositive: zero `smmla`
means the binary cannot run an i8mm matmul on any core, no matter how it dispatches at runtime. It needs
no Arm hardware and no profiler, so it runs in CI and on the x86 laptop you already have, and it audits
binaries you did not build (a shipped release, a pip wheel, an APK), not just your own source tree.

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

That is Ollama's official Windows-on-Arm build (v0.31.2): **zero** matrix, **zero** dot-product
instructions, every matmul in scalar/NEON. It is not a platform limit. llama.cpp's own Windows-on-Arm
build, **same OS, same ggml source**, ships the kernels (`i8mm 244, dotprod 1,052`). Ollama doesn't fork
ggml: it builds pinned upstream llama.cpp with one flag missing. This is a **distribution** hazard, not a
build default: a native `cmake` build detects the host and comes out warm, but any *portable* build
(cross-compiled, reproducible, or explicitly `GGML_NATIVE=OFF` for device compatibility, which is how
prebuilt binaries are made) must pick a target `-march` or fall back to baseline `armv8-a`. That is the
choice a distributor makes, and the one Ollama got wrong for Windows-on-Arm. (Distributors who use
`GGML_CPU_ALL_VARIANTS=ON` runtime dispatch avoid it, which is why Ollama's Linux arm64 build is warm.)

What it costs, measured on **Azure Cobalt 100 (Neoverse N2)**, a cloud Arm CPU, on the free GitHub
runner. Every row is built `GGML_NATIVE=OFF` with a pinned `-march` to isolate the ISA effect; only
`-march` changes:

| build (`GGML_NATIVE=OFF`, pinned `-march`) | coldpath sees | pp512 tok/s | $ / 1M tokens | vs COLD |
|---|---|---:|---:|---:|
| **COLD** `armv8-a`, the baseline a portable build falls back to (Ollama's Windows build) | i8mm 0, dotprod 0 | ~95 | ~$0.45 | 1.0x |
| **TEPID** `armv8.2-a+dotprod`, the one-line fix I filed upstream | dotprod 1,044 | ~545 | ~$0.08 | **~5.75x** |
| **WARM** `armv8.6-a+i8mm+bf16` | i8mm 268, dotprod 1,044 | ~660 | ~$0.065 | **~6.9x** |

_The tok/s and the 5.75x ratio are measured on the 4-vCPU Neoverse N2 runner; the $/1M-tokens applies a
sample Arm-cloud on-demand rate ($0.0385/vCPU-hr, Graviton4 c8g), so only the dollar column assumes a
price. The workflow's `compare` job recomputes all of it live each run (figures vary a few percent). The
fix I filed (PR #17654) is the TEPID row, dot-product, which is safe on every shipped Arm device; the
6.9x WARM row needs i8mm, which server and newer mobile cores have but Windows-on-Arm's Cortex-A76-class
chips do not, so the PR intentionally ships dot-product only._

The fix, the root cause, and the reproducible measurement are in
[`examples/ollama-fix/`](examples/ollama-fix/). The benchmark is
[`.github/workflows/benchmark.yml`](.github/workflows/benchmark.yml); its results are in every run's
summary, and anyone who forks the repo can re-run it themselves on the free Arm64 runner (a fork is needed
because `workflow_dispatch` requires write access). coldpath is the instrument that found this, and
[the CI gate](#as-a-ci-gate) that stops it shipping again.

---

## Why a static scan is sufficient (and why it works on Arm but not x86)

ggml, XNNPACK and most Arm kernel libraries bake their fast paths into each binary at **compile time**.
(ggml can ship several single-ISA binaries and pick one at load via `GGML_CPU_ALL_VARIANTS`, but each
binary is itself compile-time fixed, and coldpath scans each one, which is exactly what the missing
Windows-on-Arm flag turned off.) After llama.cpp
[PR #10457](https://github.com/ggml-org/llama.cpp/pull/10457) (which removed runtime ISA detection to fix
a ~15x regression, [#10435](https://github.com/ggml-org/llama.cpp/issues/10435)), the i8mm/dotprod
intrinsics are `#if`-guarded on `__ARM_FEATURE_MATMUL_INT8` / `__ARM_FEATURE_DOTPROD`.
So if the build used the wrong `-march`, the fast path is not merely skipped; it is **compiled out**,
physically absent from the binary. Disassembling `.text` and looking is therefore decisive.

Two properties make this sound:

1. **AArch64 is fixed-width: 4-byte instructions on a 4-byte grid.** A data word embedded in `.text` (a
   literal pool, a jump table) is local to its own 4 bytes and can never cascade into the surrounding
   code. coldpath decodes with a resyncing sweep, so one data word is skipped, never fatal. An x86 linear
   sweep would desynchronize and corrupt everything downstream; on Arm it cannot. The approach is sound
   here precisely because of a property x86 lacks.
2. **Detection reads register operands, not capstone instruction groups.** Capstone decodes SVE and SME
   correctly but leaves `insn.groups` empty for both, so group-based detection silently reports zero,
   the exact false-negative class this tool exists to catch.

coldpath reports what is **present** in the binary's `.text`, not a reachability or dispatch analysis.
That distinction is deliberate: **absence** is the airtight direction (zero `smmla` means no core can
ever run an i8mm matmul, however the library dispatches), and absence is the whole finding here. For
ggml, presence also equals what will execute, because dispatch is compile-time. For runtimes that
dispatch at runtime (ONNX Runtime's MLAS, ACL), presence proves the kernel was shipped; coldpath does
not claim it is selected for a given shape. See [Scope](#scope-and-honest-limitations).

## Install

```bash
pip install coldpath
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
`.zip`, `.tar.gz` and `.tar.zst`. Verdicts: **HOT** (SME), **WARM** (i8mm or bf16 `bfmmla` matrix),
**TEPID** (dotprod only), **COLD** (nothing), **UNKNOWN** (too little of `.text` decodable to judge,
coldpath refuses to assert absence it cannot back up).

### As a CI gate

```yaml
- uses: JonathanSolvesProblems/coldpath@v1
  with:
    path: ./build/
    require: i8mm          # fail the PR if the shipped binary has no matrix instructions
```

Point `path` at your own build output, where every binary should be hot. When you instead scan a
whole third-party **release archive**, the matmul kernels usually live in one backend library (e.g.
`ggml-cpu.dll`) while the `llama-*`/`ollama` executables next to it are thin launcher shims that
carry no kernels, so a strict gate would fail on the shims. For that case judge the set by its best
member with `--any` (CLI) or `any: true` (Action):

```bash
coldpath --require i8mm --any ./llama-arm64-release/   # passes if the backend lib is warm
```

The same flag covers a multi-variant build that dlopens the best of several single-ISA libraries at
runtime, so the set is scored on the variant that actually loads, not its armv8.0 fallback.

### On an Arm64 machine (Graviton, Cobalt, Axion, a Pi, Apple Silicon)

coldpath needs no Arm hardware, but on an Arm64 box you can point it straight at what you just built or
installed, and confirm your own deployment uses the silicon:

```bash
pip install coldpath
# example: check the ggml that pip just installed for you
python -c "import llama_cpp, pathlib; print(pathlib.Path(llama_cpp.__file__).parent)"
coldpath "$(python -c 'import llama_cpp,pathlib;print(pathlib.Path(llama_cpp.__file__).parent)')"
# WARM/HOT = you are using the matrix unit; TEPID/COLD = you left it off, rebuild with -march (see the guide)
```

Then reproduce the full cost benchmark on Arm64 with `bash scripts/demo.sh` (scans two real releases) or by
forking and running the [benchmark workflow](.github/workflows/benchmark.yml) on the free Neoverse N2 runner.

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
| Ollama v0.31.2, linux-arm64 (best variant) | WARM | 0 | 384 | 1,110 | 100% | n/a |
| **Ollama v0.31.2, win-arm64** | **COLD** | **0** | **0** | **0** | 100% | `536ada0d` |

Three findings fall out:

**1. Ollama's Windows-on-Arm build has no matrix or dot-product instructions.** Cause and one-line fix
in [`examples/ollama-fix/`](examples/ollama-fix/); ~5.75x prefill from the dot-product fix I filed, up to
~6.9x with i8mm, measured on Cobalt 100. Its Linux build is fine (the row above), so this is
Windows-specific and build-flag-specific, not Ollama being incapable. Confirmed COLD on **all 8 stable
releases** (v0.32.2 was withdrawn) from v0.31.2 through the current v0.32.7, and
[`test.yml`](.github/workflows/test.yml) re-downloads the *latest* release and re-checks it on every push,
so this claim can't silently rot.

**2. The ggml stack ships zero SME on every OS, so SME-capable silicon runs without the matrix-tile
unit by default.** (ONNX Runtime and ExecuTorch, in the table above, *do* ship SME by default; this is
specific to the ggml stack.) This is not a claim that the `armv9.2`-named backends *should* carry SME:
SME is an *optional* extension at the Armv9.2-A level, so the filename was never a promise. The point is
what happens in practice. SME reaches ggml only through KleidiAI, and `GGML_CPU_KLEIDIAI` defaults to
**OFF**, so a stock build of `libggml-cpu-armv9.2_1.so` / `_armv9.2_2.so` contains zero ZA-tile
instructions, zero `smstart`, zero outer-products. ggml's runtime dispatcher still loads the highest
variant a CPU supports, so on an SME-capable device (Dimensity 9500, Snapdragon 8 Elite Gen 5) it loads
the `armv9.2` backend and gets a library with no SME in it, leaving the matrix-tile unit unused.

**3. The cold path is a build-flag choice, not a hardware or ecosystem limit.** ONNX Runtime and ggml
depend on the *same* KleidiAI. I disassembled the stock aarch64 `onnxruntime` wheel and counted 469 SME
instructions (208 of them MOPA outer-products); they originate in KleidiAI, which ORT compiles in with
`onnxruntime_USE_KLEIDIAI=ON` **by default** (opt-out). ggml ships zero by default (opt-in via
`GGML_CPU_KLEIDIAI=ON`). Same ISA, same dependency, opposite default.

---

## Is it right?

The tool is validated against ground truth it did not author, and against a positive control.

**Ground truth, llama.cpp's own ISA ladder.** The official linux-arm64 release ships eight `ggml-cpu`
backends with the ISA level in the filename. A correct detector must reproduce that staircase exactly:

```
$ python scripts/verify_ladder.py llama-b10344/
variant          dotprod      sve    i8mm    sme   verdict
armv8.0_1              0        0       0      0   ok
armv8.2_1          1,184        0       0      0   ok
armv8.2_2          1,184        0       0      0   ok
armv8.2_3          1,235   10,735       0      0   ok
armv8.6_1          1,253   12,102     402      0   ok
armv8.6_2          1,253   11,987     402      0   ok
armv9.2_1          1,253   12,087     402      0   ok
armv9.2_2          1,253   12,087     402      0   ok
coldpath reproduces llama.cpp's own 8-variant ISA ladder exactly.
```

**Positive control, SME really is findable.** The ORT wheel above proves a zero means a real absence,
not a broken detector. `pytest` (26 tests) covers each instruction family against hand-assembled
encodings (including that `bfmmla` counts as matrix but `bfdot` does not), the resync-through-data
property, the coverage gate, and the single-word corroboration floor, so correctness is provable
without any binary on disk.

This validation is the receipt, not the headline. The headline is the 5.75x on prefill (and ~2.3x on
decode) that one build flag recovers on Arm cloud silicon.

---

## Scope and honest limitations

- **Static presence, not dynamic frequency.** coldpath proves an instruction is present in `.text`, not
  how often (or whether) it runs. Absence is proof (the kernel cannot execute on any core); presence is
  necessary, not sufficient. For the headline finding the benchmark closes that gap directly: the WARM build runs ~6.9x
  faster than the byte-identical-source COLD build, and a binary that *contained* `smmla` but never
  dispatched to it would be no faster than COLD, so the speedup is live evidence the matrix path executes.
  For a runtime-dispatching library (ONNX Runtime's MLAS, ACL) coldpath proves the kernel shipped, not
  that it is selected for a given shape.
- **It sees the ISA path, not external matrix units.** On macOS, ggml/llama.cpp can route matmul through
  Apple's Accelerate BLAS, which uses the **AMX** matrix unit, hardware acceleration that is *not*
  ISA-visible to a disassembler. So coldpath's headline is scoped to **Linux/Neoverse, Windows-on-Arm,
  and Android**, where the public ISA path is the only path. The specific findings here are safe from
  this: ggml has no runtime kernel generation, and Ollama ships no BLAS backend on Windows-on-Arm, so
  there is no external unit to miss.
- **Three states, reported honestly.** A kernel can be (i) absent, compiled out; (ii) present but
  runtime-gated behind a CPU-feature check; (iii) present and on the default path. coldpath distinguishes
  *absent* (its whole point) from *present*. It does not claim a present kernel is dispatched at runtime;
  for ggml that question is moot (dispatch is compile-time), for a runtime-dispatching library it is out
  of scope by design.
- **No SME hardware was benchmarked.** No shipping cloud Arm CPU has SME, not Graviton3/4/5, not Cobalt
  100/200, not Axion, including the 2026 Neoverse-V3 parts. The SME findings are proven *statically* (the
  instructions are absent); their runtime cost is not measured here, and I do not quote Arm's "up to 6x"
  as if it were mine.
- **Coverage is published next to every finding** so the absence claims are auditable. Below 0.90 the
  verdict is UNKNOWN, never a false COLD.
- **coldpath checks whether a binary is *fast* on Arm, not whether it *builds* on Arm**, a different,
  already well-served question.

## How it compares

| | needs Arm hardware? | needs a running workload / profiler? | works on any x86 laptop? | one-command CI-gate verdict? | catches capstone's SME false-negative? |
|---|:---:|:---:|:---:|:---:|:---:|
| **coldpath** | **no** | **no** | **yes** | **yes** | **yes** |
| Arm Streamline / Performix | yes | yes (live profiling) | no | no | n/a |
| `arm/mcp` arm-readiness checks | no | no | yes | partial | no (checks it *builds*, not that it's *fast*) |
| `objdump` / `llvm-objdump` | no | no | yes | no (raw bytes, DIY) | no |
| runtime profilers (`perf`) | yes | yes | no | no | n/a |

These are complementary, not rivals. Arm's own **Streamline and Performix** profile a *running* workload on
real Arm silicon to find hot functions and guide optimization; coldpath is the static pre-check that runs
anywhere, including CI, and catches a mis-built binary *before* you spend an instance profiling it.
arm-readiness tools check whether code *builds* on Arm64; coldpath checks whether it is *fast*. `objdump`
shows raw bytes; coldpath turns them into a one-command verdict. The clean division of labor:
**coldpath in the pull request, Performix on the instance.**

## Who this is for

- **Platform / MLOps teams running LLM inference on Arm64 cloud** (Graviton, Cobalt, Axion), the
  fast-growing default for cost-efficient inference. A cold build silently costs ~5.75x on prompt
  throughput and the matching share of the bill.
- **Runtime and image distributors** shipping portable AArch64 binaries (pip wheels, Docker images,
  prebuilt releases), where cross-compiling for device compatibility is exactly what strands the matrix path.
- **Anyone evaluating Arm instances**, to confirm the runtime they picked actually uses the silicon they
  are paying for.

Reusable artifacts come out of it: the upstream fix
([PR #17654](https://github.com/ollama/ollama/pull/17654)), the [CI-gate Action](#as-a-ci-gate) others can
drop into their own pipeline, the [scan scoreboard](RESULTS.md) of official Arm AI binaries, and a
[field guide](docs/GUIDE.md) that teaches why Arm builds ship cold and how to check and fix any of your
own, in one command.

**New here?** Start with the [field guide](docs/GUIDE.md): the 30-second version, why it happens, how to
check any build, and how to fix it. To reproduce every claim yourself in two minutes, follow
[TESTING.md](TESTING.md). For the full story of the finding and the fix, read the
[write-up](https://jonathansolvesproblems.com/blog/coldpath-ollama-arm-matrix-unit-off-ci-gate/).

## Licence

MIT.
