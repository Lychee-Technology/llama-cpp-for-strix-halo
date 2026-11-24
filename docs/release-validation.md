# Release Artifact Validation

Compiling successfully does not prove that a published archive is complete or that it can run. Every archive in the build matrix is therefore checked against an explicit **distribution contract** before it can be published. The release job then refuses to publish unless every expected ROCm variant has passing validation evidence tied to the exact bytes being released.

| File | Role |
|---|---|
| [`ci/artifact-contract.json`](../ci/artifact-contract.json) | The distribution contract: naming, variants, required contents, ABI target, allowed external libraries, runtime checks, and the checks the release gate requires |
| [`ci/validate_artifact.py`](../ci/validate_artifact.py) | `validate` checks one archive and writes a JSON report; `gate` decides whether a set of archives may be published |
| [`ci/elfinfo.py`](../ci/elfinfo.py) | Dependency-free ELF reader: header, `PT_INTERP`, `DT_NEEDED`, `DT_SONAME`, `DT_RPATH`/`DT_RUNPATH`, required symbol versions |
| [`ci/tests/`](../ci/tests) | Positive and negative tests, run by [`validator-tests.yml`](../.github/workflows/validator-tests.yml) on pull requests |

The validator uses only the Python standard library (Python 3.12 or newer, for `tarfile` extraction filters).

## Distribution Contract

| Contract field | Current value | Meaning |
|---|---|---|
| `archive_name_template` | `llama-cpp-{upstream_tag}-rocm-{rocm_variant}-gfx1151.tar.xz` | Required asset name |
| `upstream_tag_pattern` | `b<N>` or `v<semver>` | Tags `bump_llama_cpp.sh` mirrors |
| `rocm_variants` | `7.14.1`, `10.1.0`, `10.2.0` | Every variant the release must contain. Keep it in sync with the workflow matrix: a variant that is built but not listed fails validation, and a variant that is listed but not built fails the gate |
| `elf` | ELF64, little-endian, `x86_64`, `/lib64/ld-linux-x86-64.so.2` | Required architecture and program interpreter for every ELF file |
| `required_executables` | `llama-cli`, `llama-server`, `llama-bench` | Tools the documentation tells users to run |
| `required_libraries` | `libllama`, `libggml`, `libggml-base`, `libggml-cpu`, `libggml-hip` | Libraries that must ship, matched by SONAME (`<stem>.so[.N…]`) and reachable under that SONAME |
| `required_linkage` | `libggml-hip` needs `libamdhip64.so.N` | Proves the HIP backend is linked against the ROCm HIP runtime, so the build did not silently fall back to CPU only |
| `external_dependencies` | glibc, GCC runtime, OpenSSL 3, ROCm runtime (`libamdhip64`, `libhipblas`, `librocblas`, `libomp`) | The only libraries the archive may expect the host to provide |
| `runtime` | see below | Library search path, timeouts, CPU requirements and startup commands |
| `release_gate.required_checks` | see the checks table | Checks that must have **run and passed** for every variant |

Any other executables and libraries are recorded in the report's inventory but are not required.

## Checks

| Check | Kind | Gate-required | Verifies |
|---|---|---|---|
| `metadata` | static | yes | Upstream tag matches the tag pattern, the ROCm variant is in the contract, and the filename equals the template |
| `archive-integrity` | static | yes | The file is a `.tar.xz` that decompresses completely (truncation and CRC errors are caught) and contains files |
| `safe-extraction` | static | yes | Only regular files, directories and relative symlinks that stay inside the archive and resolve to an entry in it; no absolute paths, `..`, hard links, device nodes, duplicates, setuid/setgid/sticky bits or group/world-writable entries. Extraction uses the `tarfile` `data` filter |
| `inventory` | static | yes | Required executables are dynamic ELF programs; required libraries are present under their SONAMEs |
| `permissions` | static | yes | ELF executables are `r-x` for everyone; shared libraries are readable by everyone |
| `elf-architecture` | static | yes | Every ELF file parses and matches the contract's class, byte order, machine and interpreter, and is `EXEC` or `DYN` |
| `dependency-closure` | static | yes | Every `DT_NEEDED` entry is either bundled (with a matching SONAME) or an allowed external library |
| `required-linkage` | static | yes | The `required_linkage` rules hold |
| `rpath-portability` | static | advisory | Reports `DT_RPATH`/`DT_RUNPATH` entries that only exist on the build host (and empty entries, which search the current directory), plus external libraries the loader found only through such entries. Status `warn` never blocks a release |
| `loader-resolution` | runtime | yes | In the matching runtime environment, the dynamic loader resolves every dependency of every ELF file, and bundled libraries resolve to the extracted copies, not to some other location on the host |
| `startup` | runtime | no | Runs `llama-cli --version`, `llama-server --version` and `llama-bench --help` with a timeout. Each must exit 0 |

Check status is `pass`, `fail`, `warn` (advisory only) or `not_run`. If an earlier check fails in a way that makes later checks meaningless, the later checks report `not_run` with the reason.

The report `result` is:

- `fail` (exit code 1) if any mandatory check failed;
- `incomplete` (exit code 3) if nothing failed but a gate-required check did not run, for example static-only validation on macOS;
- `pass` (exit code 0) otherwise.

Usage and contract errors exit with code 2.

## Runtime Environment

Runtime checks only count when they run in a documented environment that matches the archive. That environment is the matrix build job itself:

- **Host:** `blacksmith-2vcpu-ubuntu-2404` (Ubuntu 24.04, glibc 2.39, x86_64). No GPU is present.
- **ROCm:** the same ROCm version as the variant, installed with AMD's runfile installer (`core,core-dev,dev-tools`) and linked at `/opt/rocm`. The detected version (`/opt/rocm/.info/version`, falling back to `hipconfig --version`) is recorded as `rocm_version_detected`.
- **Isolation:** the build tree is deleted before validation. The binaries record it in `DT_RPATH`/`DT_RUNPATH`, and `DT_RPATH` takes precedence over `LD_LIBRARY_PATH`, so a surviving build tree would satisfy lookups that the archive itself cannot. The check also fails if a bundled library resolves anywhere outside the extracted archive.
- **Library search path:** `LD_LIBRARY_PATH=<extracted archive>:/opt/rocm/lib`, matching the [installation instructions](../README.md#4-verify-installation). The working directory is the extracted archive.
- **Loader invocation:** `/lib64/ld-linux-x86-64.so.2 <file>` with `LD_TRACE_LOADED_OBJECTS=1`, the same trace mode `ldd` uses. The loader maps the dependencies and exits before running any code from the file.

If the host is not Linux x86_64 with the contract's loader, or `--static-only` is passed, both runtime checks report `not_run`. Because `loader-resolution` is gate-required, such a report is `incomplete` and cannot be used to publish.

### Startup check requirements

The binaries are built with `-march=znver5` and AVX-512. Running them on a CPU without those instructions can crash with `SIGILL`, which says nothing about the archive. Startup commands therefore run only when all of the following hold:

1. Runtime checks are available (see above).
2. `loader-resolution` passed.
3. `/proc/cpuinfo` lists every flag in `runtime.startup_cpu_flags` (the Zen 5 instruction set extensions the compiler may emit).

Otherwise `startup` reports `not_run` and names the missing CPU flags. Cloud runners are not guaranteed to have Zen 5 CPUs, so `startup` is not gate-required. When it does run, a non-zero exit or a timeout fails it, and a failed mandatory check makes the report `fail`, which blocks the release. Hardware-backed inference testing is tracked separately in [#7](https://github.com/Lychee-Technology/llama-cpp-for-strix-halo/issues/7).

## Release Gate

The `release` job runs `validate_artifact.py gate` after downloading the archives and reports. It publishes only if:

- `./release` contains exactly the expected archive for each ROCm variant in the contract, and nothing else (anything left there would be uploaded as a release asset);
- each archive has exactly one validation report with a matching filename, upstream tag and ROCm variant;
- each report's `sha256` equals the archive's actual digest and its contract digest equals the current contract's;
- each report's `result` is `pass`, and every check in `release_gate.required_checks` has status `pass`.

The gate also runs on `workflow_dispatch` builds. Only tag-triggered (`b*` and `v*`) runs create the GitHub Release, and the release assets and notes are unchanged.

## Evidence

| Artifact | Contents | Retention |
|---|---|---|
| `validation-report-<ROCM_VER>` | `<archive>.validation.json` for that matrix entry, uploaded even when validation fails | 90 days |
| `release-gate` | `release-gate.json`: per-variant gate outcome and digests | 90 days |

Each validation report records:
- the archive filename, size and SHA-256;
- the upstream tag, ROCm variant and detected ROCm version;
- the contract path and digest;
- the host environment (OS, kernel, CPU model, Python) and CI run identifiers;
- every check with its status, summary and details: for example, where the loader resolved each external library, and the output of the startup commands;
- the inventory of executables, libraries (with SONAMEs), symlinks and other files;
- `abi_requirements`, the highest required symbol version per namespace (e.g. `GLIBC 2.38`, `GLIBCXX 3.4.32`).

The same tables are written to the job summary of each workflow run.

## Running Locally

```bash
# Static checks only (works on any OS; result is "incomplete", exit code 3)
python3 ci/validate_artifact.py validate \
  --contract ci/artifact-contract.json \
  --archive llama-cpp-b12669-rocm-10.2.0-gfx1151.tar.xz \
  --upstream-tag b12669 --rocm-variant 10.2.0 \
  --static-only --report /tmp/report.json

# Full validation on a Linux x86_64 host with ROCm installed
python3 ci/validate_artifact.py validate \
  --contract ci/artifact-contract.json \
  --archive llama-cpp-b12669-rocm-10.2.0-gfx1151.tar.xz \
  --upstream-tag b12669 --rocm-variant 10.2.0 \
  --rocm-root /opt/rocm --report /tmp/report.json

# Test suite (the end-to-end loader tests need Linux x86_64 with gcc)
python3 -m unittest discover -s ci/tests -v
```

## Updating the Contract

- **Adding or removing a ROCm variant:** change the workflow matrix and `rocm_variants` in the same commit.
- **Upstream adds a shared-library dependency:** `dependency-closure` names the library and the files that need it. Add it to `external_dependencies` only if users are expected to have it installed; otherwise it should be bundled.
- **Upstream renames a required tool or library:** update `required_executables`/`required_libraries`, and the documentation that refers to the old name.

Any change to the contract changes its digest, so reports made against the old contract no longer pass the gate.
