"""Minimal, dependency-free ELF reader for release artifact inspection.

Only the information the dynamic loader itself uses is read: the ELF header,
the program headers (PT_LOAD, PT_INTERP, PT_DYNAMIC), the dynamic section and
the version-needed table it points to. Section headers are ignored because
stripped or unusual binaries do not need them to load.
"""

from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass, field

ELF_MAGIC = b"\x7fELF"

MACHINE_NAMES = {
    3: "i386",
    40: "arm",
    62: "x86_64",
    183: "aarch64",
    243: "riscv",
}

TYPE_NAMES = {1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}

PT_LOAD = 1
PT_DYNAMIC = 2
PT_INTERP = 3

DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_STRSZ = 10
DT_SONAME = 14
DT_RPATH = 15
DT_RUNPATH = 29
DT_VERNEED = 0x6FFFFFFE
DT_VERNEEDNUM = 0x6FFFFFFF

# Upper bounds that keep a malformed file from causing unbounded work.
MAX_PROGRAM_HEADERS = 4096
MAX_DYNAMIC_ENTRIES = 65536
MAX_VERNEED_ENTRIES = 4096


class ElfError(Exception):
    """Raised when a file is not a well-formed ELF object."""


@dataclass
class ElfInfo:
    elf_class: str  # "ELF32" or "ELF64"
    byte_order: str  # "little" or "big"
    machine: str
    elf_type: str
    interpreter: str | None = None
    needed: list[str] = field(default_factory=list)
    soname: str | None = None
    rpath: list[str] | None = None
    runpath: list[str] | None = None
    # Library file name -> version names required from it (e.g. GLIBC_2.38).
    version_requirements: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_dynamic_executable(self) -> bool:
        """True for programs started through the dynamic loader (incl. PIE)."""
        return self.interpreter is not None and self.elf_type in ("EXEC", "DYN")


def is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == ELF_MAGIC
    except OSError:
        return False


def read_elf(path: str) -> ElfInfo:
    with open(path, "rb") as handle:
        try:
            data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError as exc:  # empty file
            raise ElfError("file is empty") from exc
        try:
            return _parse(data)
        finally:
            data.close()


def parse_elf_bytes(data: bytes) -> ElfInfo:
    return _parse(data)


def _unpack(fmt: str, data, offset: int) -> tuple:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise ElfError(f"truncated ELF data at offset {offset}")
    return struct.unpack_from(fmt, data, offset)


def _cstring(data, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit:
        raise ElfError(f"string offset {offset} out of range")
    end = data.find(b"\x00", offset, limit)
    if end < 0:
        raise ElfError(f"unterminated string at offset {offset}")
    return bytes(data[offset:end]).decode("utf-8", errors="replace")


def _parse(data) -> ElfInfo:
    if len(data) < 16 or bytes(data[:4]) != ELF_MAGIC:
        raise ElfError("missing ELF magic")
    ei_class, ei_data = data[4], data[5]
    if ei_class not in (1, 2):
        raise ElfError(f"invalid ELF class {ei_class}")
    if ei_data not in (1, 2):
        raise ElfError(f"invalid ELF data encoding {ei_data}")
    is64 = ei_class == 2
    end = "<" if ei_data == 1 else ">"

    if is64:
        (e_type, e_machine, _ver, _entry, e_phoff, _shoff, _flags, _ehsize,
         e_phentsize, e_phnum, _shentsize, _shnum, _shstrndx) = _unpack(
            end + "HHIQQQIHHHHHH", data, 16)
        ph_fmt = end + "IIQQQQQQ"
        dyn_fmt = end + "qQ"
    else:
        (e_type, e_machine, _ver, _entry, e_phoff, _shoff, _flags, _ehsize,
         e_phentsize, e_phnum, _shentsize, _shnum, _shstrndx) = _unpack(
            end + "HHIIIIIHHHHHH", data, 16)
        ph_fmt = end + "IIIIIIII"
        dyn_fmt = end + "iI"

    if e_phnum > MAX_PROGRAM_HEADERS:
        raise ElfError(f"too many program headers ({e_phnum})")
    if e_phnum and e_phentsize < struct.calcsize(ph_fmt):
        raise ElfError(f"program header entry size {e_phentsize} too small")

    loads = []  # (vaddr, offset, filesz)
    interp_seg = None
    dynamic_seg = None
    for index in range(e_phnum):
        fields = _unpack(ph_fmt, data, e_phoff + index * e_phentsize)
        if is64:
            p_type, _pflags, p_offset, p_vaddr, _paddr, p_filesz, _memsz, _align = fields
        else:
            p_type, p_offset, p_vaddr, _paddr, p_filesz, _memsz, _pflags, _align = fields
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_INTERP:
            interp_seg = (p_offset, p_filesz)
        elif p_type == PT_DYNAMIC:
            dynamic_seg = (p_offset, p_filesz)

    info = ElfInfo(
        elf_class="ELF64" if is64 else "ELF32",
        byte_order="little" if ei_data == 1 else "big",
        machine=MACHINE_NAMES.get(e_machine, f"unknown({e_machine})"),
        elf_type=TYPE_NAMES.get(e_type, f"unknown({e_type})"),
    )

    if interp_seg is not None:
        offset, size = interp_seg
        if offset + size > len(data):
            raise ElfError("PT_INTERP extends past end of file")
        info.interpreter = _cstring(data, offset, offset + size)

    if dynamic_seg is None:
        return info

    def to_offset(vaddr: int) -> int:
        for seg_vaddr, seg_offset, seg_size in loads:
            if seg_vaddr <= vaddr < seg_vaddr + seg_size:
                return seg_offset + (vaddr - seg_vaddr)
        raise ElfError(f"virtual address {vaddr:#x} is not in a loadable segment")

    entries = []
    dyn_offset, dyn_size = dynamic_seg
    entry_size = struct.calcsize(dyn_fmt)
    for index in range(min(dyn_size // entry_size, MAX_DYNAMIC_ENTRIES)):
        tag, value = _unpack(dyn_fmt, data, dyn_offset + index * entry_size)
        if tag == DT_NULL:
            break
        entries.append((tag, value))

    tags = {}
    for tag, value in entries:
        tags.setdefault(tag, value)
    if DT_STRTAB not in tags:
        raise ElfError("dynamic section has no DT_STRTAB")
    strtab = to_offset(tags[DT_STRTAB])
    strtab_end = min(len(data), strtab + tags.get(DT_STRSZ, len(data) - strtab))

    def dynstr(index: int) -> str:
        return _cstring(data, strtab + index, strtab_end)

    for tag, value in entries:
        if tag == DT_NEEDED:
            info.needed.append(dynstr(value))
        elif tag == DT_SONAME:
            info.soname = dynstr(value)
        elif tag == DT_RPATH:
            info.rpath = dynstr(value).split(":")
        elif tag == DT_RUNPATH:
            info.runpath = dynstr(value).split(":")

    if DT_VERNEED in tags:
        count = tags.get(DT_VERNEEDNUM, 0)
        if count > MAX_VERNEED_ENTRIES:
            raise ElfError(f"too many version-needed entries ({count})")
        cursor = to_offset(tags[DT_VERNEED])
        for _ in range(count):
            _version, aux_count, file_index, aux_offset, next_offset = _unpack(
                end + "HHIII", data, cursor)
            names = info.version_requirements.setdefault(dynstr(file_index), [])
            aux = cursor + aux_offset
            for _ in range(min(aux_count, MAX_VERNEED_ENTRIES)):
                _hash, _vflags, _other, name_index, aux_next = _unpack(
                    end + "IHHII", data, aux)
                names.append(dynstr(name_index))
                if aux_next == 0:
                    break
                aux += aux_next
            if next_offset == 0:
                break
            cursor += next_offset

    return info
