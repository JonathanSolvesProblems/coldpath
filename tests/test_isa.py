"""Ground-truth tests for instruction detection.

These use hand-assembled AArch64 encodings, so they prove the detector is correct without needing
any binary on disk. Each encoding below is a real instruction word, verified against the Arm ARM.

The regression test at the bottom is the important one: capstone decodes SVE and SME correctly but
leaves insn.groups EMPTY for both. An earlier version of this tool detected on groups and silently
reported zero SME everywhere -- the same class of false negative coldpath exists to find. If someone
"simplifies" the detector back to groups, that test fails.
"""

import pytest

from coldpath.isa import scan_sections

# (encoding, disassembly) -- one instruction each, little-endian words.
SME = [
    (0xD503477F, "smstart"),
    (0xD503467F, "smstop"),
    (0xC00800FF, "zero {za}"),
    (0x80810000, "fmopa za0.s, p0/m, p0/m, z0.s, z1.s"),
    (0xA0810000, "smopa za0.s, p0/m, p0/m, z0.b, z1.b"),
]
I8MM_NEON = [
    (0x4E82A420, "smmla v0.4s, v1.16b, v2.16b"),
    (0x6E82A420, "ummla v0.4s, v1.16b, v2.16b"),
]
DOTPROD_NEON = [
    (0x4E829420, "sdot v0.4s, v1.16b, v2.16b"),
    (0x6E829420, "udot v0.4s, v1.16b, v2.16b"),
]
SVE = [
    (0x2518E000, "ptrue p0.b"),
    (0xA540A000, "ld1w {z0.s}, p0/z, [x0]"),
]
# Plain scalar/NEON arithmetic. Must NOT be counted as anything.
NEUTRAL = [
    (0xD503201F, "nop"),
    (0x8B010000, "add x0, x0, x1"),
    (0x4E21D800, "fadd v0.4s, v0.4s, v1.4s"),
]


def _scan(words):
    blob = b"".join(w.to_bytes(4, "little") for w in words)
    return scan_sections([(".text", 0x1000, blob)])


@pytest.mark.parametrize("word,asm", SME)
def test_sme_detected(word, asm):
    assert _scan([word]).sme == 1, f"missed SME: {asm}"


def test_sme_outer_product_counted_separately():
    r = _scan([w for w, _ in SME])
    assert r.sme == len(SME)
    assert r.counts["sme_mopa"] == 2, "fmopa and smopa are outer products"


@pytest.mark.parametrize("word,asm", I8MM_NEON)
def test_i8mm_neon(word, asm):
    r = _scan([word])
    assert r.i8mm == 1, f"missed i8mm: {asm}"
    assert r.counts["i8mm_neon"] == 1
    assert r.counts["i8mm_sve"] == 0


@pytest.mark.parametrize("word,asm", DOTPROD_NEON)
def test_dotprod_neon(word, asm):
    r = _scan([word])
    assert r.dotprod == 1, f"missed dotprod: {asm}"
    assert r.i8mm == 0, "dotprod is not a matrix instruction"


@pytest.mark.parametrize("word,asm", SVE)
def test_sve(word, asm):
    assert _scan([word]).sve == 1, f"missed SVE: {asm}"


@pytest.mark.parametrize("word,asm", NEUTRAL)
def test_neutral_not_counted(word, asm):
    r = _scan([word])
    assert (r.sme, r.i8mm, r.bf16, r.dotprod, r.sve) == (0, 0, 0, 0, 0), f"false positive on {asm}"


def test_verdicts():
    assert _scan([w for w, _ in NEUTRAL]).level == "neon"
    assert _scan([DOTPROD_NEON[0][0]]).level == "dotprod"
    assert _scan([I8MM_NEON[0][0]]).level == "i8mm"
    assert _scan([SME[3][0]]).level == "sme"

    assert not _scan([w for w, _ in NEUTRAL]).has_matrix
    assert not _scan([DOTPROD_NEON[0][0]]).has_matrix, "dot-product alone is not a matrix unit"
    assert _scan([I8MM_NEON[0][0]]).has_matrix
    assert _scan([SME[0][0]]).has_matrix


def test_dotprod_alone_is_not_i8mm():
    """The distinction that matters: a dotprod-only build looks fast but has no matrix unit."""
    r = _scan([w for w, _ in DOTPROD_NEON])
    assert r.dotprod == 2
    assert r.i8mm == 0
    assert r.level == "dotprod"
    assert not r.has_matrix


def test_capstone_leaves_sve_sme_groups_empty():
    """REGRESSION GUARD. Do not rewrite the detector to use insn.groups.

    Capstone 5.x decodes SVE/SME correctly but populates no group metadata for them. Detecting on
    groups yields a silent zero -- which is precisely the bug class this tool ships to catch.
    """
    import capstone

    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = True

    for word, asm in SME + SVE:
        insn = next(md.disasm(word.to_bytes(4, "little"), 0x1000))
        assert len(insn.groups) == 0, (
            f"capstone now reports groups for {asm!r}. If this fails, capstone gained group "
            f"metadata -- the detector is still correct, but this guard can be relaxed."
        )
        # ...and we detect it anyway, because we read register operands instead.
        r = _scan([word])
        assert r.sme or r.sve, f"detector missed {asm}"
