# Build Optimizations

The table below summarizes every non-default target and optimization flag enabled in [`.github/workflows/build-and-release.yml`](../.github/workflows/build-and-release.yml).

| Area | CI Setting | Rationale | Evidence / Caveat |
|---|---|---|---|
| **GPU target** | `-DGPU_TARGETS="gfx1151"` `-DAMDGPU_TARGETS="gfx1151"` | Compiles HIP kernels specifically for Strix Halo (`gfx1151`). | Verified in workflow; restricts binaries to `gfx1151` GPUs. |
| **CPU target** | `-DGGML_NATIVE=OFF` `-march=znver5 -mtune=znver5` + AVX/AVX2/AVX-512/VBMI/VNNI/BMI2/FMA/F16C | Targets Zen 5 hosts instead of the CI runner CPU while enabling Zen 5 SIMD extensions. | Verified in workflow; binaries will fail with `SIGILL` on non-Zen-5 / non-AVX-512 CPUs. |
| **Unified memory** | `-DGGML_HIP_UMA=ON` | Enables `hipMallocManaged` unified-memory support in the GGML HIP backend for APUs. | Verified in workflow; runtime effectiveness depends on kernel `amdgpu`/`amdkfd` and TTM configuration. |
| **KV-cache FlashAttention** | `-DGGML_CUDA_FA_ALL_QUANTS=ON` | Builds FlashAttention kernels for quantized KV caches instead of `f16`/`bf16` only. | Verified in workflow; avoids fallback to non-FA paths when quantized KV caches are requested. |
| **rocWMMA FlashAttention** | `-DGGML_HIP_ROCWMMA_FATTN=OFF` | Explicitly disabled due to severe `gfx115x` prefill regressions tracked in upstream [`llama.cpp#19984`](https://github.com/ggml-org/llama.cpp/issues/19984). | Verified in workflow; workaround intended to prevent regression rather than add a new feature. |
| **AMDGPU loop unroll** | `-mllvm --amdgpu-unroll-threshold-local=600` | Workaround for LLVM/ROCm loop-unrolling threshold regressions affecting `gfx1151` kernels (see [`llama.cpp#19984`](https://github.com/ggml-org/llama.cpp/issues/19984)). | Verified in workflow; compiler workaround rather than a general-purpose guarantee across future ROCm releases. |

## Exact CMake Configuration Used in CI

```bash
export ROCM_PATH=/opt/rocm
export LLVM_BIN="$(hipconfig -l)"
export CFLAGS="-O3 -march=znver5 -mtune=znver5"
export HIP_FLAGS="--rocm-path=${ROCM_PATH} -mllvm --amdgpu-unroll-threshold-local=600"

cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="${LLVM_BIN}/clang" \
  -DCMAKE_CXX_COMPILER="${LLVM_BIN}/clang++" \
  -DCMAKE_HIP_COMPILER="${LLVM_BIN}/clang" \
  -DCMAKE_HIP_FLAGS="${HIP_FLAGS}" \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_CXX_FLAGS="$CFLAGS" \
  -DGGML_HIP=ON \
  -DGGML_NATIVE=OFF \
  -DGGML_AVX=ON \
  -DGGML_AVX2=ON \
  -DGGML_AVX512=ON \
  -DGGML_AVX512_VBMI=ON \
  -DGGML_AVX512_VNNI=ON \
  -DGGML_BMI2=ON \
  -DGGML_FMA=ON \
  -DGGML_F16C=ON \
  -DGGML_CUDA_FA_ALL_QUANTS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DGPU_TARGETS="gfx1151" \
  -DAMDGPU_TARGETS="gfx1151" \
  -DGGML_HIP_UMA=ON \
  -DGGML_HIP_ROCWMMA_FATTN=OFF
```
