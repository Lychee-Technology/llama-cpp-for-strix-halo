# Build from Source

The instructions below reproduce the CI build configuration from [`.github/workflows/build-and-release.yml`](../.github/workflows/build-and-release.yml) for a pinned upstream tag. Note that these are **version-pinned, repeatable build instructions**, not bit-for-bit deterministic builds.

## Prerequisites

- Ubuntu 24.04 `x86_64` (or equivalent Linux environment)
- Build tools (`cmake`, `build-essential`, `libcurl4-openssl-dev`, `git`)
- ROCm toolchain (`core`, `core-dev`, `dev-tools`) installed at `/opt/rocm` (for example via AMD's runfile installer with `gfx=gfx1151`)

## Build Steps

```bash
# 1. Install build dependencies (Ubuntu / Debian example)
sudo apt-get update
sudo apt-get install -y cmake build-essential libcurl4-openssl-dev git

# 2. Read the pinned upstream llama.cpp tag from this repository
source ./llama_cpp_version

# 3. Clone upstream llama.cpp at the pinned tag
git clone --depth 1 --branch "${LLAMA_CPP_VER}" https://github.com/ggml-org/llama.cpp.git
cd llama.cpp

# 4. Configure using the ROCm LLVM toolchain and Strix Halo flags
export ROCM_PATH=/opt/rocm
export LLVM_BIN="$("${ROCM_PATH}/bin/hipconfig" -l)"
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

# 5. Compile
cmake --build build -- -j"$(nproc)"

# 6. Verify built binaries
LD_LIBRARY_PATH="$PWD/build/bin:${ROCM_PATH}/lib" ./build/bin/llama-cli --version
LD_LIBRARY_PATH="$PWD/build/bin:${ROCM_PATH}/lib" ./build/bin/llama-cli --list-devices
```

For rationale behind each flag, see [build-optimizations.md](build-optimizations.md). For the full, authoritative CI environment setup (including runfile installer options), see [`.github/workflows/build-and-release.yml`](../.github/workflows/build-and-release.yml).
