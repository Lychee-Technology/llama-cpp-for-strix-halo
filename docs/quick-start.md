# Quick Start

Start with a small GGUF model to confirm end-to-end GPU inference after completing [Download and Installation](../README.md#download-and-installation). Model weights are provided by their respective authors under their own licenses.

## Example 1: Local CLI Inference (`llama-cli`)

Using `-hf` downloads a small model directly from Hugging Face (the CI builds link against `libcurl4-openssl-dev`). Alternatively, pass `-m /path/to/model.gguf` for a local GGUF file:

```bash
cd llama-cpp-strix-halo
export LD_LIBRARY_PATH="$PWD:/opt/rocm/lib:${LD_LIBRARY_PATH:-}"

# Single-turn prompt against a small Qwen3 GGUF model with full GPU layer offload
./llama-cli \
  -hf ggml-org/Qwen3-0.6B-GGUF \
  -ngl 99 \
  -st \
  -p "Explain unified memory in two sentences."
```

- `-ngl 99`: Offloads model layers to the HIP GPU backend (adjust or use `auto` depending on model size and available memory).
- `-st`: Runs a single turn and exits cleanly instead of entering interactive chat mode.

## Example 2: Local OpenAI-Compatible API Server (`llama-server`)

Bind to `127.0.0.1` so the server is only reachable from your local machine:

```bash
cd llama-cpp-strix-halo
export LD_LIBRARY_PATH="$PWD:/opt/rocm/lib:${LD_LIBRARY_PATH:-}"

./llama-server \
  -m /path/to/your-model.gguf \
  -ngl 99 \
  --host 127.0.0.1 \
  --port 8080
```

> [!WARNING]
> Do not bind `--host 0.0.0.0` on untrusted networks without authentication and firewall rules. `llama-server` does not enforce user authentication by default.

In another terminal, test the local endpoint:

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "max_tokens": 64
  }'
```

## Example 3: Throughput Benchmark (`llama-bench`)

Measure prompt processing (`pp`) and token generation (`tg`) throughput on a local model:

```bash
cd llama-cpp-strix-halo
export LD_LIBRARY_PATH="$PWD:/opt/rocm/lib:${LD_LIBRARY_PATH:-}"

./llama-bench -m /path/to/your-model.gguf -ngl 99 -p 512 -n 128
```

## Verifying GPU Acceleration

A command starting without errors does not guarantee GPU execution—`llama.cpp` can silently fall back to CPU if HIP initialization fails or `-ngl` is omitted. Confirm GPU offload using these checks:

1. **Device discovery:** `./llama-cli --list-devices` lists a HIP/ROCm device (rather than only CPU).
2. **Startup logs:** Look for HIP device initialization and layer offload lines such as `offloaded X/Y layers to GPU` in stderr output.
3. **Live GPU monitoring:** Run `rocm-smi` or `amdgpu_top` in a second terminal while generating tokens to observe GPU compute/memory activity.

For additional CLI flags and server options, refer to the [upstream `llama.cpp` documentation](https://github.com/ggml-org/llama.cpp).
