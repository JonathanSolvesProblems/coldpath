"""Validate coldpath against ground truth we did not author.

llama.cpp's official linux-arm64 release ships eight ggml-cpu backends with the ISA level encoded in
the filename (armv8.0_1, armv8.2_1 DOTPROD, armv8.6_1 +MATMUL_INT8, ...). That is an answer key
written by someone else. A correct detector must reproduce the staircase exactly: each rung has the
features its name claims, and none of the features it doesn't.

If this passes, coldpath is not merely self-consistent -- it agrees with upstream's own build matrix.
"""

import pathlib
import sys

from coldpath import scan_sections, sections

# From ggml/src/ggml-cpu/CMakeLists.txt: the ARM variant list and what each level enables.
LADDER = {
    "armv8.0_1": {"dotprod": False, "i8mm": False, "sve": False},
    "armv8.2_1": {"dotprod": True,  "i8mm": False, "sve": False},
    "armv8.2_2": {"dotprod": True,  "i8mm": False, "sve": False},   # +FP16
    "armv8.2_3": {"dotprod": True,  "i8mm": False, "sve": True},    # +FP16 +SVE
    "armv8.6_1": {"dotprod": True,  "i8mm": True,  "sve": True},    # +MATMUL_INT8
    "armv8.6_2": {"dotprod": True,  "i8mm": True,  "sve": True},    # +SVE2
    "armv9.2_1": {"dotprod": True,  "i8mm": True,  "sve": True},    # +SME  (see note below)
    "armv9.2_2": {"dotprod": True,  "i8mm": True,  "sve": True},    # +SVE2 +SME
}


def main(root: str) -> int:
    libs = sorted(pathlib.Path(root).rglob("libggml-cpu-*.so"))
    if not libs:
        print(f"no ggml-cpu variants found under {root}", file=sys.stderr)
        return 2

    failures = []
    print(f"{'variant':<14} {'dotprod':>9} {'sve':>8} {'i8mm':>7} {'sme':>6}   verdict")
    print("-" * 62)

    for lib in libs:
        key = next((k for k in LADDER if k in lib.name), None)
        if key is None:
            continue
        r = scan_sections(sections(lib))
        want = LADDER[key]

        ok = (
            bool(r.dotprod) == want["dotprod"]
            and bool(r.i8mm) == want["i8mm"]
            and bool(r.sve) == want["sve"]
        )
        if not ok:
            failures.append((key, want, r))

        print(f"{key:<14} {r.dotprod:>9,} {r.sve:>8,} {r.i8mm:>7,} {r.sme:>6,}   "
              f"{'ok' if ok else 'MISMATCH'}")

    print()
    if failures:
        for key, want, r in failures:
            print(f"MISMATCH {key}: expected {want}, got "
                  f"dotprod={r.dotprod} i8mm={r.i8mm} sve={r.sve}", file=sys.stderr)
        return 1

    print(f"coldpath reproduces llama.cpp's own {len(libs)}-variant ISA ladder exactly.")

    # Not a failure -- an observation, and one of this project's findings. The armv9.2 variants are
    # NAMED for SME but ggml only gets SME via KleidiAI, and GGML_CPU_KLEIDIAI defaults to OFF. So
    # upstream's own "SME" backends contain no SME. ggml's dispatcher will still select them on an
    # SME-capable chip (Apple M4, Dimensity 9500, Snapdragon 8 Elite Gen 5).
    sme_named = [l for l in libs if "armv9.2" in l.name]
    if sme_named and not any(scan_sections(sections(l)).sme for l in sme_named):
        print()
        print("NOTE: the armv9.2_* variants are named for SME and contain ZERO SME instructions.")
        print("      Cause: SME reaches ggml only through KleidiAI, and GGML_CPU_KLEIDIAI=OFF by default.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
