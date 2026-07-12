# coldpath

**Prove whether an Arm binary can actually use the chip's matrix hardware.**

```
$ coldpath ollama/lib/ollama/ggml-cpu.dll

lib/ollama/ggml-cpu.dll
  154,880 instructions   COLD PATH  -- 0 matrix instructions
    --   SME  (ZA tile)                     0
    --   i8mm (smmla)                       0
    --   dotprod (sdot)                     0

    This binary cannot execute a matrix or dot-product instruction on ANY Arm CPU,
    no matter what hardware it runs on. The kernels were not compiled in.
```

That is Ollama's current official Windows-on-Arm release. Every Snapdragon X laptop running it is
doing LLM matrix multiplication with no matrix hardware, and until now there was no way to know.

---

## Why this is possible at all

ggml, XNNPACK and most Arm kernel libraries pick their fast paths at **compile time**, not run time.
ggml's runtime ISA detection was deliberately removed upstream ([PR #10457]) after it caused a 15x
regression. So if a build sets the wrong `-march`, the fast path is not merely *unused*:

> **it is not in the binary.**

There is no warning. The library loads, the model runs, the answers are correct, and the matrix unit
sits idle. KleidiAI ships no introspection API, and Arm's own guidance is to open a profiler and read
assembly symbol names out of a call-paths view.

`coldpath` just disassembles `.text` and looks. Because dispatch is compile-time, **absence in the
binary is proof of absence at runtime.** No profiler, no PMU counters, no Arm hardware required — it
runs fine on your x86 laptop.

[PR #10457]: https://github.com/ggml-org/llama.cpp/pull/10457

## Install

```bash
pip install coldpath
```

## Use

```bash
coldpath libggml-cpu.so                  # one binary
coldpath ./build/                        # a directory tree
coldpath onnxruntime-1.27.0-aarch64.whl  # a wheel
coldpath app-release.apk                 # an Android APK
coldpath --require i8mm ./dist/          # exit 1 if anything lacks i8mm  <- CI gate
coldpath libfoo.so --json                # machine-readable
```

Reads AArch64 **ELF, PE and Mach-O** (including universal binaries), and looks inside `.whl`, `.apk`,
`.zip`, `.tar.gz` and `.tar.zst`.

### As a CI gate

```yaml
- uses: jonathan/coldpath@v1
  with:
    path: ./build/
    require: i8mm
```

Fails the PR before a cold binary reaches your users.

---

## Is it right?

Fair question, and the answer isn't "trust me."

llama.cpp's official `linux-arm64` release ships **eight** `ggml-cpu` backends with the ISA level in
the filename. That is an answer key written by someone else. A correct detector has to reproduce the
staircase exactly — every feature each rung claims, and none it doesn't:

```
$ python scripts/verify_ladder.py llama-b9977/

variant          dotprod      sve    i8mm    sme   verdict
----------------------------------------------------------
armv8.0_1              0        0       0      0   ok
armv8.2_1          1,184        0       0      0   ok
armv8.2_2          1,184        0       0      0   ok
armv8.2_3          1,235   10,691       0      0   ok
armv8.6_1          1,253   12,058     402      0   ok
armv8.6_2          1,253   11,943     402      0   ok
armv9.2_1          1,253   12,043     402      0   ok
armv9.2_2          1,253   12,043     402      0   ok

coldpath reproduces llama.cpp's own 8-variant ISA ladder exactly.
```

And a **positive control**, so "zero" means something. ONNX Runtime's stock aarch64 wheel really does
ship SME2 kernels, and coldpath finds them:

| `pip install onnxruntime` → `libonnxruntime.so` | count |
|---|---|
| **SME (ZA tile)** | **106** |
| — of which MOPA outer-product | 84 |
| i8mm | 800 |
| bf16 | 320 |
| dotprod | 1,642 |

A detector that reports zero everywhere is worthless. This one discriminates.

`pytest` covers each instruction family against hand-assembled encodings, so correctness is provable
without any binary on disk.

---

## What it found

Scans of **official, unmodified release binaries**, downloaded from each project's own release channel.
Re-verified on every push by [`.github/workflows/test.yml`](.github/workflows/test.yml) — if a project
fixes its build, that job fails, on purpose.

| binary (official release) | SME | i8mm | dotprod | |
|---|---|---|---|---|
| ONNX Runtime 1.27.0, aarch64 wheel | **106** | 800 | 1,642 | HOT |
| llama.cpp b9977, linux-arm64 | 0 | 402 | 1,253 | WARM |
| llama.cpp b9977, win-arm64 | 0 | 244 | 1,052 | WARM |
| Ollama v0.31.2, linux-arm64 | 0 | 220 | 969 | WARM |
| **Ollama v0.31.2, win-arm64** | **0** | **0** | **0** | **COLD** |

### 1. Ollama's Windows-on-Arm build has no matrix instructions at all

Not disabled. **Absent.** 154,880 instructions of baseline Armv8.0 NEON — not one `sdot`, not one
`smmla`.

This is not a platform limitation. llama.cpp's own Windows-on-Arm build, on the same OS and the same
chip, has working i8mm — and Ollama *builds llama.cpp from source*. The difference is a build flag:
Ollama's `cpu_arm64` preset sets `GGML_CPU_ALL_VARIANTS=OFF` and `GGML_NATIVE=OFF` and never sets
`GGML_CPU_ARM_ARCH`, so ggml's CMake appends no `-march` at all and the compiler falls back to plain
`armv8-a`.

It may also explain [ollama#8246], open since 2024: 5–10 seconds *per token* on one Arm chip, fine on
another, closed with no root cause. That is the fingerprint of a missing-dotprod build.

[ollama#8246]: https://github.com/ollama/ollama/issues/8246

### 2. Nothing in the ggml ecosystem ships SME — including the backends named for it

`libggml-cpu-armv9.2_1.so` and `libggml-cpu-armv9.2_2.so` are named for SME in ggml's own variant
list. Both contain **zero** ZA-tile instructions, zero `smstart`, zero outer products.

The cause: SME reaches ggml only through KleidiAI, and `GGML_CPU_KLEIDIAI` defaults to **OFF**.
`kai_*` symbol count across all eight variants is zero.

It bites because ggml's dispatcher loads the *highest* variant the CPU supports. On an Apple M4, a
Dimensity 9500, or a Snapdragon 8 Elite Gen 5, it loads `armv9.2_2` believing it picked the
SME-optimised backend, and gets a library with no SME in it. ONNX Runtime, in the same table above,
shows this is entirely achievable.

---

## What this costs, in tokens/sec

Measured on a **free GitHub-hosted Arm64 runner** (Azure Cobalt 100, Neoverse N2) by
[`.github/workflows/benchmark.yml`](.github/workflows/benchmark.yml), which builds llama.cpp three
times on the same hardware changing **only** the `-march` flag, uses `coldpath` to prove what landed
in each binary, then benchmarks each one.

You can re-run it yourself from the Actions tab. The numbers are not a screenshot; they regenerate.

> **Not yet measured. This section will be filled in from a real CI run, not estimated.**
> Anything else would be exactly the kind of unverified claim this tool exists to catch.

---

## Honest limitations

- **Static counts, not dynamic ones.** coldpath proves an instruction is *present and reachable*, not
  how often it executes. A binary can contain `smmla` and still not use it for your shape or quant.
  Absence is proof; presence is necessary, not sufficient.
- **SME vs SME2 are not yet distinguished.** Both report as `sme`. Every SME instruction found so far
  in the wild is SME2, but coldpath does not currently prove that, and it should.
- **No Arm hardware was used to produce the static findings**, by design — that is the point. The
  tokens/sec numbers do come from real Arm64 silicon (Neoverse N2, in CI).
- **No SME hardware was benchmarked.** No cloud Arm CPU has SME (confirmed on Graviton, Cobalt,
  Axion), and I don't own an M4. The SME finding is therefore proven *statically* — the instructions
  are absent — but its performance cost is **not** measured here. I'm not going to quote Arm's "up to
  6x" as if it were mine.
- **Coverage is reported per binary.** Below 95% decoded, treat counts as a lower bound.
- **This tool checks whether a binary is *fast* on Arm. It does not check whether it *builds* or
  *runs* on Arm** — a different and already well-served question.

## Licence

MIT.
