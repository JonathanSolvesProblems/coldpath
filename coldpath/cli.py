"""coldpath -- prove whether an Arm binary can actually use the chip's matrix hardware."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import tarfile
import tempfile
import zipfile

from . import __version__
from .binfmt import NotAArch64, looks_like_binary, sections
from .isa import Result, scan_sections

ARCHIVES = {".whl", ".apk", ".aar", ".zip", ".jar", ".tar", ".tgz", ".gz", ".zst", ".xz", ".bz2"}

BANNERS = {
    "HOT":     "HOT   -- SME matrix engine",
    "WARM":    "WARM  -- i8mm matrix instructions",
    "TEPID":   "TEPID -- dot-product only, no matrix unit",
    "COLD":    "COLD  -- 0 matrix instructions",
    "UNKNOWN": "UNKNOWN -- too little of .text was decodable to judge",
}


def _expand(path: pathlib.Path, tmp: pathlib.Path) -> list[pathlib.Path]:
    """Files to scan. Archives are unpacked; directories are walked."""
    if path.is_dir():
        return [p for p in sorted(path.rglob("*")) if p.is_file() and looks_like_binary(p)]

    suffixes = "".join(path.suffixes[-2:]).lower()
    if path.suffix.lower() in ARCHIVES or suffixes in {".tar.gz", ".tar.zst", ".tar.xz", ".tar.bz2"}:
        dest = tmp / path.name
        dest.mkdir(parents=True, exist_ok=True)
        # coldpath's whole job is opening untrusted third-party archives, so extraction must be
        # path-traversal safe. tarfile's 'data' filter (3.12+, backported to security releases) blocks
        # '..' and absolute paths; ZipFile already sanitises member paths.
        tar_kw = {"filter": "data"} if sys.version_info >= (3, 12) else {}
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as z:
                    z.extractall(dest)
            elif path.suffix.lower() == ".zst":
                import zstandard
                with path.open("rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as reader:
                    with tarfile.open(fileobj=reader, mode="r|") as t:
                        t.extractall(dest, **tar_kw)
            else:
                with tarfile.open(path) as t:
                    t.extractall(dest, **tar_kw)
        except Exception as e:  # noqa: BLE001 - a corrupt archive must not abort a 40-binary scan
            print(f"  ! could not unpack {path.name}: {e}", file=sys.stderr)
            return []
        return [p for p in sorted(dest.rglob("*")) if p.is_file() and looks_like_binary(p)]

    return [path] if looks_like_binary(path) else []


def _label(found: pathlib.Path, given: pathlib.Path, tmp: pathlib.Path) -> str:
    """Name a result unambiguously. Two files called ggml-cpu.dll is the whole point of this tool."""
    try:
        if tmp in found.parents:
            return f"{given.name}::{found.relative_to(tmp / given.name).as_posix()}"
        if given.is_dir():
            return (given.name / found.relative_to(given)).as_posix()
    except ValueError:
        pass
    parts = found.parts
    return "/".join(parts[-3:]) if len(parts) >= 3 else found.as_posix()


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


ROWS = [
    ("SME  (ZA tile)", lambda r: r.sme),
    ("  streaming/MOPA anchor", lambda r: r.sme_anchor),
    ("i8mm (smmla)", lambda r: r.i8mm),
    ("bf16 (bfmmla/bfdot)", lambda r: r.bf16),
    ("dotprod (sdot)", lambda r: r.dotprod),
    ("SVE", lambda r: r.sve),
]


def report(name: str, r: Result, sha: str | None, verbose: bool) -> None:
    print(f"\n{name}")
    meta = f"  {r.real_insns:,} instructions   {r.coverage*100:.1f}% decoded   {BANNERS[r.verdict]}"
    print(meta)
    if sha:
        print(f"  sha256:{sha[:16]}")
    if r.verdict == "UNKNOWN":
        print(f"  ! only {r.min_section_coverage*100:.1f}% of the smallest code section decoded; "
              f"cannot assert presence or absence")
    for label, get in ROWS:
        n = get(r)
        print(f"    {'ok  ' if n else '--  '} {label:<26} {n:>9,}")
    if verbose and r.verdict == "COLD":
        print("\n    This build ships zero matrix and dot-product instructions. On any Arm CPU it")
        print("    runs LLM matmul in scalar/NEON only -- the fast kernels were not compiled in.")


def _result_json(name: str, r: Result, sha: str) -> dict:
    return {
        "name": name, "sha256": sha, "verdict": r.verdict,
        "instructions": r.real_insns, "coverage": round(r.coverage, 4),
        "min_section_coverage": round(r.min_section_coverage, 4),
        "sme": r.sme, "sme_anchor": r.sme_anchor, "sme_mopa": r.counts["sme_mopa"],
        "i8mm": r.i8mm, "bf16": r.bf16, "dotprod": r.dotprod, "sve": r.sve,
        "has_matrix": r.has_matrix,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="coldpath",
        description="Prove whether an Arm binary can actually use the chip's matrix hardware.",
        epilog="Exit code is 1 if --require is not satisfied, so this works as a CI gate.",
    )
    ap.add_argument("paths", nargs="+", type=pathlib.Path,
                    help="binaries, directories, or archives (.whl, .apk, .tar.gz, .zip)")
    ap.add_argument("--require", choices=["sme", "i8mm", "bf16", "dotprod"], metavar="FEATURE",
                    help="fail (exit 1) unless FEATURE is present")
    ap.add_argument("--any", action="store_true",
                    help="with --require: pass if ANY scanned binary has FEATURE (use for a "
                         "multi-variant release that dlopens the best of libggml-cpu-*.so at runtime)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-q", "--quiet", action="store_true", help="only report failures")
    ap.add_argument("-V", "--version", action="version", version=f"coldpath {__version__}")
    args = ap.parse_args(argv)

    results: list[tuple[str, Result, str]] = []
    skipped = 0

    with tempfile.TemporaryDirectory(prefix="coldpath-") as td:
        tmp = pathlib.Path(td)
        for p in args.paths:
            if not p.exists():
                print(f"coldpath: {p}: no such file", file=sys.stderr)
                return 2
            for f in _expand(p, tmp):
                try:
                    secs = sections(f)
                except NotAArch64:
                    skipped += 1
                    continue
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {f.name}: {e}", file=sys.stderr)
                    skipped += 1
                    continue
                if not secs:
                    skipped += 1
                    continue
                results.append((_label(f, p, tmp), scan_sections(secs), _sha256(f)))

    if not results:
        print("coldpath: no AArch64 binaries found", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "version": __version__,
            "binaries": [_result_json(n, r, s) for n, r, s in results],
            "skipped_non_aarch64": skipped,
            "required": args.require,
        }, indent=2))
    else:
        for n, r, s in results:
            if args.quiet and not (args.require and not r.has(args.require)):
                continue
            report(n, r, s, verbose=not args.quiet)

    if args.require:
        satisfied = [(n, r) for n, r, _ in results if r.has(args.require)]
        if args.any:
            ok = len(satisfied) > 0
            if not ok:
                print(f"\ncoldpath: no scanned binary has '{args.require}'", file=sys.stderr)
            return 0 if ok else 1
        failed = [n for n, r, _ in results if not r.has(args.require)]
        if failed:
            print(f"\ncoldpath: {len(failed)} of {len(results)} binaries lack '{args.require}':",
                  file=sys.stderr)
            for n in failed:
                print(f"  - {n}", file=sys.stderr)
            return 1
        print(f"\ncoldpath: all {len(results)} binaries have '{args.require}'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
