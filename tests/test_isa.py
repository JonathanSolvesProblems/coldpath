"""Ground-truth tests for instruction detection, using hand-assembled AArch64 encodings.

These prove the detector is correct without needing any binary on disk. Each encoding is a real
instruction word verified against the Arm ARM. The suite also pins the three hardening properties a
skeptical Arm engineer would attack: SVE/SME are detected despite empty capstone groups, the sweep
resyncs through embedded data instead of truncating, and a single stray data word cannot flip a
verdict or fake a COLD.
"""

import pytest

from coldpath.isa import scan_sections, VERDICT_FLOOR

SME = [
    (0xD503477F, "smstart"),
    (0xD503467F, "smstop"),
    (0xC00800FF, "zero {za}"),
    (0x80810000, "fmopa za0.s, p0/m, p0/m, z0.s, z1.s"),
    (0xA0810000, "smopa za0.s, p0/m, p0/m, z0.b, z1.b"),
]
I8MM_NEON = [(0x4E82A420, "smmla v0.4s, v1.16b, v2.16b"), (0x6E82A420, "ummla ...")]
BF16_MATRIX_NEON = [(0x6E42EC20, "bfmmla v0.4s, v1.8h, v2.8h")]
BF16_DOT_NEON = [(0x2E42FC20, "bfdot v0.2s, v1.4h, v2.4h")]
DOTPROD_NEON = [(0x4E829420, "sdot v0.4s, v1.16b, v2.16b"), (0x6E829420, "udot ...")]
SVE = [(0x2518E000, "ptrue p0.b"), (0xA540A000, "ld1w {z0.s}, p0/z, [x0]")]
NEUTRAL = [(0xD503201F, "nop"), (0x8B010000, "add x0, x0, x1"), (0x4E21D800, "fadd v0.4s, ...")]
DATA = 0xFFFFFFFF  # a word capstone cannot decode; stands in for a literal-pool / jump-table entry


def _scan(words):
    blob = b"".join(w.to_bytes(4, "little") for w in words)
    return scan_sections([(".text", 0x1000, blob)])


# ---- raw detection of each family ----

@pytest.mark.parametrize("word,asm", SME)
def test_sme_detected(word, asm):
    assert _scan([word]).sme == 1, f"missed SME: {asm}"


def test_mopa_and_ctrl_are_anchors():
    assert _scan([SME[0][0]]).sme_anchor == 1   # smstart
    assert _scan([SME[3][0]]).sme_anchor == 1   # fmopa (outer product)
    assert _scan([SME[2][0]]).sme_anchor == 0   # bare "zero {za}" is not an anchor


@pytest.mark.parametrize("word,asm", I8MM_NEON)
def test_i8mm_neon(word, asm):
    r = _scan([word])
    assert r.i8mm == 1 and r.counts["i8mm_neon"] == 1 and r.counts["i8mm_sve"] == 0


@pytest.mark.parametrize("word,asm", DOTPROD_NEON)
def test_dotprod_is_not_matrix(word, asm):
    r = _scan([word])
    assert r.dotprod == 1 and r.i8mm == 0


@pytest.mark.parametrize("word,asm", SVE)
def test_sve(word, asm):
    assert _scan([word]).sve == 1, f"missed SVE: {asm}"


@pytest.mark.parametrize("word,asm", NEUTRAL)
def test_neutral_not_counted(word, asm):
    r = _scan([word])
    assert (r.sme, r.i8mm, r.bf16, r.dotprod, r.sve) == (0, 0, 0, 0, 0), f"false positive on {asm}"


# ---- verdict + corroboration floor ----

def test_verdicts_need_the_floor():
    # A single matmul instruction does NOT decide a verdict (could be one data word).
    assert _scan([I8MM_NEON[0][0]]).verdict == "COLD"
    # Two do.
    assert _scan([I8MM_NEON[0][0], I8MM_NEON[1][0]]).verdict == "WARM"
    assert _scan(DOTPROD_ALL := [w for w, _ in DOTPROD_NEON]).verdict == "TEPID"


def test_single_za_word_cannot_fake_hot():
    """A lone ZA-shaped word (~1-in-476 in random data) must not flip the verdict to HOT/SME."""
    r = _scan([SME[2][0]])          # one "zero {za}", no anchor
    assert r.sme == 1
    assert r.sme_ok is False
    assert r.verdict == "COLD"


def test_single_anchor_is_enough_for_sme():
    assert _scan([SME[0][0], NEUTRAL[0][0]]).sme_ok is True   # smstart is a hard anchor
    assert _scan([SME[3][0], NEUTRAL[0][0]]).sme_ok is True   # fmopa is a hard anchor


def test_dotprod_alone_is_not_matrix():
    r = _scan([w for w, _ in DOTPROD_NEON])
    assert r.dotprod == 2 and r.i8mm == 0
    assert r.has_matrix is False and r.verdict == "TEPID"


def test_bf16_matmul_is_warm():
    """bfmmla is a bf16 matrix multiply: it must count as a matrix kernel (WARM), like i8mm."""
    r = _scan([w for w, _ in BF16_MATRIX_NEON] * 2)   # two, to clear the corroboration floor
    assert r.bf16 == 2 and r.bf16_matrix == 2 and r.i8mm == 0
    assert r.has_matrix is True and r.verdict == "WARM"


def test_bf16_dot_is_not_matrix():
    """bfdot is a bf16 dot product, not a matrix op: it must NOT reach WARM on its own."""
    r = _scan([w for w, _ in BF16_DOT_NEON] * 2)
    assert r.bf16 == 2 and r.bf16_matrix == 0
    assert r.has_matrix is False and r.verdict != "WARM"


# ---- the hardening the adversarial review demanded ----

def test_resync_through_embedded_data():
    """One undecodable data word must NOT truncate the scan (the false-COLD bug).

    Realistic ratio: a single literal-pool word among plenty of real code. The two smmla AFTER the
    data word must still be counted -- the default (halting) sweep would report only the one before.
    """
    neutral = [w for w, _ in NEUTRAL]
    r = _scan(neutral * 20 + [I8MM_NEON[0][0], DATA, I8MM_NEON[0][0], I8MM_NEON[1][0]] + neutral * 20)
    assert r.i8mm == 3, "sweep halted on data instead of resyncing -> false COLD"
    assert r.data_words == 1
    assert r.verdict == "WARM"


def test_low_coverage_refuses_to_assert_cold():
    """If a section is mostly unreadable, the verdict is UNKNOWN, never a false COLD."""
    r = _scan([DATA] * 200 + [NEUTRAL[0][0]])
    assert r.coverage < 0.1
    assert r.verdict == "UNKNOWN"


def test_clean_code_has_full_coverage():
    r = _scan([w for w, _ in NEUTRAL] * 40)
    assert r.coverage == 1.0
    assert r.verdict == "COLD"          # real code, genuinely no matrix instructions


def test_capstone_leaves_sve_sme_groups_empty():
    """REGRESSION GUARD: do not rewrite detection to use insn.groups.

    Capstone 5.x decodes SVE/SME correctly but reports no group metadata for them, so group-based
    detection silently returns zero -- the bug class coldpath ships to catch.
    """
    import capstone

    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    for word, asm in SME + SVE:
        insn = next(md.disasm(word.to_bytes(4, "little"), 0x1000))
        assert len(insn.groups) == 0, f"capstone now reports groups for {asm!r}; guard can relax"
        assert _scan([word]).sme or _scan([word]).sve, f"detector missed {asm}"


def test_floor_constant_is_sane():
    assert VERDICT_FLOOR >= 2
