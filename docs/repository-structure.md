# Repository Structure

| File | Responsibility |
|---|---|
| [`.github/workflows/build-and-release.yml`](../.github/workflows/build-and-release.yml) | ROCm build matrix, CMake configuration, `.tar.xz` packaging, artifact validation, release gate, and GitHub Release publishing |
| [`.github/workflows/validator-tests.yml`](../.github/workflows/validator-tests.yml) | Runs the release validator test suite on pull requests |
| [`ci/artifact-contract.json`](../ci/artifact-contract.json) | Distribution contract every release archive is validated against |
| [`ci/validate_artifact.py`](../ci/validate_artifact.py) | Release archive validator (`validate`) and release gate (`gate`) |
| [`ci/elfinfo.py`](../ci/elfinfo.py) | Dependency-free ELF reader used by the validator |
| [`ci/tests/`](../ci/tests) | Positive and negative tests for the validator and gate |
| [`llama_cpp_version`](../llama_cpp_version) | Pinned upstream `llama.cpp` release tag built by CI |
| [`bump_llama_cpp.sh`](../bump_llama_cpp.sh) | Maintainer script to track upstream `b*` and `v*` releases and push mirror tags |
| [`docs/quick-start.md`](quick-start.md) | CLI inference, `llama-server`, `llama-bench`, and GPU offload verification |
| [`docs/memory-configuration.md`](memory-configuration.md) | Strix Halo Linux unified memory, TTM calculation, distro setup, and rollback guide |
| [`docs/build-optimizations.md`](build-optimizations.md) | Explanation of CI CMake flags and `gfx1151` compiler workarounds |
| [`docs/benchmarks.md`](benchmarks.md) | Reproducible `llama-bench` methodology and submission requirements |
| [`docs/runtime-selection.md`](runtime-selection.md) | Comparison between `llama.cpp` and specialized runtimes (Loom, Strata) |
| [`docs/troubleshooting.md`](troubleshooting.md) | Diagnostic and resolution guide for common runtime and memory issues |
| [`docs/build-from-source.md`](build-from-source.md) | Step-by-step instructions to reproduce the CI build locally |
| [`docs/release-pipeline.md`](release-pipeline.md) | Upstream release tracking and CI release automation |
| [`docs/release-validation.md`](release-validation.md) | Distribution contract, artifact checks, runtime environment, release gate, and evidence |
| [`docs/repository-structure.md`](repository-structure.md) | Index of tracked files in this repository |
| [`README.md`](../README.md) | Primary project overview, compatibility, download/installation, and documentation index |
| [`LICENSE`](../LICENSE) | MIT license for this repository |
