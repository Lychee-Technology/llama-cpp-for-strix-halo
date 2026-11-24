# Runtime Selection

`llama.cpp` is a general-purpose GGUF inference runtime covering a wide range of dense and MoE models. However, specialized model architectures may benefit from dedicated engines with custom kernels:

| Runtime | Intended Workloads | Model Format | Strengths | Limitations |
|---|---|---|---|---|
| **`llama.cpp` (this distribution)** | General-purpose local LLMs, OpenAI-compatible serving, broad quantization support | GGUF | Mature tooling (`llama-cli`, `llama-server`, `llama-bench`), broad model coverage, Strix Halo `gfx1151` HIP + UMA build | Generic kernels may underperform specialized engines on niche architectures |
| **[Loom (`ROCm/hrx-system`)](https://github.com/ROCm/hrx-system)** | Heterogeneous AMD execution and specialized architectures such as looped/recurrent transformers (e.g., Inkling 2 Mini) | Loom model packages / GGUF checkpoints | JIT/kernel stack (`hrx-loom`) tuned for architectures like looped transformers on Strix Halo | Narrower model/ecosystem scope compared to `llama.cpp`; separate runtime install |
| **[Strata (`Niko1221/Strata`)](https://github.com/Niko1221/Strata)** | Large MoE and specialized architectures such as Inkling 2 Mini | Strata engine packages | Native `gfx1151` support and model-specific optimizations for supported architectures | Separate engine ecosystem and package format; verify current model support upstream |

Refer directly to the [Loom](https://github.com/ROCm/hrx-system) and [Strata](https://github.com/Niko1221/Strata) repositories for up-to-date model compatibility, installation commands, and developer benchmarks. Regardless of which GPU runtime you choose, Linux TTM memory limits still govern large allocations on Strix Halo (see [memory-configuration.md](memory-configuration.md)).
