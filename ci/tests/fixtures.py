"""Builders for synthetic ELF files and release archives used by the tests."""

from __future__ import annotations

import io
import struct
import tarfile

EM_X86_64 = 62
EM_AARCH64 = 183
ET_EXEC = 2
ET_DYN = 3
INTERP = "/lib64/ld-linux-x86-64.so.2"


def build_elf(*, machine=EM_X86_64, elf_type=ET_DYN, interp=None, needed=(), soname=None,
              rpath=None, runpath=None, verneed=None) -> bytes:
    """Build a minimal little-endian ELF64 object the loader-facing parser can read.

    A single PT_LOAD maps the whole file at virtual address 0, so file offsets
    and virtual addresses coincide.
    """
    strtab = bytearray(b"\x00")

    def add(text):
        offset = len(strtab)
        strtab.extend(text.encode() + b"\x00")
        return offset

    dynamic = [(1, add(name)) for name in needed]  # DT_NEEDED
    if soname is not None:
        dynamic.append((14, add(soname)))  # DT_SONAME
    if rpath is not None:
        dynamic.append((15, add(rpath)))  # DT_RPATH
    if runpath is not None:
        dynamic.append((29, add(runpath)))  # DT_RUNPATH
    verneed_entries = [(add(lib), [add(n) for n in names])
                       for lib, names in (verneed or {}).items()]

    phnum = 2 + (1 if interp else 0)
    offset = 64 + 56 * phnum
    interp_bytes = interp.encode() + b"\x00" if interp else b""
    interp_offset = offset
    offset += len(interp_bytes)
    strtab_offset = offset
    offset += len(strtab)
    offset = (offset + 7) & ~7

    verneed_bytes = bytearray()
    for index, (file_index, names) in enumerate(verneed_entries):
        last = index == len(verneed_entries) - 1
        next_offset = 0 if last else 16 + 16 * len(names)
        verneed_bytes += struct.pack("<HHIII", 1, len(names), file_index, 16, next_offset)
        for aux_index, name_index in enumerate(names):
            aux_next = 0 if aux_index == len(names) - 1 else 16
            verneed_bytes += struct.pack("<IHHII", 0, 0, 2 + aux_index, name_index, aux_next)
    verneed_offset = offset
    offset += len(verneed_bytes)

    dynamic += [(5, strtab_offset), (10, len(strtab))]  # DT_STRTAB, DT_STRSZ
    if verneed_entries:
        dynamic += [(0x6FFFFFFE, verneed_offset), (0x6FFFFFFF, len(verneed_entries))]
    dynamic.append((0, 0))
    dynamic_offset = offset
    dynamic_bytes = b"".join(struct.pack("<qQ", tag, value) for tag, value in dynamic)
    total = dynamic_offset + len(dynamic_bytes)

    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    header = ident + struct.pack("<HHIQQQIHHHHHH", elf_type, machine, 1, 0, 64, 0, 0,
                                 64, 56, phnum, 64, 0, 0)
    phdrs = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, total, total, 0x1000)  # PT_LOAD
    if interp:
        phdrs += struct.pack("<IIQQQQQQ", 3, 4, interp_offset, interp_offset, interp_offset,
                             len(interp_bytes), len(interp_bytes), 1)  # PT_INTERP
    phdrs += struct.pack("<IIQQQQQQ", 2, 6, dynamic_offset, dynamic_offset, dynamic_offset,
                         len(dynamic_bytes), len(dynamic_bytes), 8)  # PT_DYNAMIC

    data = bytearray(header + phdrs)
    data += interp_bytes
    data += strtab
    data += bytes(verneed_offset - len(data))
    data += verneed_bytes
    data += dynamic_bytes
    assert len(data) == total
    return bytes(data)


def executable(needed, **kwargs) -> dict:
    kwargs.setdefault("interp", INTERP)
    kwargs.setdefault("verneed", {"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.34"]})
    return {"data": build_elf(needed=needed, **kwargs), "mode": 0o755}


def library(soname, needed=(), **kwargs) -> dict:
    return {"data": build_elf(soname=soname, needed=needed, **kwargs), "mode": 0o755}


def symlink(target) -> dict:
    return {"symlink": target}


def good_entries() -> dict:
    """An archive layout that satisfies ci/artifact-contract.json statically."""
    core = ["libllama.so.1", "libggml.so.1", "libggml-base.so.1",
            "libstdc++.so.6", "libgcc_s.so.1", "libc.so.6"]
    entries = {
        "llama-cli": executable(core + ["libssl.so.3", "libcrypto.so.3"]),
        "llama-server": executable(core + ["libssl.so.3", "libcrypto.so.3"]),
        "llama-bench": executable(core, verneed={
            "libc.so.6": ["GLIBC_2.38"], "libstdc++.so.6": ["GLIBCXX_3.4.32"]}),
    }
    libraries = {
        "libllama": ["libggml.so.1", "libggml-base.so.1", "libc.so.6"],
        "libggml": ["libggml-cpu.so.1", "libggml-hip.so.1", "libggml-base.so.1"],
        "libggml-base": ["libm.so.6", "libc.so.6"],
        "libggml-cpu": ["libggml-base.so.1", "libomp.so", "libc.so.6"],
        "libggml-hip": ["libggml-base.so.1", "libhipblas.so.3", "librocblas.so.5",
                        "libamdhip64.so.7", "libc.so.6", "ld-linux-x86-64.so.2"],
    }
    for stem, needed in libraries.items():
        entries[f"{stem}.so.1.0.0"] = library(f"{stem}.so.1", needed)
        entries[f"{stem}.so.1"] = symlink(f"{stem}.so.1.0.0")
        entries[f"{stem}.so"] = symlink(f"{stem}.so.1")
    return entries


def write_archive(path, entries, *, prefix="./", extra_members=()) -> str:
    """Write entries (name -> spec) as a .tar.xz shaped like `tar -C build/bin .`."""
    with tarfile.open(path, "w:xz") as tar:
        root = tarfile.TarInfo(prefix.rstrip("/") or ".")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        for name, spec in entries.items():
            info = tarfile.TarInfo(prefix + name)
            if "symlink" in spec:
                info.type = tarfile.SYMTYPE
                info.linkname = spec["symlink"]
                info.mode = 0o777
                tar.addfile(info)
            elif "hardlink" in spec:
                info.type = tarfile.LNKTYPE
                info.linkname = prefix + spec["hardlink"]
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.mode = spec.get("mode", 0o644)
                info.size = len(spec["data"])
                tar.addfile(info, io.BytesIO(spec["data"]))
        for info, data in extra_members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path
