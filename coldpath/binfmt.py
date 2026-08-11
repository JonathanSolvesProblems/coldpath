"""Pull executable sections out of AArch64 binaries: ELF, PE, and Mach-O.

Mach-O matters more than it looks. Apple Silicon is by far the largest population of SME-capable
hardware in existence, so a tool that reports on SME and cannot read a .dylib has a hole exactly
where the interesting answer is.
"""

from __future__ import annotations

import pathlib
import struct
from typing import Iterator

Section = tuple[str, int, bytes]

# Mach-O
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
CPU_TYPE_ARM64 = 0x0100000C
LC_SEGMENT_64 = 0x19
S_ATTR_PURE_INSTRUCTIONS = 0x80000000
S_ATTR_SOME_INSTRUCTIONS = 0x00000400


class NotAArch64(Exception):
    """The file is a valid binary, but not for AArch64. Not an error -- just not our business."""


def _elf(path: pathlib.Path) -> Iterator[Section]:
    from elftools.elf.elffile import ELFFile

    with path.open("rb") as fh:
        elf = ELFFile(fh)
        if elf.get_machine_arch() != "AArch64":
            raise NotAArch64(elf.get_machine_arch())
        for sec in elf.iter_sections():
            if sec["sh_flags"] & 0x4:  # SHF_EXECINSTR
                yield sec.name, sec["sh_addr"], sec.data()


def _pe(path: pathlib.Path) -> Iterator[Section]:
    import pefile

    # Parse from an in-memory copy and close explicitly. pefile mmaps the file by default, which on
    # Windows keeps it locked and makes a later TemporaryDirectory teardown fail (WinError 32) when the
    # binary came out of an archive we unpacked. Reading bytes + close() avoids the lock entirely.
    pe = pefile.PE(data=path.read_bytes(), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0xAA64:  # IMAGE_FILE_MACHINE_ARM64
            raise NotAArch64(hex(pe.FILE_HEADER.Machine))
        base = pe.OPTIONAL_HEADER.ImageBase
        out = []
        for sec in pe.sections:
            if sec.Characteristics & 0x20000000:  # IMAGE_SCN_MEM_EXECUTE
                name = sec.Name.rstrip(b"\x00").decode(errors="replace")
                out.append((name, base + sec.VirtualAddress, sec.get_data()))
        return out
    finally:
        pe.close()


def _macho_slice(data: bytes, off: int) -> Iterator[Section]:
    """Parse one thin Mach-O image starting at `off`."""
    magic = struct.unpack_from("<I", data, off)[0]
    if magic not in (MH_MAGIC_64, MH_CIGAM_64):
        raise NotAArch64(hex(magic))
    endian = "<" if magic == MH_MAGIC_64 else ">"

    cputype, _cpusub, _ft, ncmds, _sz, _fl, _res = struct.unpack_from(endian + "7I", data, off + 4)
    if cputype != CPU_TYPE_ARM64:
        raise NotAArch64(hex(cputype))

    pos = off + 32  # mach_header_64 is 32 bytes
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from(endian + "2I", data, pos)
        if cmd == LC_SEGMENT_64:
            nsects = struct.unpack_from(endian + "I", data, pos + 64)[0]
            spos = pos + 72  # segment_command_64 is 72 bytes
            for _s in range(nsects):
                name = data[spos:spos + 16].rstrip(b"\x00").decode(errors="replace")
                addr, size = struct.unpack_from(endian + "2Q", data, spos + 32)
                offset, _align, _reloff, _nreloc, flags = struct.unpack_from(endian + "5I", data, spos + 48)
                if flags & (S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS):
                    yield name, addr, data[off + offset: off + offset + size]
                spos += 80  # section_64 is 80 bytes
        pos += cmdsize


def _macho(path: pathlib.Path) -> Iterator[Section]:
    data = path.read_bytes()
    magic = struct.unpack_from(">I", data, 0)[0]

    if magic in (FAT_MAGIC, FAT_CIGAM):
        # Universal binary. Find the arm64 slice and parse only that.
        nfat = struct.unpack_from(">I", data, 4)[0]
        for i in range(nfat):
            cputype, _sub, offset, _size, _align = struct.unpack_from(">5I", data, 8 + i * 20)
            if cputype == CPU_TYPE_ARM64:
                yield from _macho_slice(data, offset)
                return
        raise NotAArch64("fat binary with no arm64 slice")

    yield from _macho_slice(data, 0)


def sections(path: pathlib.Path) -> list[Section]:
    """Executable sections of an AArch64 binary. Raises NotAArch64 if it isn't one."""
    head = path.read_bytes()[:4]

    if head[:4] == b"\x7fELF":
        return list(_elf(path))
    if head[:2] == b"MZ":
        return list(_pe(path))
    if struct.unpack(">I", head)[0] in (MH_MAGIC_64, MH_CIGAM_64, FAT_MAGIC, FAT_CIGAM):
        return list(_macho(path))

    raise NotAArch64("unrecognised file format")


def looks_like_binary(path: pathlib.Path) -> bool:
    """Cheap pre-filter so we don't try to disassemble READMEs and .py files."""
    try:
        with path.open("rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    if len(head) < 4:
        return False
    return (
        head[:4] == b"\x7fELF"
        or head[:2] == b"MZ"
        or struct.unpack(">I", head)[0] in (MH_MAGIC_64, MH_CIGAM_64, FAT_MAGIC, FAT_CIGAM)
    )
