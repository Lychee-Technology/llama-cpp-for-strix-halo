# Performance and Benchmarks

This repository does not currently run automated GPU benchmarks in CI or publish controlled benchmark baselines. Because Strix Halo throughput varies significantly with model architecture, quantization, context length, Linux kernel version, ROCm version, and platform TDP/power profile, unverified or uncontrolled token-per-second figures are omitted.

## Reproducible Benchmark Method

To measure your own setup with `llama-bench`:

```bash
cd llama-cpp-strix-halo
export LD_LIBRARY_PATH="$PWD:/opt/rocm/lib:${LD_LIBRARY_PATH:-}"

./llama-bench -m /path/to/model.gguf -ngl 99 -p 512,2048 -n 128 -fa on -r 3 -o md
```

## Submitting Benchmark Results

If you submit benchmark results via an issue or pull request, please include:

- Hardware model, total RAM capacity, and power profile / TDP setting
- Linux distribution, kernel version (`uname -r`), and ROCm version
- Release archive filename (or upstream `llama.cpp` tag + build flags)
- Exact model artifact and quantization, context/prompt/generation lengths, `-ngl`, `-fa`, and full `llama-bench` command with raw output
