#!/usr/bin/env bash
# Reproduce coldpath's headline finding in ~30 seconds, on any OS, with no Arm hardware.
# It downloads two official Windows-on-Arm release binaries and scans them.
set -euo pipefail

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

PY="$(command -v python3 || command -v python)"

echo "==> installing coldpath"
"$PY" -m pip install --quiet "coldpath @ git+https://github.com/JonathanSolvesProblems/coldpath"

echo
echo "==> Ollama's official Windows-on-Arm build"
curl -sL -o ollama.zip https://github.com/ollama/ollama/releases/download/v0.31.2/ollama-windows-arm64.zip
unzip -q ollama.zip -d ollama
"$PY" -m coldpath ollama/lib/ollama/ggml-cpu.dll

echo
echo "==> llama.cpp's official Windows-on-Arm build (same OS, same ggml source)"
LV="$(curl -s https://api.github.com/repos/ggml-org/llama.cpp/releases/latest | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["tag_name"])')"
curl -sL -o lcpp.zip "https://github.com/ggml-org/llama.cpp/releases/download/${LV}/llama-${LV}-bin-win-cpu-arm64.zip"
unzip -q lcpp.zip -d lcpp
"$PY" -m coldpath lcpp/ggml-cpu.dll

echo
echo "==> Same OS. Same ggml. One has the matrix kernels, one ships COLD."
echo "    Root cause + one-line fix: examples/ollama-fix/"
echo "    What it costs (6.97x on Neoverse N2): the 'What the cold path costs' GitHub Action."
