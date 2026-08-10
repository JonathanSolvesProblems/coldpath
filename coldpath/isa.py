"""Which Arm matrix / dot-product instructions can this binary actually execute?

ggml, XNNPACK and most Arm kernel libraries select their fast paths at COMPILE time. If the build
used the wrong -march, the fast path is not merely unused: the intrinsics are #if-compiled out, so
the instructions are ABSENT from the binary. Disassembling .text is therefore decisive, and needs no
Arm hardware and no profiler.

Two properties make a static linear sweep SOUND on AArch64, where it would be unsound on x86:

  1. Fixed 4-byte instructions on a 4-byte grid. A mis-decoded data word (literal pool, jump table)
     is local to its own 4 bytes and can never cascade into the following code. We enable capstone
     SKIPDATA with a 4-byte stride, so the sweep RESYNCS on the next word instead of halting. Without
     this, one word of embedded data would truncate the scan and could report a false COLD.

  2. Detection is on register operands, not capstone instruction groups. Capstone decodes SVE and SME
     correctly but leaves insn.groups empty for both, so group-based detection silently reports zero
     -- the exact false-negative class this tool exists to catch.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import capstone

# Integer / bfloat matmul and dot-product mnemonics. Each exists in a NEON form (V registers) and an
# SVE form (Z registers) that require different -march features, so we split by register class.
I8MM = frozenset({"smmla", "ummla", "usmmla"})
BF16 = frozenset({"bfmmla", "bfdot", "bfmlalb", "bfmlalt"})
DOTPROD = frozenset({"sdot", "udot", "usdot", "sudot"})
SME_CTRL = frozenset({"smstart", "smstop"})

# A feature counts toward the verdict only at or above this many static occurrences. A single word of
# embedded data can, with tiny probability, decode as a valid matmul instruction (~1-in-22k for i8mm,
# ~1-in-476 for an SME outer-product operand). Real matmul kernels contain dozens to hundreds, so a
# floor of 2 removes the single-data-word false positive without ever hiding a real kernel.
VERDICT_FLOOR = 2

_ZP_REG = re.compile(r"(?:^|[\s,{[])[zp]\d")  # an SVE Z or P register in the operand string
_SME_TOKEN = "za"                              # SME ZA-tile register: za, za0.., zas0.. (no other token has it)


@dataclass
class Result:
    counts: Counter = field(default_factory=lambda: Counter())
    real_insns: int = 0
    data_words: int = 0
    _sec_cov: list = field(default_factory=list)   # (real, total) per executable section

    # ---- raw feature counts (what is physically in the binary) ----
    @property
    def sme(self) -> int: return self.counts["sme"]
    @property
    def i8mm(self) -> int: return self.counts["i8mm_neon"] + self.counts["i8mm_sve"]
    @property
    def bf16(self) -> int: return self.counts["bf16_neon"] + self.counts["bf16_sve"]
    @property
    def dotprod(self) -> int: return self.counts["dotprod_neon"] + self.counts["dotprod_sve"]
    @property
    def sve(self) -> int:
        return (self.counts["sve_other"] + self.counts["i8mm_sve"]
                + self.counts["bf16_sve"] + self.counts["dotprod_sve"])
    @property
    def sme_anchor(self) -> int:
        """SME evidence that is near-impossible in random data: streaming control + outer products."""
        return self.counts["sme_ctrl"] + self.counts["sme_mopa"]

    # ---- coverage: fraction of executable words that were real instructions (not skipped data) ----
    @property
    def coverage(self) -> float:
        tot = self.real_insns + self.data_words
        return self.real_insns / tot if tot else 0.0

    @property
    def min_section_coverage(self) -> float:
        """Worst per-section coverage, over sections big enough to matter (>= 64 words)."""
        sizable = [(r, t) for (r, t) in self._sec_cov if t >= 64]
        return min((r / t for r, t in sizable), default=self.coverage)

    # ---- verdict (applies the corroboration floor; raw counts above are unfiltered) ----
    def _passes(self, feature: str) -> bool:
        return getattr(self, feature) >= VERDICT_FLOOR

    @property
    def sme_ok(self) -> bool:
        # SME needs either a hard anchor (smstart/smstop/MOPA) or >= 2 ZA-tile touches.
        return self.sme_anchor >= 1 or self.sme >= VERDICT_FLOOR

    @property
    def has_matrix(self) -> bool:
        return self.sme_ok or self._passes("i8mm")

    @property
    def verdict(self) -> str:
        # If we could not read the code (mostly data / unreadable), refuse to assert absence.
        if self.min_section_coverage < 0.90:
            return "UNKNOWN"
        if self.sme_ok:
            return "HOT"
        if self._passes("i8mm"):
            return "WARM"
        if self._passes("dotprod"):
            return "TEPID"
        return "COLD"

    def has(self, feature: str) -> bool:
        if feature == "sme":
            return self.sme_ok
        return self._passes(feature)


def _make_engine() -> capstone.Cs:
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = False                       # mnemonic + op_str is enough, and much faster
    md.skipdata = True                      # do not halt on data ...
    md.skipdata_setup = (".long", lambda buf, size, off, ud: 4, None)  # ... resync every 4 bytes
    return md


def scan_sections(sections) -> Result:
    """sections: iterable of (name, vaddr, bytes) for executable sections."""
    md = _make_engine()
    r = Result()

    for _name, vaddr, blob in sections:
        blob = blob[: len(blob) - (len(blob) % 4)]  # whole 4-byte words only
        sec_real = 0
        sec_data = 0

        for _addr, _size, mnem, ops in md.disasm_lite(blob, vaddr):
            if mnem == ".long":             # a word capstone could not decode: data, skipped
                sec_data += 1
                continue
            sec_real += 1

            # SME first: ZA tile operand, streaming control, or an outer-product mnemonic.
            is_mopa = mnem.endswith(("mopa", "mops"))
            if _SME_TOKEN in ops or mnem in SME_CTRL or is_mopa:
                r.counts["sme"] += 1
                if mnem in SME_CTRL:
                    r.counts["sme_ctrl"] += 1
                if is_mopa:
                    r.counts["sme_mopa"] += 1
                continue

            sve = bool(_ZP_REG.search(ops))
            if mnem in I8MM:
                r.counts["i8mm_sve" if sve else "i8mm_neon"] += 1
            elif mnem in BF16:
                r.counts["bf16_sve" if sve else "bf16_neon"] += 1
            elif mnem in DOTPROD:
                r.counts["dotprod_sve" if sve else "dotprod_neon"] += 1
            elif sve:
                r.counts["sve_other"] += 1

        r.real_insns += sec_real
        r.data_words += sec_data
        r._sec_cov.append((sec_real, sec_real + sec_data))

    return r
