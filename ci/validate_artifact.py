#!/usr/bin/env python3
"""Validate llama.cpp Strix Halo release archives against the distribution contract.

Subcommands:
  validate  Inspect one release archive and write a machine-readable report.
  gate      Check that every expected archive is present and has a passing
            report bound to its exact bytes before it may be published.

Only the Python standard library is used. See docs/release-validation.md.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import lzma
import os
import platform
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elfinfo  # noqa: E402

REPORT_SCHEMA_VERSION = 1
CONTRACT_SCHEMA_VERSION = 1

PASS = "pass"
FAIL = "fail"
WARN = "warn"  # advisory finding; never blocks a release
NOT_RUN = "not_run"

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_INCOMPLETE = "incomplete"

EXIT_CODES = {RESULT_PASS: 0, RESULT_FAIL: 1, RESULT_INCOMPLETE: 3}

MAX_SYMLINK_DEPTH = 40
OUTPUT_EXCERPT_BYTES = 2048
SYSTEM_LIBRARY_DIRS = (
    "/lib", "/lib64", "/usr/lib", "/usr/lib64",
    "/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
)


class ContractError(Exception):
    """The contract file is missing, malformed or unsupported."""


@dataclass
class Check:
    id: str
    mandatory: bool
    status: str
    summary: str
    details: dict = field(default_factory=dict)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------

CONTRACT_KEYS = (
    "archive_name_template", "upstream_tag_pattern", "rocm_variants", "elf",
    "required_executables", "required_libraries", "required_linkage",
    "external_dependencies", "rpath_allowed_absolute", "runtime", "release_gate",
)


def load_contract(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            contract = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ContractError(f"cannot read contract {path}: {exc}") from exc
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ContractError(
            f"unsupported contract schema_version {contract.get('schema_version')!r}")
    missing = [key for key in CONTRACT_KEYS if key not in contract]
    if missing:
        raise ContractError(f"contract is missing keys: {', '.join(missing)}")
    return contract


def expected_archive_name(contract: dict, upstream_tag: str, rocm_variant: str) -> str:
    return contract["archive_name_template"].format(
        upstream_tag=upstream_tag, rocm_variant=rocm_variant)


def soname_regex(stem: str) -> re.Pattern:
    return re.compile(r"^" + re.escape(stem) + r"\.so(\.[0-9]+)*$")


# --------------------------------------------------------------------------
# Archive model
# --------------------------------------------------------------------------

@dataclass
class Archive:
    members: dict  # normalized name -> tarfile.TarInfo
    root: str | None = None  # extraction directory once extracted
    elves: dict = field(default_factory=dict)  # file name -> ElfInfo
    elf_errors: dict = field(default_factory=dict)  # file name -> error text
    other_files: list = field(default_factory=list)

    def resolve(self, name: str):
        """Follow symlinks inside the archive. Returns (name, TarInfo) or None."""
        for _ in range(MAX_SYMLINK_DEPTH):
            member = self.members.get(name)
            if member is None:
                return None
            if not member.issym():
                return name, member
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(name), member.linkname))
            if target.startswith("../") or target == ".." or target.startswith("/"):
                return None
            name = target
        return None

    def resolve_elf(self, name: str):
        resolved = self.resolve(name)
        if resolved is None or not resolved[1].isfile():
            return None
        return resolved[0], self.elves.get(resolved[0])

    @property
    def executables(self) -> list:
        return sorted(n for n, e in self.elves.items() if e.is_dynamic_executable)

    @property
    def libraries(self) -> list:
        return sorted(n for n, e in self.elves.items() if not e.is_dynamic_executable)


def normalize_member_name(raw: str):
    """Return (normalized name, problem). The archive root itself maps to ''."""
    if raw.startswith("/"):
        return None, "absolute path"
    name = posixpath.normpath(raw)
    if name == ".":
        return "", None
    if name == ".." or name.startswith("../") or "/../" in f"/{name}/":
        return None, "parent directory traversal"
    return name, None


def member_kind(member: tarfile.TarInfo) -> str:
    if member.isfile():
        return "file"
    if member.isdir():
        return "dir"
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    return "special"


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def check_metadata(contract, archive_path, upstream_tag, rocm_variant) -> Check:
    problems = []
    filename = os.path.basename(archive_path)
    if not re.match(contract["upstream_tag_pattern"], upstream_tag or ""):
        problems.append(f"upstream tag {upstream_tag!r} does not match "
                        f"{contract['upstream_tag_pattern']}")
    if rocm_variant not in contract["rocm_variants"]:
        problems.append(f"ROCm variant {rocm_variant!r} is not one of "
                        f"{', '.join(contract['rocm_variants'])}")
    expected = expected_archive_name(contract, upstream_tag, rocm_variant)
    if filename != expected:
        problems.append(f"archive filename {filename!r} != expected {expected!r}")
    details = {"filename": filename, "expected_filename": expected,
               "upstream_tag": upstream_tag, "rocm_variant": rocm_variant}
    if problems:
        details["problems"] = problems
        return Check("metadata", True, FAIL, "; ".join(problems), details)
    return Check("metadata", True, PASS,
                 f"{filename} matches tag {upstream_tag} and ROCm {rocm_variant}", details)


def check_archive_integrity(archive_path):
    """Decompress every member fully so truncation or CRC errors surface."""
    members = []
    try:
        with tarfile.open(archive_path, "r:xz") as tar:
            for member in tar:
                members.append(member)
                if member.isfile():
                    handle = tar.extractfile(member)
                    while handle.read(1 << 20):
                        pass
    except (tarfile.TarError, lzma.LZMAError, EOFError, OSError, ValueError) as exc:
        return Check("archive-integrity", True, FAIL,
                     f"archive is not a readable .tar.xz: {type(exc).__name__}: {exc}",
                     {"error": str(exc)}), None
    file_count = sum(1 for m in members if m.isfile())
    if file_count == 0:
        return Check("archive-integrity", True, FAIL, "archive contains no files",
                     {"member_count": len(members)}), None
    return Check("archive-integrity", True, PASS,
                 f"decompressed {len(members)} members ({file_count} files) without error",
                 {"member_count": len(members), "file_count": file_count}), members


def check_safe_extraction(raw_members, archive_path, workdir):
    problems = []
    members = {}
    for member in raw_members:
        name, problem = normalize_member_name(member.name)
        if problem:
            problems.append(f"{member.name}: {problem}")
            continue
        if name == "":
            continue
        if name in members:
            problems.append(f"{name}: duplicate member")
        kind = member_kind(member)
        if kind not in ("file", "dir", "symlink"):
            problems.append(f"{name}: {kind} entries are not allowed")
        if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            problems.append(f"{name}: setuid/setgid/sticky bit set ({member.mode:#o})")
        if not member.issym() and member.mode & (stat.S_IWGRP | stat.S_IWOTH):
            problems.append(f"{name}: group/world writable ({member.mode:#o})")
        members[name] = member

    archive = Archive(members=members)
    for name, member in members.items():
        parent = posixpath.dirname(name)
        while parent:
            if parent in members and members[parent].issym():
                problems.append(f"{name}: parent {parent} is a symlink")
                break
            parent = posixpath.dirname(parent)
        if member.issym():
            if member.linkname.startswith("/"):
                problems.append(f"{name}: absolute symlink target {member.linkname}")
            elif archive.resolve(name) is None:
                problems.append(
                    f"{name}: symlink target {member.linkname} is missing, "
                    "outside the archive, or loops")

    if problems:
        return Check("safe-extraction", True, FAIL,
                     f"{len(problems)} unsafe archive entries", {"problems": problems}), None

    if not hasattr(tarfile, "data_filter"):
        return Check("safe-extraction", True, FAIL,
                     "this Python lacks tarfile extraction filters (need 3.12+)", {}), None
    root = os.path.join(workdir, "extracted")
    os.makedirs(root)
    try:
        with tarfile.open(archive_path, "r:xz") as tar:
            tar.extractall(root, filter="data")
    except (tarfile.TarError, OSError) as exc:
        return Check("safe-extraction", True, FAIL, f"extraction failed: {exc}",
                     {"error": str(exc)}), None
    archive.root = root
    return Check("safe-extraction", True, PASS,
                 "all entries are relative regular files, directories or "
                 "in-archive symlinks; extracted with the tarfile 'data' filter",
                 {"entries": len(members)}), archive


def scan_elves(archive: Archive) -> None:
    for name, member in sorted(archive.members.items()):
        if not member.isfile():
            continue
        path = os.path.join(archive.root, name)
        if not elfinfo.is_elf(path):
            archive.other_files.append(name)
            continue
        try:
            archive.elves[name] = elfinfo.read_elf(path)
        except (elfinfo.ElfError, OSError) as exc:
            archive.elf_errors[name] = str(exc)


def check_inventory(contract, archive: Archive) -> Check:
    problems = []
    found_executables = {}
    for name in contract["required_executables"]:
        resolved = archive.resolve_elf(name)
        if resolved is None or resolved[1] is None:
            problems.append(f"required executable {name} is missing or not an ELF file")
        elif not resolved[1].is_dynamic_executable:
            problems.append(f"required executable {name} is not a dynamic executable")
        else:
            found_executables[name] = resolved[0]

    found_libraries = {}
    for stem in contract["required_libraries"]:
        pattern = soname_regex(stem)
        matches = sorted(n for n, e in archive.elves.items()
                         if e.soname and pattern.match(e.soname))
        if not matches:
            problems.append(f"required library {stem} (SONAME {pattern.pattern}) is missing")
            continue
        soname = archive.elves[matches[0]].soname
        resolved = archive.resolve_elf(soname)
        if resolved is None or resolved[0] != matches[0]:
            problems.append(f"required library {stem}: no entry named {soname} "
                            f"resolving to {matches[0]}")
            continue
        found_libraries[stem] = {"file": matches[0], "soname": soname}

    details = {"required_executables": found_executables,
               "required_libraries": found_libraries}
    if problems:
        details["problems"] = problems
        return Check("inventory", True, FAIL, "; ".join(problems), details)
    return Check("inventory", True, PASS,
                 f"{len(found_executables)} required executables and "
                 f"{len(found_libraries)} required libraries present", details)


def check_permissions(contract, archive: Archive) -> Check:
    problems = []
    for name in archive.executables:
        mode = archive.members[name].mode
        if mode & 0o555 != 0o555:
            problems.append(f"executable {name} has mode {mode:#o}, needs r-x for all")
    for name in archive.libraries:
        mode = archive.members[name].mode
        if mode & 0o444 != 0o444:
            problems.append(f"library {name} has mode {mode:#o}, needs read for all")
    for name in contract["required_executables"]:
        resolved = archive.resolve(name)
        if resolved and resolved[1].isfile() and resolved[1].mode & 0o111 != 0o111:
            problems.append(f"required executable {name} is not executable")
    if problems:
        problems = sorted(set(problems))
        return Check("permissions", True, FAIL, f"{len(problems)} permission problems",
                     {"problems": problems})
    return Check("permissions", True, PASS,
                 f"{len(archive.executables)} executables are r-x for all; "
                 f"{len(archive.libraries)} libraries are readable",
                 {"executables": len(archive.executables),
                  "libraries": len(archive.libraries)})


def check_elf_architecture(contract, archive: Archive) -> Check:
    want = contract["elf"]
    problems = [f"{name}: unparsable ELF ({error})"
                for name, error in sorted(archive.elf_errors.items())]
    for name, info in sorted(archive.elves.items()):
        if info.elf_class != want["class"]:
            problems.append(f"{name}: class {info.elf_class} != {want['class']}")
        if info.byte_order != want["byte_order"]:
            problems.append(f"{name}: byte order {info.byte_order} != {want['byte_order']}")
        if info.machine != want["machine"]:
            problems.append(f"{name}: machine {info.machine} != {want['machine']}")
        if info.elf_type not in ("EXEC", "DYN"):
            problems.append(f"{name}: ELF type {info.elf_type} is not EXEC or DYN")
        if info.interpreter is not None and info.interpreter != want["interpreter"]:
            problems.append(f"{name}: interpreter {info.interpreter} != {want['interpreter']}")
    details = {"expected": want, "elf_files": len(archive.elves) + len(archive.elf_errors)}
    if problems:
        details["problems"] = problems
        return Check("elf-architecture", True, FAIL, f"{len(problems)} ELF problems", details)
    return Check("elf-architecture", True, PASS,
                 f"{len(archive.elves)} ELF files are {want['class']} "
                 f"{want['byte_order']}-endian {want['machine']}", details)


def classify_external(contract, name: str):
    for group in contract["external_dependencies"]:
        if any(re.match(pattern, name) for pattern in group["patterns"]):
            return group["provided_by"]
    return None


def check_dependency_closure(contract, archive: Archive):
    """Every DT_NEEDED entry is bundled or an allowed external dependency."""
    problems = []
    bundled = {}
    external = {}
    for name, info in sorted(archive.elves.items()):
        for needed in info.needed:
            resolved = archive.resolve_elf(needed)
            if resolved is not None:
                target, target_info = resolved
                bundled.setdefault(needed, target)
                if target_info is not None and target_info.soname not in (None, needed):
                    problems.append(f"{name}: needs {needed} but {target} has SONAME "
                                    f"{target_info.soname}")
                continue
            provider = classify_external(contract, needed)
            if provider is None:
                problems.append(f"{name}: needs {needed}, which is neither bundled "
                                "nor an allowed external dependency")
                continue
            entry = external.setdefault(needed, {"provided_by": provider, "required_by": []})
            entry["required_by"].append(name)

    details = {"bundled": bundled, "external": external}
    if problems:
        details["problems"] = problems
        return Check("dependency-closure", True, FAIL,
                     f"{len(problems)} unresolved dependencies", details)
    return Check("dependency-closure", True, PASS,
                 f"{len(bundled)} bundled and {len(external)} allowed external "
                 "libraries cover every DT_NEEDED entry", details)


def check_required_linkage(contract, archive: Archive) -> Check:
    problems = []
    satisfied = []
    for rule in contract["required_linkage"]:
        pattern = soname_regex(rule["library"])
        libraries = [(n, e) for n, e in sorted(archive.elves.items())
                     if e.soname and pattern.match(e.soname)]
        if not libraries:
            problems.append(f"{rule['library']} not found ({rule['reason']})")
            continue
        for name, info in libraries:
            matching = [n for n in info.needed if re.match(rule["needs_pattern"], n)]
            if matching:
                satisfied.append(f"{name} -> {', '.join(matching)}")
            else:
                problems.append(f"{name} does not need {rule['needs_pattern']} "
                                f"({rule['reason']})")
    if problems:
        return Check("required-linkage", True, FAIL, "; ".join(problems),
                     {"problems": problems, "satisfied": satisfied})
    return Check("required-linkage", True, PASS, "; ".join(satisfied),
                 {"satisfied": satisfied})


def check_rpath_portability(contract, archive: Archive, loader: Check | None) -> Check:
    """Advisory: report search-path entries that only exist on the build host."""
    allowed = set(contract["rpath_allowed_absolute"])
    findings = {}
    for name, info in sorted(archive.elves.items()):
        for kind, entries in (("DT_RPATH", info.rpath), ("DT_RUNPATH", info.runpath)):
            for entry in entries or []:
                if entry == "":
                    reason = "empty entry (searches the current working directory)"
                elif entry.startswith("$ORIGIN") or entry.startswith("${ORIGIN}"):
                    continue
                elif entry in allowed:
                    continue
                else:
                    reason = "absolute path from the build host"
                findings.setdefault(f"{kind} {entry!r}", {"reason": reason, "files": 0})
                findings[f"{kind} {entry!r}"]["files"] += 1
    details = {"non_portable_entries": findings}
    if loader is not None and loader.details.get("resolved_via_build_paths"):
        details["resolved_via_build_paths"] = loader.details["resolved_via_build_paths"]
    if findings or details.get("resolved_via_build_paths"):
        return Check("rpath-portability", False, WARN,
                     f"{len(findings)} non-portable search-path entries (advisory)", details)
    return Check("rpath-portability", False, PASS,
                 "search paths use only $ORIGIN or allowed locations", details)


# --------------------------------------------------------------------------
# Runtime checks (need a matching Linux runtime environment)
# --------------------------------------------------------------------------

def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def host_environment() -> dict:
    env = {
        "system": platform.system(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
    }
    for line in read_text("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            env["os"] = line.split("=", 1)[1].strip().strip('"')
    cpu_flags = []
    for line in read_text("/proc/cpuinfo").splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "model name" and "cpu_model" not in env:
            env["cpu_model"] = value.strip()
        elif key == "flags" and not cpu_flags:
            cpu_flags = value.split()
    env["cpu_flags"] = cpu_flags
    return env


def runtime_unavailable_reason(contract, environment, static_only: bool):
    if static_only:
        return "runtime checks disabled (--static-only)"
    if environment["system"] != "Linux":
        return f"host system is {environment['system']}, not Linux"
    if environment["machine"] != contract["elf"]["machine"]:
        return f"host machine is {environment['machine']}, not {contract['elf']['machine']}"
    if not os.path.exists(contract["elf"]["interpreter"]):
        return f"dynamic loader {contract['elf']['interpreter']} is not present"
    return None


def runtime_library_path(contract, archive_root: str, rocm_root: str | None) -> list:
    paths = []
    for entry in contract["runtime"]["library_path"]:
        if "{rocm_root}" in entry and not rocm_root:
            continue
        paths.append(entry.format(archive_dir=archive_root, rocm_root=rocm_root or ""))
    return paths


def runtime_env(library_path: list) -> dict:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LD_LIBRARY_PATH": ":".join(library_path),
    }


LIST_LINE = re.compile(r"^\s*(\S+)\s+=>\s+(?:(not found)|(\S+)\s+\(0x[0-9a-fA-F]+\))\s*$")


def parse_loader_listing(output: str) -> dict:
    """Map library name -> resolved path (None when not found)."""
    resolved = {}
    for line in output.splitlines():
        match = LIST_LINE.match(line)
        if match:
            resolved[match.group(1)] = None if match.group(2) else match.group(3)
    return resolved


def ldconfig_dirs() -> set:
    dirs = set()
    for command in (["ldconfig", "-p"], ["/sbin/ldconfig", "-p"]):
        try:
            output = subprocess.run(command, capture_output=True, text=True,
                                    timeout=30, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in output.splitlines():
            if "=>" in line:
                dirs.add(os.path.dirname(line.split("=>", 1)[1].strip()))
        break
    return dirs


def is_under(path: str, roots) -> bool:
    """Lexical prefix test on the path exactly as the loader printed it."""
    path = os.path.normpath(path)
    return any(path == os.path.normpath(root) or path.startswith(os.path.normpath(root) + os.sep)
               for root in roots)


def check_loader_resolution(contract, archive: Archive, rocm_root, unavailable) -> Check:
    if unavailable:
        return Check("loader-resolution", True, NOT_RUN, unavailable, {})
    library_path = runtime_library_path(contract, archive.root, rocm_root)
    # Trace mode, as used by ldd: the loader maps every dependency, prints where
    # each one resolved (or "not found") and exits before running any code.
    # Unlike `ld.so --list`, it keeps going after the first missing library.
    env = dict(runtime_env(library_path), LD_TRACE_LOADED_OBJECTS="1")
    timeout = contract["runtime"]["loader_timeout_seconds"]
    interpreter = contract["elf"]["interpreter"]
    archive_real = os.path.realpath(archive.root)
    # Locations a user host is expected to provide: the documented search path,
    # the ROCm installation (whose own libraries use $ORIGIN-relative RUNPATHs),
    # and the system library directories. Anything else was only reachable
    # through a search-path entry baked in on the build host.
    expected_roots = [*library_path, *([rocm_root] if rocm_root else []),
                      *SYSTEM_LIBRARY_DIRS, *ldconfig_dirs()]

    problems = []
    resolved_external = {}
    via_build_paths = {}
    for name in sorted(archive.elves):
        path = os.path.join(archive.root, name)
        try:
            proc = subprocess.run([interpreter, path], cwd=archive.root, env=env,
                                  capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"{name}: loader failed: {exc}")
            continue
        listing = parse_loader_listing(proc.stdout)
        if proc.returncode != 0 and not listing:
            problems.append(f"{name}: loader exited {proc.returncode}: "
                            f"{proc.stderr.strip()[:300]}")
            continue
        for lib, target in sorted(listing.items()):
            if target is None:
                problems.append(f"{name}: {lib} => not found")
                continue
            real_dir = os.path.realpath(os.path.dirname(target))
            inside = real_dir == archive_real or real_dir.startswith(archive_real + os.sep)
            if archive.resolve_elf(lib) is not None:
                if not inside:
                    problems.append(f"{name}: bundled {lib} resolved outside the archive "
                                    f"to {target}")
                continue
            if inside:
                continue
            resolved_external.setdefault(lib, target)
            if not is_under(target, expected_roots):
                via_build_paths.setdefault(lib, target)

    details = {
        "library_path": library_path,
        "interpreter": interpreter,
        "resolved_external": resolved_external,
        "resolved_via_build_paths": via_build_paths,
    }
    if problems:
        details["problems"] = problems
        return Check("loader-resolution", True, FAIL,
                     f"{len(problems)} loader resolution problems", details)
    return Check("loader-resolution", True, PASS,
                 f"{len(archive.elves)} ELF files fully resolved by {interpreter}; "
                 "bundled libraries resolve inside the archive", details)


def excerpt(text: str) -> str:
    text = text or ""
    return text if len(text) <= OUTPUT_EXCERPT_BYTES else text[:OUTPUT_EXCERPT_BYTES] + "..."


def check_startup(contract, archive: Archive, rocm_root, environment, unavailable) -> Check:
    runtime = contract["runtime"]
    missing_flags = [f for f in runtime["startup_cpu_flags"]
                     if f not in environment.get("cpu_flags", [])]
    if unavailable is None and missing_flags:
        unavailable = ("host CPU lacks instruction set extensions the binaries are built "
                       f"for: {', '.join(missing_flags)}")
    if unavailable:
        return Check("startup", True, NOT_RUN, unavailable,
                     {"missing_cpu_flags": missing_flags})

    env = runtime_env(runtime_library_path(contract, archive.root, rocm_root))
    results = []
    problems = []
    for spec in runtime["startup_checks"]:
        command = [os.path.join(archive.root, spec["executable"]), *spec["args"]]
        label = " ".join([spec["executable"], *spec["args"]])
        record = {"command": label}
        try:
            proc = subprocess.run(command, cwd=archive.root, env=env, capture_output=True,
                                  text=True, errors="replace",
                                  timeout=runtime["startup_timeout_seconds"],
                                  stdin=subprocess.DEVNULL, check=False)
            record.update(exit_code=proc.returncode, stdout=excerpt(proc.stdout),
                          stderr=excerpt(proc.stderr))
            if proc.returncode != 0:
                problems.append(f"{label} exited {proc.returncode}")
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
            problems.append(f"{label} timed out after {runtime['startup_timeout_seconds']}s")
        except OSError as exc:
            record["error"] = str(exc)
            problems.append(f"{label}: {exc}")
        results.append(record)
    details = {"results": results}
    if problems:
        details["problems"] = problems
        return Check("startup", True, FAIL, "; ".join(problems), details)
    return Check("startup", True, PASS,
                 f"{len(results)} startup commands exited 0", details)


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

VERSION_NAME = re.compile(r"^(.+?)_([0-9]+(?:\.[0-9]+)*)$")


def version_key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def abi_requirements(archive: Archive) -> dict:
    """Highest required symbol version per version namespace (e.g. GLIBC)."""
    highest = {}
    for info in archive.elves.values():
        for names in info.version_requirements.values():
            for name in names:
                match = VERSION_NAME.match(name)
                if not match:
                    continue
                prefix, version = match.groups()
                if prefix not in highest or version_key(version) > version_key(highest[prefix]):
                    highest[prefix] = version
    return dict(sorted(highest.items()))


def blocked(check_id: str, mandatory: bool, reason: str) -> Check:
    return Check(check_id, mandatory, NOT_RUN, f"not run: {reason}", {})


def overall_result(contract, checks) -> str:
    if any(c.mandatory and c.status == FAIL for c in checks):
        return RESULT_FAIL
    statuses = {c.id: c.status for c in checks}
    if any(statuses.get(cid) != PASS for cid in contract["release_gate"]["required_checks"]):
        return RESULT_INCOMPLETE
    return RESULT_PASS


def validate_archive(contract, contract_path, archive_path, upstream_tag, rocm_variant,
                     rocm_root=None, rocm_version_detected=None, static_only=False) -> dict:
    environment = host_environment()
    checks = [check_metadata(contract, archive_path, upstream_tag, rocm_variant)]
    archive = None
    workdir = tempfile.mkdtemp(prefix="artifact-validation-")
    try:
        integrity, raw_members = check_archive_integrity(archive_path)
        checks.append(integrity)
        if raw_members is not None:
            safety, archive = check_safe_extraction(raw_members, archive_path, workdir)
            checks.append(safety)

        later = ["inventory", "permissions", "elf-architecture", "dependency-closure",
                 "required-linkage", "rpath-portability", "loader-resolution", "startup"]
        if archive is None:
            reason = "archive could not be read or safely extracted"
            checks.extend(blocked(cid, cid != "rpath-portability", reason) for cid in later)
        else:
            scan_elves(archive)
            checks.append(check_inventory(contract, archive))
            checks.append(check_permissions(contract, archive))
            checks.append(check_elf_architecture(contract, archive))
            closure = check_dependency_closure(contract, archive)
            checks.append(closure)
            checks.append(check_required_linkage(contract, archive))
            unavailable = runtime_unavailable_reason(contract, environment, static_only)
            static_failed = [c.id for c in checks if c.mandatory and c.status == FAIL]
            if unavailable is None and static_failed:
                unavailable = f"static checks failed ({', '.join(static_failed)})"
            loader = check_loader_resolution(contract, archive, rocm_root, unavailable)
            checks.append(check_rpath_portability(contract, archive, loader))
            checks.append(loader)
            startup_blocker = unavailable
            if startup_blocker is None and loader.status != PASS:
                startup_blocker = "loader resolution did not pass"
            checks.append(check_startup(contract, archive, rocm_root, environment,
                                        startup_blocker))

        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "kind": "artifact-validation",
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
                            .replace(microsecond=0).isoformat(),
            "artifact": {
                "filename": os.path.basename(archive_path),
                "sha256": sha256_file(archive_path) if os.path.isfile(archive_path) else None,
                "size_bytes": os.path.getsize(archive_path)
                              if os.path.isfile(archive_path) else None,
            },
            "upstream_tag": upstream_tag,
            "rocm_variant": rocm_variant,
            "rocm_version_detected": rocm_version_detected,
            "contract": {"path": contract_path, "sha256": sha256_file(contract_path)},
            "environment": {k: v for k, v in environment.items() if k != "cpu_flags"},
            "ci": {k: os.environ[k] for k in
                   ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID")
                   if k in os.environ},
            "result": overall_result(contract, checks),
            "checks": [asdict(c) for c in checks],
        }
        if archive is not None:
            report["inventory"] = {
                "executables": archive.executables,
                "libraries": [{"file": n, "soname": archive.elves[n].soname}
                              for n in archive.libraries],
                "symlinks": {n: m.linkname for n, m in sorted(archive.members.items())
                             if m.issym()},
                "other_files": archive.other_files,
            }
            report["abi_requirements"] = abi_requirements(archive)
        return report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Release gate
# --------------------------------------------------------------------------

def load_reports(reports_dir: str):
    reports = {}
    problems = []
    for entry in sorted(os.listdir(reports_dir)):
        path = os.path.join(reports_dir, entry)
        if not entry.endswith(".json") or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                report = json.load(handle)
        except (OSError, ValueError) as exc:
            problems.append(f"{entry}: unreadable report ({exc})")
            continue
        if not isinstance(report, dict) or report.get("kind") != "artifact-validation":
            continue
        filename = (report.get("artifact") or {}).get("filename")
        if filename in reports:
            problems.append(f"{entry}: duplicate report for {filename}")
            continue
        reports[filename] = report
    return reports, problems


def gate_release(contract, contract_path, upstream_tag, archives_dir, reports_dir) -> dict:
    expected = {expected_archive_name(contract, upstream_tag, v): v
                for v in contract["rocm_variants"]}
    present = set(os.listdir(archives_dir))
    reports, problems = load_reports(reports_dir)
    contract_sha = sha256_file(contract_path)
    required = contract["release_gate"]["required_checks"]

    for name in sorted(present - set(expected)):
        problems.append(f"{name}: unexpected file in the release directory")
    for name in sorted(set(reports) - set(expected)):
        problems.append(f"{name}: report for an artifact that is not expected")

    artifacts = []
    for filename, variant in expected.items():
        issues = []
        path = os.path.join(archives_dir, filename)
        actual_sha = None
        if filename not in present or not os.path.isfile(path):
            issues.append("archive is missing")
        else:
            actual_sha = sha256_file(path)
        report = reports.get(filename)
        if report is None:
            issues.append("no validation report")
        else:
            if report.get("schema_version") != REPORT_SCHEMA_VERSION:
                issues.append(f"report schema_version {report.get('schema_version')!r}")
            if report.get("upstream_tag") != upstream_tag:
                issues.append(f"report upstream_tag {report.get('upstream_tag')!r} "
                              f"!= {upstream_tag!r}")
            if report.get("rocm_variant") != variant:
                issues.append(f"report rocm_variant {report.get('rocm_variant')!r} "
                              f"!= {variant!r}")
            if (report.get("contract") or {}).get("sha256") != contract_sha:
                issues.append("report was produced against a different contract")
            if actual_sha and report["artifact"].get("sha256") != actual_sha:
                issues.append("report sha256 does not match the archive bytes")
            if report.get("result") != RESULT_PASS:
                issues.append(f"report result is {report.get('result')!r}")
            statuses = {c.get("id"): c.get("status") for c in report.get("checks", [])}
            for cid in required:
                if statuses.get(cid) != PASS:
                    issues.append(f"required check {cid} is {statuses.get(cid) or 'absent'}")
        artifacts.append({
            "filename": filename,
            "rocm_variant": variant,
            "sha256": actual_sha,
            "status": PASS if not issues else FAIL,
            "checks": {c["id"]: c["status"] for c in (report or {}).get("checks", [])},
            "problems": issues,
        })
        problems.extend(f"{filename}: {issue}" for issue in issues)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": "release-gate",
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                        .replace(microsecond=0).isoformat(),
        "upstream_tag": upstream_tag,
        "contract": {"path": contract_path, "sha256": contract_sha},
        "required_checks": required,
        "result": RESULT_PASS if not problems else RESULT_FAIL,
        "artifacts": artifacts,
        "problems": problems,
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

STATUS_ICON = {PASS: "✅", FAIL: "❌", WARN: "⚠️", NOT_RUN: "⏭️"}


def markdown_validation(report: dict) -> str:
    lines = [
        f"### Artifact validation: `{report['artifact']['filename']}` — **{report['result']}**",
        "",
        f"Upstream tag `{report['upstream_tag']}` · ROCm variant `{report['rocm_variant']}`"
        f" · detected ROCm `{report.get('rocm_version_detected') or 'n/a'}`"
        f" · sha256 `{report['artifact'].get('sha256')}`",
        "",
        "| Check | Mandatory | Status | Summary |",
        "|---|---|---|---|",
    ]
    for check in report["checks"]:
        summary = check["summary"].replace("|", "\\|")
        if len(summary) > 300:
            summary = summary[:300] + "…"
        lines.append(f"| `{check['id']}` | {'yes' if check['mandatory'] else 'advisory'} | "
                     f"{STATUS_ICON.get(check['status'], '')} {check['status']} | {summary} |")
    if report.get("abi_requirements"):
        abi = ", ".join(f"{k} {v}" for k, v in report["abi_requirements"].items())
        lines += ["", f"ABI requirements: {abi}"]
    return "\n".join(lines) + "\n\n"


def markdown_gate(summary: dict) -> str:
    lines = [
        f"### Release gate for `{summary['upstream_tag']}` — **{summary['result']}**",
        "",
        "| Artifact | ROCm | Status | Problems |",
        "|---|---|---|---|",
    ]
    for artifact in summary["artifacts"]:
        problems = "; ".join(artifact["problems"]) or "—"
        lines.append(f"| `{artifact['filename']}` | {artifact['rocm_variant']} | "
                     f"{STATUS_ICON[artifact['status']]} {artifact['status']} | {problems} |")
    other = [p for p in summary["problems"]
             if not any(p.startswith(a["filename"] + ":") for a in summary["artifacts"])]
    if other:
        lines += ["", "Other problems:", *[f"- {p}" for p in other]]
    return "\n".join(lines) + "\n\n"


def write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def append_text(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    val = sub.add_parser("validate", help="validate one release archive")
    val.add_argument("--contract", required=True)
    val.add_argument("--archive", required=True)
    val.add_argument("--upstream-tag", required=True)
    val.add_argument("--rocm-variant", required=True)
    val.add_argument("--rocm-root", help="ROCm installation used for runtime checks")
    val.add_argument("--rocm-version-detected", help="ROCm version string to record")
    val.add_argument("--static-only", action="store_true",
                     help="skip loader and startup checks (reported as not_run)")
    val.add_argument("--report", required=True, help="path of the JSON report to write")
    val.add_argument("--markdown", help="append a Markdown summary to this file")

    gate = sub.add_parser("gate", help="gate publication on validation reports")
    gate.add_argument("--contract", required=True)
    gate.add_argument("--upstream-tag", required=True)
    gate.add_argument("--archives-dir", required=True)
    gate.add_argument("--reports-dir", required=True)
    gate.add_argument("--summary", help="path of the JSON gate summary to write")
    gate.add_argument("--markdown", help="append a Markdown summary to this file")

    args = parser.parse_args(argv)
    try:
        contract = load_contract(args.contract)
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        report = validate_archive(
            contract, args.contract, args.archive, args.upstream_tag, args.rocm_variant,
            rocm_root=args.rocm_root, rocm_version_detected=args.rocm_version_detected,
            static_only=args.static_only)
        write_json(args.report, report)
        append_text(args.markdown, markdown_validation(report))
        for check in report["checks"]:
            print(f"[{check['status']:>7}] {check['id']}: {check['summary']}")
            for problem in check["details"].get("problems", [])[:50]:
                print(f"          - {problem}")
        print(f"result: {report['result']} (report: {args.report})")
        return EXIT_CODES[report["result"]]

    summary = gate_release(contract, args.contract, args.upstream_tag,
                           args.archives_dir, args.reports_dir)
    if args.summary:
        write_json(args.summary, summary)
    append_text(args.markdown, markdown_gate(summary))
    for artifact in summary["artifacts"]:
        print(f"[{artifact['status']:>4}] {artifact['filename']}")
    for problem in summary["problems"]:
        print(f"  - {problem}")
    print(f"release gate: {summary['result']}")
    return EXIT_CODES[summary["result"]]


if __name__ == "__main__":
    sys.exit(main())
