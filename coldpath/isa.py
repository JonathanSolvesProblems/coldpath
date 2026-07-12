"""Which Arm instructions can this binary actually execute?

ggml, XNNPACK, and most Arm kernel libraries select their fast paths at COMPILE time. If the build
used the wrong -march, the fast path is not merely unused: it is absent from the binary. So the
presence of these instructions in .text is decisive, and provable without running anything.

Detection is on REGISTER OPERANDS, not mnemonics and not capstone's instruction groups. Capstone
decodes SVE and SME correctly but leaves insn.groups empty for both, so group-based detection
silently reports zero -- the exact class of false negative this tool exists to catch.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import capstone
from capstone import arm64

# Integer and bfloat matmul / dot-product mnemonics. Each exists in both a NEON form (V registers)
# and an SVE form (Z registers) that require DIFFERENT -march features, so we split them by the
# register class they operate on. Counting mnemonics alone would conflate the two.
I8MM = frozenset({"smmla", "ummla", "usmmla"})
BF16 = frozenset({"bfmmla", "bfdot", "bfmlalb", "bfmlalt"})
DOTPROD = frozenset({"sdot", "udot", "usdot", "sudot"})

# SME streaming-mode control. Presence proves SME was compiled in, even without an outer product.
SME_CTRL = frozenset({"smstart", "smstop"})

# Ordered from most to least capable. A binary's "level" is the best thing it can actually do.
LEVELS = ["sme", "i8mm", "bf16", "dotprod", "neon"]


@dataclass
class Result:
    """What a binary can execute. Counts are static instruction counts, not dynamic frequencies."""

    counts: Counter = field(default_factory=Counter)
    total: int = 0
    decoded_bytes: int = 0
    text_bytes: int = 0

    @property
    def coverage(self) -> float:
        """Fraction of executable bytes we successfully disassembled. Low = don't trust the result."""
        return self.decoded_bytes / self.text_bytes if self.text_bytes else 0.0

    @property
    def sme(self) -> int:
        return self.counts["sme"]

    @property
    def i8mm(self) -> int:
        return self.counts["i8mm_neon"] + self.counts["i8mm_sve"]

    @property
    def bf16(self) -> int:
        return self.counts["bf16_neon"] + self.counts["bf16_sve"]

    @property
    def dotprod(self) -> int:
        return self.counts["dotprod_neon"] + self.counts["dotprod_sve"]

    @property
    def sve(self) -> int:
        return (self.counts["sve_other"] + self.counts["i8mm_sve"]
                + self.counts["bf16_sve"] + self.counts["dotprod_sve"])

    @property
    def has_matrix(self) -> bool:
        """Can this binary do a matrix multiply in hardware at all?"""
        return bool(self.sme or self.i8mm)

    @property
    def level(self) -> str:
        """The best matmul capability actually present."""
        for name in LEVELS:
            if name == "neon":
                return "neon"
            if getattr(self, name):
                return name
        return "neon"

    def has(self, feature: str) -> bool:
        return bool(getattr(self, feature, 0))


def _classes(insn, md) -> set[str]:
    """Register classes touched: 'za' (SME tile), 'z'/'p' (SVE), 'v' (NEON)."""
    out = set()
    for op in insn.operands:
        if op.type != arm64.ARM64_OP_REG:
            continue
        name = md.reg_name(op.reg) or ""
        if name.startswith("za"):                       # zas0, zad0, zab0 ... SME ZA tiles
            out.add("za")
        elif name.startswith("z"):                      # z0..z31, SVE vectors
            out.add("z")
        elif name.startswith("p") and name[1:].isdigit():  # p0..p15, SVE predicates
            out.add("p")
        elif name.startswith("v"):                      # v0..v31, NEON
            out.add("v")
    return out


def scan_sections(sections) -> Result:
    """sections: iterable of (name, vaddr, bytes) for executable sections."""
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = True

    r = Result()

    for _name, vaddr, blob in sections:
        r.text_bytes += len(blob)
        end = 0
        for insn in md.disasm(blob, vaddr):
            r.total += 1
            end = insn.address - vaddr + insn.size

            m = insn.mnemonic
            cls = _classes(insn, md)

            # SME first: any ZA-tile touch, streaming-mode control, or outer product.
            if "za" in cls or m in SME_CTRL or (m == "zero" and "za" in insn.op_str):
                r.counts["sme"] += 1
                if m.endswith(("mopa", "mops")):
                    r.counts["sme_mopa"] += 1
                continue

            sve = bool(cls & {"z", "p"})

            if m in I8MM:
                r.counts["i8mm_sve" if sve else "i8mm_neon"] += 1
            elif m in BF16:
                r.counts["bf16_sve" if sve else "bf16_neon"] += 1
            elif m in DOTPROD:
                r.counts["dotprod_sve" if sve else "dotprod_neon"] += 1
            elif sve:
                r.counts["sve_other"] += 1

        r.decoded_bytes += end

    return r
