"""Detect which Arm matrix/dot-product instructions an AArch64 binary can actually execute.

ggml/llama.cpp select their Arm kernels at COMPILE time, not run time. If the build used the wrong
-march, the fast path is not merely unused: it is absent from the binary. So the presence or absence
of these instructions in .text is decisive, and provable without running anything.

Detection is on register operands, not capstone instruction groups. Capstone 5.0.7 decodes SVE and
SME correctly but leaves insn.groups empty for both, so group-based detection silently reports zero.
"""

import sys
import pathlib
from collections import Counter

import capstone
from capstone import arm64

# Integer/bf16 matmul and dot-product mnemonics. These are the instructions that make int8/int4
# LLM inference fast on Arm. Same mnemonics exist in NEON and SVE forms; we split them by operand
# register class below, since a NEON smmla and an SVE smmla require different -march features.
I8MM = {"smmla", "ummla", "usmmla"}
BF16 = {"bfmmla", "bfdot", "bfmlalb", "bfmlalt"}
DOTPROD = {"sdot", "udot", "usdot", "sudot"}

# SME streaming-mode control. Presence of these is sufficient to prove SME was compiled in.
SME_CTRL = {"smstart", "smstop"}


def _reg_classes(insn, md):
    """Return the set of register classes used: 'v' (NEON), 'z'/'p' (SVE), 'za' (SME)."""
    classes = set()
    for op in insn.operands:
        if op.type != arm64.ARM64_OP_REG:
            continue
        name = md.reg_name(op.reg) or ""
        if name.startswith("za"):       # zas0, zad0, zab0... SME ZA tiles
            classes.add("za")
        elif name.startswith("z"):      # z0..z31, SVE vectors
            classes.add("z")
        elif name.startswith("p") and name[1:].isdigit():   # p0..p15, SVE predicates
            classes.add("p")
        elif name.startswith("v"):      # v0..v31, NEON
            classes.add("v")
    return classes


def executable_sections(path: pathlib.Path):
    """Yield (name, vaddr, bytes) for each executable section. Handles ELF and PE."""
    head = path.read_bytes()[:4]

    if head[:4] == b"\x7fELF":
        from elftools.elf.elffile import ELFFile
        with path.open("rb") as fh:
            elf = ELFFile(fh)
            if elf.get_machine_arch() != "AArch64":
                return
            for sec in elf.iter_sections():
                if sec["sh_flags"] & 0x4:  # SHF_EXECINSTR
                    yield sec.name, sec["sh_addr"], sec.data()

    elif head[:2] == b"MZ":
        import pefile
        pe = pefile.PE(str(path), fast_load=True)
        if pe.FILE_HEADER.Machine != 0xAA64:  # IMAGE_FILE_MACHINE_ARM64
            return
        base = pe.OPTIONAL_HEADER.ImageBase
        for sec in pe.sections:
            if sec.Characteristics & 0x20000000:  # IMAGE_SCN_MEM_EXECUTE
                yield (sec.Name.rstrip(b"\x00").decode(errors="replace"),
                       base + sec.VirtualAddress, sec.get_data())


def scan(path: pathlib.Path) -> dict:
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = True

    counts = Counter()
    total = 0
    decoded_bytes = 0
    section_bytes = 0

    for _name, vaddr, blob in executable_sections(path):
        section_bytes += len(blob)
        for insn in md.disasm(blob, vaddr):
            total += 1
            decoded_bytes = max(decoded_bytes, insn.address - vaddr + insn.size)

            m = insn.mnemonic
            cls = _reg_classes(insn, md)

            # SME first: any ZA-tile touch, or streaming-mode control, proves SME is compiled in.
            if "za" in cls or m in SME_CTRL or (m == "zero" and "za" in insn.op_str):
                counts["sme"] += 1
                if m.endswith(("mopa", "mops")):
                    counts["sme_mopa"] += 1
                continue

            sve = bool(cls & {"z", "p"})

            if m in I8MM:
                counts["i8mm_sve" if sve else "i8mm_neon"] += 1
            elif m in BF16:
                counts["bf16_sve" if sve else "bf16_neon"] += 1
            elif m in DOTPROD:
                counts["dotprod_sve" if sve else "dotprod_neon"] += 1
            elif sve:
                counts["sve_other"] += 1

    coverage = decoded_bytes / section_bytes if section_bytes else 0.0
    return {"path": path, "total": total, "counts": counts, "coverage": coverage}


ROWS = [
    ("SME (ZA tile)",  ["sme"]),
    ("  of which MOPA outer-product", ["sme_mopa"]),
    ("i8mm  SVE",      ["i8mm_sve"]),
    ("i8mm  NEON",     ["i8mm_neon"]),
    ("bf16  SVE",      ["bf16_sve"]),
    ("bf16  NEON",     ["bf16_neon"]),
    ("dotprod SVE",    ["dotprod_sve"]),
    ("dotprod NEON",   ["dotprod_neon"]),
    ("SVE (other)",    ["sve_other"]),
]


def main(paths):
    for p in paths:
        path = pathlib.Path(p)
        r = scan(path)
        if r["total"] == 0:
            print(f"{path.name}: not an AArch64 binary, skipped")
            continue

        c = r["counts"]
        matmul = c["sme"] + c["i8mm_sve"] + c["i8mm_neon"]
        verdict = "HAS MATRIX UNITS" if matmul else "NO MATRIX INSTRUCTIONS"

        print(f"\n{path.name}")
        print(f"  {r['total']:,} instructions, {r['coverage']*100:.1f}% of .text decoded  ->  {verdict}")
        for label, keys in ROWS:
            n = sum(c[k] for k in keys)
            print(f"    {'ok  ' if n else '--  '} {label:<32} {n:>8,}")


if __name__ == "__main__":
    main(sys.argv[1:])
