# Scan scoreboard

Which shipped Arm AI binaries actually use the chip's matrix hardware. Every row is an **official,
unmodified release binary** scanned with `coldpath`, at 100% decode coverage (so COLD/zero rows are real
absences, not truncation). Reproduce any row with `coldpath <binary>`; the Windows-on-Arm rows are
re-checked against the latest release on every push by [`.github/workflows/test.yml`](.github/workflows/test.yml).

Verdicts: **HOT** = SME matrix, **WARM** = i8mm matrix, **TEPID** = dot-product only, **COLD** = nothing.

| binary (official release) | verdict | SME | i8mm | dotprod | sha256 |
|---|---|---:|---:|---:|---|
| ONNX Runtime 1.27.0, linux aarch64 wheel | **HOT** | 469 | 800 | 1,642 | `9275ef52` |
| ONNX Runtime 1.27.0, macOS arm64 dylib | **HOT** | 283 | 160 | 1,615 | n/a |
| ExecuTorch 1.3.1, aarch64 wheel | **HOT** | 771 | 960 | 3,185 | `9208f5cf` |
| llama.cpp b10344, linux-arm64 (best of 8 variants) | WARM | 0 | 402 | 1,253 | `807564f5` |
| llama.cpp b10344, win-arm64 | WARM | 0 | 244 | 1,052 | `b9a0dd0e` |
| Ollama v0.31.2, linux-arm64 (best variant) | WARM | 0 | 384 | 1,110 | n/a |
| **Ollama v0.31.2 – v0.32.7 (all 8 stable), win-arm64** | **COLD** | **0** | **0** | **0** | `536ada0d` (v0.31.2) |

<sub>*macOS row sha truncated for display; run locally to pin.</sub>

## What the scoreboard shows

- **Same dependency, opposite default.** ONNX Runtime and ExecuTorch ship real SME2 kernels by default;
  ggml (llama.cpp, Ollama) ships **zero SME on every OS**, because `GGML_CPU_KLEIDIAI` is off by default.
  Both depend on the same KleidiAI.
- **A shipping cold binary.** Ollama's Windows-on-Arm build executes no matrix or dot-product instructions
  at all, across all 8 stable releases tested. Fix + measured recovery: [`examples/ollama-fix/`](examples/ollama-fix/).
- **The tool discriminates.** HOT/WARM/COLD verdicts across the same ecosystem prove a zero is a real
  absence, not a broken detector.

## Reproduce

```bash
pip install coldpath
# a wheel, straight from PyPI's mirror:
pip download --no-deps onnxruntime -d /tmp/ort && coldpath /tmp/ort/*.whl
# the cold one:
curl -LO https://github.com/ollama/ollama/releases/download/v0.31.2/ollama-windows-arm64.zip
coldpath ollama-windows-arm64.zip
```
