
# **llama.cpp for AMD Strix Halo (Linux)**

This repository provides **prebuilt and reproducible Linux binaries of llama.cpp specifically optimized for AMD Strix Halo (AMD AI APU) platforms**.

## **Why This Project Exists**

AMD Strix Halo–based systems are uniquely capable of running **large local LLMs** thanks to their ability to access **well over 100 GB of unified system memory**. When this project started (Jan 2026):

* **`llama.cpp` was the primary local LLM runtime that could practically leverage >80 GB memory on Strix Halo**
* Official `llama.cpp` releases **do not provide Linux binaries tuned for Strix Halo**
* Default Linux kernel and ROCm settings **severely limit usable VRAM / GPU-addressable memory**
* Building `llama.cpp` correctly for Strix Halo requires:
  * Non-default compile parameters
  * Correct ROCm installation

This repository exists to:

* Provide **ready-to-use Linux builds of llama.cpp for Strix Halo**
* Document **exact system tuning steps** required to unlock large-memory inference
* Lower the barrier for running **70B–120B+ models locally** on AMD AI APUs

## **Model-Specific Runtime Choice: [Loom](https://github.com/ROCm/hrx-system) & [Strata](https://github.com/Niko1221/Strata) vs. `llama.cpp`**

While `llama.cpp` remains a versatile general-purpose runtime for Strix Halo, **it is not always the fastest option for every model architecture**. For models using specialized architectures—such as **Looped / Recurrent Transformers** (e.g., **Inkling 2 Mini**)—standard `llama.cpp` (ROCm) and tools built on top of it (such as Ollama or Unsloth Desktop) lack kernels optimized for repeated/looped layer computation, resulting in significantly lower throughput.

Based on community benchmarks on AMD Strix Halo (`gfx1151`, 128 GB unified memory, Nov 2026), **[Loom](https://github.com/ROCm/hrx-system)** and **[Strata](https://github.com/Niko1221/Strata)** can deliver substantially better performance on these architectures:

### **1. [Loom](https://github.com/ROCm/hrx-system) (Highest Tested Performance for Looped Architectures)**

[Loom](https://pypi.org/project/hrx-loom/) (`hrx-loom`) is developed and tested directly on Strix Halo hardware and provides mature support for models like Inkling 2 Mini.

* **Observed Strix Halo (`gfx1151`) benchmarks (Inkling 2 Mini, `UD-Q4_K_XL` ~17.6 GB):**
  * **Decode:** **44–44.8 tok/s** vs. `llama.cpp` (ROCm) at **15.7–23.8 tok/s** (~1.88x–2.8x faster)
  * **Prefill:** **5.9x–7x+ faster** than `llama.cpp` (e.g., **1,465 tok/s**, and >7x speedup on 8K document prefill)
* **Quick Start (example for [`loom-fun/unsloth-Inkling-2-Mini-loom`](https://huggingface.co/loom-fun/unsloth-Inkling-2-Mini-loom)):**
  ```sh
  pipx install "hrx-loom[hf]==0.2.0"
  loom-fetch huggingface:loom-fun/unsloth-Inkling-2-Mini-loom:chat --materialize=copy --output=./inkling-2-mini
  loom-serve --model=./inkling-2-mini/model.json '--device=amdgpu://?completion_spin_ns=4294967295' --pool_backing=fixed --parameter_mode=preload
  ```
  On a 128 GB Strix Halo system, the `UD-Q4_K_XL` package (~17.6 GB) leaves ample memory for ultra-long contexts (up to 1M tokens).

### **2. [Strata](https://github.com/Niko1221/Strata) (Native `gfx1151` Support, Comparable Speed)**

[Strata](https://github.com/Niko1221/Strata) also provides native engine support for specialized models like Inkling 2 Mini with prebuilt HIP packages targeting **Strix Halo (`gfx1151`)**.

* **Observed Strix Halo benchmarks ([`strata-engine/Inkling-2-Mini-Strata`](https://huggingface.co/strata-engine/Inkling-2-Mini-Strata), `UD-Q4_K_XL`):** Developer-reported decode speeds of **46.6–49.1 tok/s**, putting it in the same performance tier as Loom.
* **Deployment:** Uses the dedicated `strata-inkling` engine package with precompiled `gfx1151` HIP binaries.

### **3. Notes on `llama.cpp` Variants and Wrappers**

* **Standard `llama.cpp` / Unsloth Desktop:** Convenient for general GGUF models, but runs looped-transformer models noticeably slower than Loom or Strata because default kernels are not specialized for looped layers.
* **Community patches (e.g., `llama.cpp-inkling2-amd`):** Forks targeting AMD RDNA3 optimizations primarily tune for discrete desktop GPUs (`gfx1100`–`gfx1103`), so Strix Halo (`gfx1151`) may not see their full optimization gains.

## **Platform Overview: Strix Halo Memory Model**

Strix Halo uses **unified system memory** instead of discrete VRAM. However, Linux does **not automatically expose this memory efficiently to the GPU**.

By default:

* GPU-visible memory is capped
* Large Vulkan / ROCm allocations fail
* LLMs crash or silently fall back to CPU

To fix this, **TTM (Translation Table Maps) limits must be adjusted**.

## **Kernel Parameters: Unlocking Large GPU Memory**

You **must** increase TTM limits to allow the GPU to map large portions of system RAM.

### **Required Kernel Parameters**

Add the following parameters to your kernel command line (for devices with 128GB memory):

`ttm.page_pool_size=25600000 ttm.pages_limit=25600000`

### **What These Parameters Do**

| Parameter          | Purpose                                                       |
| ------------------ | ------------------------------------------------------------- |
| ttm.page_pool_size | Size of the page pool (in pages) used for GPU memory mappings |
| ttm.pages_limit    | Maximum number of pages the GPU can map                       |

With 4 KB pages, the above values allow **100+ GB of GPU-accessible memory**, which is critical for large LLM inference.

### **How to Apply**

Edit `/etc/modprobe.d/increase_amd_memory.conf`:

Update:

```
options ttm pages_limit=25600000 #4k per page, 100GB total
options ttm page_pool_size=25600000
```

Then apply and reboot:

```sh
sudo update-initramfs -u -k all
sudo reboot  
```

These values are conservative but proven stable on Strix Halo with 128GB memory. Advanced users may experiment further at their own risk.

> **Note (GTT / CachyOS & Alternative Runtimes):** Unlocking GPU-addressable unified memory is required regardless of whether you use **`llama.cpp`**, **[Loom](https://github.com/ROCm/hrx-system)**, or **[Strata](https://github.com/Niko1221/Strata)**—otherwise Linux caps GTT at roughly 50% of system RAM (~64 GB on a 128 GB machine). On distributions such as CachyOS, users targeting ~120 GB GTT capacity often configure `/etc/modprobe.d/increase_amd_memory.conf` with:
> ```
> options amdgpu gttsize=120000
> options ttm pages_limit=31457280
> options ttm page_pool_size=15728640
> ```
> Remember to regenerate your initramfs and reboot after making changes.

## **Installing ROCm (Required)**

`llama.cpp` on Strix Halo relies on **ROCm** for GPU acceleration.

Follow AMD’s official [ROCm instructions](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/install-methods/package-manager-index.html)


## **Acknowledgements**

This project would not exist without the work and documentation from **Jeff Geerling**. In particular:
* [Increasing VRAM allocation on AMD AI APUs under Linux](https://www.jeffgeerling.com/blog/2025/increasing-vram-allocation-on-amd-ai-apus-under-linux/)
* [Beowulf AI Cluster discussion](https://github.com/geerlingguy/beowulf-ai-cluster/issues/5)

Thank you for making advanced Linux + AMD hardware knowledge accessible to the community.

## **Disclaimer**

This project is **not affiliated with AMD or the llama.cpp maintainers**.
Use at your own risk. Kernel parameter changes can affect system stability.
