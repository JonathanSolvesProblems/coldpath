# Testing coldpath in two minutes

Every claim in this repo is reproducible from a clean machine with no Arm hardware. coldpath is a
static analyzer, so it runs on any OS (Windows, macOS, Linux; x86 or Arm) and reads Arm binaries.
The commands below are the exact sequence I run before every release; each one prints the result
this file predicts.

Prerequisites: Python 3.9 or newer, and `pip`. Nothing else.

## 1. Install and smoke-test

```bash
pip install coldpath          # or: python -m pip install coldpath
coldpath --version            # -> coldpath 0.1.x
coldpath --help
```

## 2. The finding: Ollama's official Windows-on-Arm build ships cold

```bash
curl -L -o ollama-win-arm64.zip \
  https://github.com/ollama/ollama/releases/download/v0.31.2/ollama-windows-arm64.zip
coldpath ollama-win-arm64.zip
```

Expected: every binary in the archive is **COLD**, with `i8mm 0`, `bf16 0`, `dotprod 0`. The
backend that does the matmul, `lib/ollama/ggml-cpu.dll`, decodes at 100% and contains zero matrix
and zero dot-product instructions. On any Arm CPU this build runs LLM matmul in scalar/NEON only.

## 3. The CI gate: the exit code is the product

```bash
coldpath --require i8mm ollama-win-arm64.zip
echo $?                       # -> 1   (Git Bash / macOS / Linux; PowerShell uses $LASTEXITCODE)
```

A non-zero exit is what fails a pull request before a cold binary reaches your Arm cloud fleet.

## 4. The contrast: same source, one build flag apart

llama.cpp builds from the same ggml source as Ollama. Its official Windows-on-Arm release ships the
kernels Ollama omits:

```bash
curl -L -o llama-win-arm64.zip \
  https://github.com/ggml-org/llama.cpp/releases/download/b10360/llama-b10360-bin-win-cpu-arm64.zip
coldpath llama-win-arm64.zip
```

Expected: the backend `ggml-cpu.dll` is **WARM**, with `i8mm 244` and `dotprod 1,052`. The same
filename in Ollama's release was COLD with 0. That single-file comparison is the whole point.

Note on release archives: the `llama-*.exe` files next to the backend DLL are thin launcher shims
that carry no kernels, so a strict gate over the whole archive fails on the shims. To gate a release
by its best member, use `--any`:

```bash
coldpath --require i8mm llama-win-arm64.zip ; echo $?          # -> 1  (strict: shims lack i8mm)
coldpath --require i8mm --any llama-win-arm64.zip ; echo $?    # -> 0  (backend lib is warm)
```

## 5. The tool's own tests

```bash
git clone https://github.com/JonathanSolvesProblems/coldpath
cd coldpath
pip install -e ".[dev]"
pytest -q                     # -> 24 passed
```

`scripts/verify_ladder.py <llama.cpp release dir>` additionally reproduces llama.cpp's own eight-rung
ISA ladder (the ISA level is in each `ggml-cpu-*` filename), which is ground truth I did not author.

## 6. Check your own deployment (on an Arm64 box)

On a Graviton, Cobalt, Axion, Raspberry Pi, or Apple Silicon machine, point coldpath at what you
actually run:

```bash
pip install coldpath
coldpath "$(python -c 'import llama_cpp,pathlib;print(pathlib.Path(llama_cpp.__file__).parent)')"
# or an installed runtime directly:
coldpath /usr/local/lib/ollama
```

WARM or HOT means you are using the matrix unit. TEPID or COLD means you left performance on the
table; `docs/GUIDE.md` explains the one-line `-march` fix.

## What the verdicts mean

| Verdict | Meaning |
|---|---|
| **HOT** | SME/SME2 present (the matrix-tile unit) |
| **WARM** | i8mm matrix instructions present (`smmla`) |
| **TEPID** | dot-product only (`sdot`), no matrix unit |
| **COLD** | no matrix and no dot-product kernels |
| **UNKNOWN** | too little of `.text` decoded to judge; coldpath refuses to assert an absence it cannot back up |
