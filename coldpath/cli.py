"""coldpath -- prove whether an Arm binary can actually use the chip's matrix hardware."""

from __future__ import annotations

import argparse
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


def _expand(path: pathlib.Path, tmp: pathlib.Path) -> list[pathlib.Path]:
    """Files to scan. Archives are unpacked; directories are walked."""
    if path.is_dir():
        return [p for p in sorted(path.rglob("*")) if p.is_file() and looks_like_binary(p)]

    if path.suffix.lower() in ARCHIVES or "".join(path.suffixes[-2:]).lower() in {".tar.gz", ".tar.zst", ".tar.xz"}:
        dest = tmp / path.name
        dest.mkdir(parents=True, exist_ok=True)
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as z:
                    z.extractall(dest)
            elif path.suffix.lower() == ".zst":
                import zstandard
                with path.open("rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as r:
                    with tarfile.open(fileobj=r, mode="r|") as t:
                        t.extractall(dest)
            else:
                with tarfile.open(path) as t:
                    t.extractall(dest)
        except Exception as e:  # noqa: BLE001 - a corrupt archive shouldn't crash a scan of 40 others
            print(f"  ! could not unpack {path.name}: {e}", file=sys.stderr)
            return []
        return [p for p in sorted(dest.rglob("*")) if p.is_file() and looks_like_binary(p)]

    return [path] if looks_like_binary(path) else []


def _label(found: pathlib.Path, given: pathlib.Path, tmp: pathlib.Path) -> str:
    """Name a result unambiguously. Two files called ggml-cpu.dll is the whole point of this tool."""
    try:
        if tmp in found.parents:                       # came out of an archive
            inner = found.relative_to(tmp / given.name)
            return f"{given.name}::{inner.as_posix()}"
        if given.is_dir():
            return (given.name / found.relative_to(given)).as_posix()
    except ValueError:
        pass
    # A plain file argument: keep enough path to tell two same-named binaries apart.
    parts = found.parts
    return "/".join(parts[-3:]) if len(parts) >= 3 else found.as_posix()


def _verdict(r: Result) -> tuple[str, str]:
    if r.sme:
        return "HOT", "SME"
    if r.i8mm:
        return "WARM", "i8mm"
    if r.dotprod:
        return "TEPID", "dotprod"
    return "COLD", "none"


ROWS = [
    ("SME  (ZA tile)", lambda r: r.sme),
    ("  outer-product (MOPA)", lambda r: r.counts["sme_mopa"]),
    ("i8mm (smmla)", lambda r: r.i8mm),
    ("bf16 (bfmmla/bfdot)", lambda r: r.bf16),
    ("dotprod (sdot)", lambda r: r.dotprod),
    ("SVE", lambda r: r.sve),
]


def report(name: str, r: Result, verbose: bool) -> None:
    verdict, best = _verdict(r)
    banner = {
        "COLD": "COLD PATH  -- 0 matrix instructions",
        "TEPID": "TEPID  -- dot-product only, no matrix unit",
        "WARM": "WARM  -- i8mm matrix, no SME",
        "HOT": "HOT  -- SME matrix engine",
    }[verdict]

    print(f"\n{name}")
    print(f"  {r.total:,} instructions   {banner}")
    if r.coverage < 0.95:
        print(f"  ! only {r.coverage*100:.1f}% of .text decoded -- treat counts as a lower bound")

    for label, get in ROWS:
        n = get(r)
        print(f"    {'ok  ' if n else '--  '} {label:<26} {n:>9,}")

    if verbose and verdict == "COLD":
        print("\n    This binary cannot execute a matrix or dot-product instruction on ANY Arm CPU,")
        print("    no matter what hardware it runs on. The kernels were not compiled in.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="coldpath",
        description="Prove whether an Arm binary can actually use the chip's matrix hardware.",
        epilog="Exit code is 1 if --require is not satisfied, so this works as a CI gate.",
    )
    ap.add_argument("paths", nargs="+", type=pathlib.Path,
                    help="binaries, directories, or archives (.whl, .apk, .tar.gz, .zip)")
    ap.add_argument("--require", choices=["sme", "i8mm", "bf16", "dotprod"], metavar="FEATURE",
                    help="fail (exit 1) if any scanned binary lacks FEATURE")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-q", "--quiet", action="store_true", help="only report failures")
    ap.add_argument("-V", "--version", action="version", version=f"coldpath {__version__}")
    args = ap.parse_args(argv)

    results: list[tuple[str, Result]] = []
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
                results.append((_label(f, p, tmp), scan_sections(secs)))

    if not results:
        print("coldpath: no AArch64 binaries found", file=sys.stderr)
        return 2

    failed = [n for n, r in results if args.require and not r.has(args.require)]

    if args.json:
        print(json.dumps({
            "version": __version__,
            "binaries": [{
                "name": n,
                "verdict": _verdict(r)[0],
                "instructions": r.total,
                "coverage": round(r.coverage, 4),
                "sme": r.sme, "sme_mopa": r.counts["sme_mopa"],
                "i8mm": r.i8mm, "bf16": r.bf16, "dotprod": r.dotprod, "sve": r.sve,
            } for n, r in results],
            "skipped_non_aarch64": skipped,
            "required": args.require,
            "failed": failed,
        }, indent=2))
    else:
        for n, r in results:
            if args.quiet and not (args.require and n in failed):
                continue
            report(n, r, verbose=not args.quiet)

    if args.require:
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
