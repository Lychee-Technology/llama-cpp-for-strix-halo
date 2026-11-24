# llama.cpp for AMD Strix Halo

Prebuilt Linux `x86_64` binaries and build configurations for running upstream [`llama.cpp`](https://github.com/ggml-org/llama.cpp) on **AMD Strix Halo (`gfx1151`)** APUs with **ROCm HIP**, **Zen 5 CPU targeting**, and **unified-memory-aware (`GGML_HIP_UMA`)** flags.

[![Latest Release](https://img.shields.io/github/v/release/Lychee-Technology/llama-cpp-for-strix-halo?include_prereleases&label=release)](https://github.com/Lychee-Technology/llama-cpp-for-strix-halo/releases)
[![CI Build](https://img.shields.io/github/actions/workflow/status/Lychee-Technology/llama-cpp-for-strix-halo/build-and-release.yml?label=CI%20build)](https://github.com/Lychee-Technology/llama-cpp-for-strix-halo/actions/workflows/build-and-release.yml)
[![License](https://img.shields.io/github/license/Lychee-Technology/llama-cpp-for-strix-halo)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Linux%20x86__64-blue)
![Target GPU](https://img.shields.io/badge/target-AMD%20Strix%20Halo%20(gfx1151)-ED1C24)
![Backend](https://img.shields.io/badge/backend-ROCm%20HIP-orange)

[Download](https://github.com/Lychee-Technology/llama-cpp-for-strix-halo/releases) · [Compatibility](#compatibility-and-system-requirements) · [Quick Start](docs/quick-start.md) · [Unified Memory](docs/memory-configuration.md) · [Build Optimizations](docs/build-optimizations.md) · [Troubleshooting](docs/troubleshooting.md) · [Build from Source](docs/build-from-source.md)

---

## Overview

AMD Strix Halo APUs combine Zen 5 CPU cores, an RDNA 3.5 integrated GPU (`gfx1151`), and high-capacity unified LPDDR5X system memory. While upstream `llama.cpp` now publishes generic Ubuntu ROCm binaries, compiling and tuning specifically for Strix Halo still requires non-default build choices:

- Targeting `gfx1151` and Zen 5 (`-march=znver5`, AVX-512) instead of broad generic targets.
- Enabling HIP unified memory allocation (`GGML_HIP_UMA=ON`) and quantized KV-cache FlashAttention kernels (`GGML_CUDA_FA_ALL_QUANTS=ON`).
- Applying ROCm/LLVM workarounds relevant to `gfx1151`, including disabling rocWMMA FlashAttention (`GGML_HIP_ROCWMMA_FATTN=OFF`) and raising the AMDGPU local loop-unroll threshold.

This repository tracks upstream `ggml-org/llama.cpp` releases and builds ready-to-extract Linux `x86_64` archives across multiple ROCm versions via GitHub Actions.

### Scope and Boundaries

| Layer | Responsibility |
|---|---|
| **Upstream [`llama.cpp`](https://github.com/ggml-org/llama.cpp)** | Inference engine, GGUF format, quantization, `llama-cli`, `llama-server`, `llama-bench`, and backend kernels. |
| **This repository** | Pinned upstream tracking ([`llama_cpp_version`](llama_cpp_version)), Strix Halo CMake/compiler flags, multi-ROCm CI builds, and Linux host documentation. |
| **Linux / AMDGPU / ROCm** | Kernel driver (`amdgpu`, `amdkfd`), TTM/GTT memory management, and host ROCm runtime libraries. |

> [!NOTE]
> **What this project is not:** This repository is not a separate inference engine, not a fork of `llama.cpp` source code, not an official AMD or `llama.cpp` distribution, and does not target the Ryzen AI NPU or other AMD GPU architectures.

<details>
<summary>Historical context (January 2026)</summary>

When this repository was created in January 2026, `llama.cpp` was one of the few runtimes that could practically utilize large unified memory pools (>80 GiB) on Strix Halo under Linux, upstream releases did not target Strix Halo's specific build flags, and older Linux kernels plus default TTM settings severely restricted GPU-addressable allocations. Upstream `llama.cpp` and Linux kernel support have since evolved, while this project continues to provide `gfx1151` + Zen 5 builds and unified-memory guidance.

</details>

---

## Key Features

- **Hardware-targeted Linux `x86_64` binaries:** Built specifically for AMD Strix Halo (`gfx1151`) and Zen 5 (`-march=znver5 -mtune=znver5`, AVX-512).
- **Unified-memory-aware HIP configuration:** Compiles with `GGML_HIP=ON` and `GGML_HIP_UMA=ON` so HIP can allocate unified memory on Strix Halo APUs.
- **Workarounds for known `gfx1151` regressions:** Sets `GGML_HIP_ROCWMMA_FATTN=OFF` and `-mllvm --amdgpu-unroll-threshold-local=600` in CI builds (see [Build Optimizations](docs/build-optimizations.md)).
- **Multiple ROCm build variants:** Publishes separate archives for ROCm `7.14.1`, `10.1.0`, and `10.2.0` from each release tag.
- **Upstream release tracking:** Mirrors upstream `b*` rolling tags and `v*` semantic releases via [`bump_llama_cpp.sh`](bump_llama_cpp.sh) and [`llama_cpp_version`](llama_cpp_version).

---

## Compatibility and System Requirements

### Hardware Requirements

- **CPU:** AMD Zen 5 `x86_64` processor (binaries are compiled with `-march=znver5` and AVX-512 flags and will not run on CPUs lacking Zen 5 / AVX-512 instructions).
- **GPU:** AMD Strix Halo integrated GPU (`gfx1151` ISA target only). Other RDNA 3 / 3.5 iGPUs or discrete GPUs (e.g., `gfx1100`–`gfx1103`, `gfx1150`) are not targeted.
- **System Memory:** Determines the maximum model size and context length you can load. Running 70B–120B+ parameter models depends on having sufficient physical RAM (such as 128 GiB SKUs), appropriate quantization, and adequate GPU-mappable memory; 32 GiB and 64 GiB systems are bounded accordingly.

### Software Requirements

- **OS:** Linux `x86_64` (built on Ubuntu 24.04 runners; distributions with older `glibc` than Ubuntu 24.04 are not verified).
- **Linux Kernel:** Kernel with `amdgpu` and `amdkfd` support for Strix Halo. **Linux `6.16.9+` or `6.18+`** is recommended because earlier kernels exhibited KFD/ROCm allocation caps near 15.5 GiB even when GTT was enlarged.
- **ROCm Runtime:** User-space ROCm runtime libraries matching the ROCm version of the archive you download (plus `amdgpu` kernel support and `render`/`video` group membership).

### Compatibility Matrix

The CI workflow builds on cloud runners without Strix Halo hardware. Before publication, each archive is [validated](docs/release-validation.md) for completeness, ELF architecture, and shared-library resolution against the matching ROCm version. GPU execution is not tested in CI and depends on your host kernel and ROCm installation.

| Component | Target / Build Configuration | Verification Status | Notes |
|---|---|---|---|
| **GPU ISA** | `gfx1151` (AMD Strix Halo) | Build target (CI-verified) | Compiled with `GPU_TARGETS=gfx1151` and `AMDGPU_TARGETS=gfx1151` |
| **CPU ISA** | Zen 5 (`-march=znver5`, AVX-512) | Build target (CI-verified) | Portable across Zen 5 hosts (`GGML_NATIVE=OFF`) |
| **ROCm `7.14.1`** | Built via AMD runfile (`core,core-dev,dev-tools`) | Build-verified in CI | Not hardware-tested in CI; requires compatible host ROCm 7.x runtime |
| **ROCm `10.1.0`** | Built via AMD runfile (`core,core-dev,dev-tools`) | Build-verified in CI | Not hardware-tested in CI; requires compatible host ROCm 10.1 runtime |
| **ROCm `10.2.0`** | Built via AMD runfile (`core,core-dev,dev-tools`) | Build-verified in CI | Not hardware-tested in CI; requires compatible host ROCm 10.2 runtime |
| **Linux Distros** | Ubuntu 24.04 (`glibc` baseline) | Build environment | Other modern distros (Fedora, Arch/CachyOS) may work if `glibc` and ROCm runtime are compatible |

---

## Download and Installation

### 1. Choose a Release and ROCm Variant

Browse available builds on the [Releases page](https://github.com/Lychee-Technology/llama-cpp-for-strix-halo/releases).

- **Release tags:** `b<N>` tracks upstream rolling `llama.cpp` builds (e.g., `b12669`); `v<X.Y.Z>` tracks upstream semantic releases. The currently pinned tag is recorded in [`llama_cpp_version`](llama_cpp_version).
- **Asset naming:** `llama-cpp-<LLAMA_CPP_VER>-rocm-<ROCM_VER>-gfx1151.tar.xz`
- **Active ROCm matrix:** `7.14.1`, `10.1.0`, `10.2.0` (choose the version matching the ROCm runtime installed on your Linux host).

### 2. Install Prerequisites

Before running the binaries, ensure your Linux host has:

1. **AMDGPU kernel support & device permissions:** Your user must belong to the `render` and `video` groups to access `/dev/kfd` and `/dev/dri/*`:

   ```bash
   sudo usermod -aG render,video "$USER"
   ```

   Log out and back in (or reboot) for group changes to take effect. Verify with `groups`.

2. **ROCm user-space runtime:** Install the ROCm runtime matching your selected tarball (`7.14.1`, `10.1.0`, or `10.2.0`). You do **not** need the full ROCm compiler SDK just to run prebuilt binaries, only the runtime libraries and driver support. Follow AMD's official [ROCm Linux installation guide](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/install-methods/package-manager-index.html) for your distribution, or AMD's ROCm runfile/tarball installers if using ROCm 10.x.

3. **Extraction tools:** Ensure `tar` and `xz` (`xz-utils` on Debian/Ubuntu, `xz` on Fedora/Arch) and `curl` are installed.

### 3. Download and Extract

The release `.tar.xz` archives package the contents of `llama.cpp/build/bin` directly at the archive root, so extract into a dedicated directory. Check the [Releases page](https://github.com/Lychee-Technology/llama-cpp-for-strix-halo/releases) and set `LLAMA_CPP_VER` and `ROCM_VER` to match the release you want:

```bash
# Example values — replace with the release tag and ROCm version you want to install
LLAMA_CPP_VER="b12669"
ROCM_VER="10.2.0"

ARCHIVE="llama-cpp-${LLAMA_CPP_VER}-rocm-${ROCM_VER}-gfx1151.tar.xz"
URL="https://github.com/Lychee-Technology/llama-cpp-for-strix-halo/releases/download/${LLAMA_CPP_VER}/${ARCHIVE}"

curl -fsSLO "${URL}"
mkdir -p llama-cpp-strix-halo
tar -xJf "${ARCHIVE}" -C llama-cpp-strix-halo
```

> [!NOTE]
> Release assets do not currently include `.sha256` checksum files. You can inspect downloaded archives locally with `tar -tf "${ARCHIVE}"` before extracting.

### 4. Verify Installation

Move into the extracted directory and add both the extracted directory (for bundled `libllama.so` / `libggml*.so`) and your ROCm library directory to `LD_LIBRARY_PATH`:

```bash
cd llama-cpp-strix-halo
export LD_LIBRARY_PATH="$PWD:/opt/rocm/lib:${LD_LIBRARY_PATH:-}"

# 1. Confirm all shared libraries resolve (should print no "not found" lines)
ldd ./llama-cli | grep "not found" || echo "All shared libraries resolved"

# 2. Check binary version and build info
./llama-cli --version

# 3. Check detected compute devices (look for HIP / Radeon 8060S / gfx1151)
./llama-cli --list-devices
```

If `/opt/rocm` is installed at a versioned path (e.g., `/opt/rocm-10.2.0/lib` or a custom runfile prefix), replace `/opt/rocm/lib` with your actual ROCm `lib` directory.

---

## Guides and Reference

| Guide | Topics Covered |
|---|---|
| **[Quick Start](docs/quick-start.md)** | `llama-cli`, `llama-server`, `llama-bench`, and verifying GPU acceleration |
| **[Unified Memory Configuration](docs/memory-configuration.md)** | BIOS UMA vs. GTT/TTM mechanics, read-only checks, GiB-to-page math, distro setup, and rollback |
| **[Build Optimizations](docs/build-optimizations.md)** | CI CMake flags, `gfx1151` workarounds (`#19984`), and exact build configuration |
| **[Performance and Benchmarks](docs/benchmarks.md)** | Reproducible `llama-bench` methodology and community submission requirements |
| **[Runtime Selection](docs/runtime-selection.md)** | Choosing between `llama.cpp` and specialized engines (Loom, Strata) |
| **[Troubleshooting](docs/troubleshooting.md)** | Diagnosing shared library, device detection, CPU fallback, and memory issues |
| **[Build from Source](docs/build-from-source.md)** | Reproducing the CI build locally from pinned upstream tags |
| **[Release Pipeline and Versioning](docs/release-pipeline.md)** | Upstream tracking (`bump_llama_cpp.sh`), GitHub Actions matrix, and verification scope |
| **[Release Validation](docs/release-validation.md)** | Distribution contract, artifact checks, release gate, and validation evidence |
| **[Repository Structure](docs/repository-structure.md)** | Overview of tracked scripts, workflows, and documentation files |

---

## Contributing

Contributions focused on Strix Halo build engineering and documentation are welcome:

- ROCm toolchain or CMake compatibility updates
- Strix Halo hardware verification reports and reproducible `llama-bench` measurements
- Documentation clarity and troubleshooting improvements
- Release automation enhancements

### Reporting Issues

Before opening an issue, check which layer owns the problem:

- **Packaging, CI builds, missing release variants, or these docs:** Open an issue in this repository.
- **Core model support, GGUF parsing, CLI flags, or HIP kernel bugs:** Report upstream at [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp/issues).
- **Kernel panics, `amdgpu`/`amdkfd` driver bugs, or ROCm installer issues:** Check your Linux distribution or AMD ROCm issue trackers.

When reporting a bug here, please include:

- Strix Halo device model and physical RAM capacity
- Linux distribution, kernel version (`uname -r`), and ROCm version
- Exact downloaded archive filename and `llama.cpp` tag
- Model name, quantization, exact command run, and relevant stderr logs (including `--list-devices` output)

### Pull Request Workflow

1. Check existing issues and pull requests to avoid duplicate work.
2. Fork the repository and create a focused branch.
3. Keep changes scoped and consistent with [`.github/workflows/build-and-release.yml`](.github/workflows/build-and-release.yml).
4. Validate your changes locally (for example, shell/YAML syntax or markdown links) and open a pull request describing what was tested and on what hardware.

---

## License

This repository's scripts, workflow definitions, and documentation are licensed under the **[MIT License](LICENSE)** (Copyright © 2026 Lychee Technology Inc.).

- **Upstream `llama.cpp`:** Distributed under the [upstream `llama.cpp` MIT License](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE).
- **ROCm & third-party runtime libraries:** Governed by their respective AMD ROCm and system library licenses.
- **Model weights:** Governed by the license terms specified by each model's publisher.

---

## Acknowledgments and Disclaimer

- **Community memory-tuning foundation:** Thanks to **Jeff Geerling** for documenting Linux AMD AI APU memory configuration in [*Increasing VRAM allocation on AMD AI APUs under Linux*](https://www.jeffgeerling.com/blog/2025/increasing-vram-allocation-on-amd-ai-apus-under-linux/) and the [Beowulf AI Cluster discussion](https://github.com/geerlingguy/beowulf-ai-cluster/issues/5).
- **Upstream ecosystems:** Thanks to the [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp), GGML, AMD ROCm, and Strix Halo Linux communities.

**Disclaimer:** This project is **not affiliated with, endorsed by, or maintained by AMD or the `llama.cpp` maintainers**. Binaries are provided as-is without warranty, and kernel boot or module parameter changes carry system-stability risks—always back up existing configurations before modifying boot settings.
