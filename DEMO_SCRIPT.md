# coldpath — 75-second demo script

Written **before** the build, as the focus gate. If a feature doesn't appear in this script,
it doesn't ship. (Hackathon rule 25. Salon and Memex both died of scope; this is the tourniquet.)

**Target: 75 seconds. Ceiling: 90.** Arm allows 3 minutes. Winners use half of it.

---

### [0:00–0:12] The hook — one screen, one number

> *Screen: a Snapdragon X laptop running Ollama. Tokens crawling out.*

**"This is Ollama, on an Arm laptop. It's slow, and nobody could tell you why.**
**So I disassembled it."**

> *Cut to terminal:*
> ```
> $ coldpath ollama/ggml-cpu.dll
>
>   COLD PATH — 0 matrix instructions
>     SME 0    i8mm 0    dotprod 0
> ```

**"Zero. Ollama's Arm build cannot execute a single matrix instruction."**

---

### [0:12–0:30] Why that's not obvious, and why nobody caught it

> *Screen: the ggml CMake snippet, one line highlighted.*

**"Arm's matrix kernels are chosen at compile time, not run time. Get the build flag wrong and the
fast path isn't slow — it's absent. The binary physically cannot run it, and nothing warns you.**

**There has never been a tool that checks. Arm's own answer is 'open a profiler and read the assembly.'"**

---

### [0:30–0:45] The proof it's a real defect, not my bug

> *Screen: the two-row table, big.*

| Windows-on-Arm | i8mm | dotprod |
|---|---|---|
| llama.cpp | 244 | 1,052 |
| **Ollama** | **0** | **0** |

**"Same OS. Same chip. Same ggml — Ollama builds llama.cpp from source. llama.cpp gets its matrix
kernels. Ollama gets nothing. It's one missing CMake flag."**

---

### [0:45–1:00] What it costs — the number

> *Screen: the CI run, live, on a real Arm64 runner. Two bars.*

**"On an Arm Neoverse server, that flag is worth [N]x on prompt processing. Here's the benchmark —
running in GitHub Actions, on free Arm64 hardware, so you can re-run it yourself by clicking a link."**

*(Do NOT fabricate N. Measure it. If it's 1.4x, say 1.4x.)*

---

### [1:00–1:15] The fix, and the close

> *Screen: the GitHub Action failing a PR, red X.*

**"So coldpath ships as a CI gate. If your build would ship a cold path, it fails the PR — before your
users get the slow binary.**

**I found this in Ollama. I also found that nothing in the ggml ecosystem ships SME at all — not on any
OS, not on any device — including on the chips Arm built SME for.**

**The patches are upstream. The tool is MIT. It's one command."**

> *Final screen:*
> ```
> pip install coldpath && coldpath <any arm64 binary>
> ```

---

## What is deliberately NOT in this demo

Cut, on purpose, even though each was tempting:

- The 8-variant ISA-ladder validation table → **README**, not the demo. It's how I know the tool is
  right, but it's methodology, and methodology is not a hook.
- The capstone `insn.groups` false-negative war story → **README**. Delicious, but it's a footnote.
- The Android/S24 scan → **README + a screenshot in the writeup.** It dilutes a Cloud-track demo.
- The full ecosystem corpus table (PyTorch, vLLM, MLX, llamafile…) → **README.**
- Any explanation of what SME/i8mm/dotprod *are*. If a judge needs that, the table already told them
  what they need: one row has numbers, the other has zeroes.

## The one sentence, if I only get one

> **Ollama on an Arm laptop cannot execute a single matrix instruction, and until now there was no way
> to know that about any binary.**
