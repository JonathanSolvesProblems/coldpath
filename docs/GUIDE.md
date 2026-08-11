# Field guide: is your Arm AI build actually using the matrix hardware?

A short, practical guide to a problem that silently costs Arm LLM deployments ~5x, and how to check any
build for it in one command. No Arm hardware required.

## The 30-second version

Modern Arm CPUs have dedicated instructions for the matrix and dot-product math that dominates LLM
inference: `sdot` (dot-product), `smmla` (i8mm integer matrix multiply), and the SME/SME2 ZA-tile engine.
Using them is often **5x or more** on prompt processing. But these instructions are chosen when the code
is **compiled**, and a build with the wrong flags simply leaves them out. The binary still runs and still
gives correct answers, just slowly, and nothing warns you. `coldpath` disassembles the binary and tells
you which of these instructions are present, so you can catch a "cold" build before it ships.

## Why a build ships without them

Three independent reasons, any one of which produces a cold binary:

1. **No `-march`, so the compiler targets baseline `armv8-a`.** ggml's fast paths are guarded by
   `#if defined(__ARM_FEATURE_MATMUL_INT8)` / `__ARM_FEATURE_DOTPROD`. Under plain `armv8-a` those macros
   are undefined and the kernels are compiled out. This is what happened to Ollama's Windows-on-Arm build:
   its CMake preset disabled multi-variant dispatch and set no target arch, so the compiler defaulted to
   `armv8-a`.
2. **Portable / cross-compiled builds.** A *native* build (`cmake` on the target) detects the host and
   comes out warm. But prebuilt binaries are usually cross-compiled or built `GGML_NATIVE=OFF` for
   compatibility across devices, and then someone has to pick the `-march` explicitly. Miss it, and the
   binary is cold. Cold is a **distribution** default, not a build default.
3. **An accelerated kernel library is off by default.** ggml reaches SME only through Arm's KleidiAI, and
   `GGML_CPU_KLEIDIAI` defaults to **OFF**. So ggml-based runtimes (llama.cpp, Ollama) ship **zero SME**
   even on SME-capable silicon, while ONNX Runtime and ExecuTorch, which enable KleidiAI by default, ship
   real SME2 kernels. Same dependency, opposite default.

## How to check any build

```bash
pip install coldpath
coldpath path/to/libggml-cpu.so     # a shared library
coldpath ./release-dir/             # a whole release
coldpath some-wheel.whl             # or an archive / APK
```

Read the verdict:

| verdict | meaning | what it runs |
|---|---|---|
| **HOT** | SME/SME2 present | the matrix engine |
| **WARM** | i8mm (`smmla`) present | integer matrix multiply |
| **TEPID** | only dot-product (`sdot`) | fast, but no matrix unit |
| **COLD** | none of the above | scalar / plain NEON |
| **UNKNOWN** | too little of `.text` decodable to judge | (coldpath refuses to guess) |

`COLD` on a binary you built or downloaded means the matrix path was compiled out. That is the finding to
act on.

## How to fix it

Depending on how you build:

- **Building llama.cpp / ggml yourself:** set a target arch, e.g.
  `-DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod` (safe on every Arm device with dot-product) or
  `-DGGML_CPU_ARM_ARCH=armv8.6-a+i8mm` (adds the matrix unit on i8mm-capable cores). To ship one binary
  that adapts at runtime instead, use `-DGGML_CPU_ALL_VARIANTS=ON` (Linux/Android/Apple), which compiles
  several ISA variants and loads the best one for the host.
- **Want SME on capable silicon:** build with `-DGGML_CPU_KLEIDIAI=ON`.
- **Shipping to a fixed cloud target (Graviton/Cobalt/Axion):** those are i8mm-capable, so
  `armv8.6-a+i8mm` (or `-mcpu=native` on the build host) is safe and fast.

## Gate it so it can't come back

Add coldpath to CI so a cold build fails the pull request before it reaches users:

```yaml
- uses: JonathanSolvesProblems/coldpath@v1
  with:
    path: ./build/
    require: i8mm
```

## A one-line checklist for your own Arm AI build

- [ ] Did I set an explicit `-march` / `-mcpu`, or enable `GGML_CPU_ALL_VARIANTS`?
- [ ] For SME-capable targets, is `GGML_CPU_KLEIDIAI=ON`?
- [ ] Did `coldpath` on the shipped binary report WARM or HOT, not COLD or TEPID?
- [ ] Is that check wired into CI so a future change can't silently regress it?

If all four are yes, your Arm AI build is using the hardware you are paying for.
