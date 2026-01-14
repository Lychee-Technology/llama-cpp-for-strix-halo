
# **llama.cpp for AMD Strix Halo (Linux)**

This repository provides **prebuilt and reproducible Linux binaries of llama.cpp specifically optimized for AMD Strix Halo (AMD AI APU) platforms**.

## **Why This Project Exists**

AMD Strix Halo–based systems are uniquely capable of running **large local LLMs** thanks to their ability to access **well over 100 GB of unified system memory**. At the time of writing (Jan 2026):

* **`llama.cpp` is the only local LLM runtime that can practically leverage >80 GB memory on Strix Halo**
* Official `llama.cpp` releases **do not provide Linux binaries tuned for Strix Halo**
* Default Linux kernel and ROCm settings **severely limit usable VRAM / GPU-addressable memory**
* Building `llama.cpp` correctly for Strix Halo requires:
  * Non-default compile parameters
  * Correct ROCm installation

This repository exists to:

* Provide **ready-to-use Linux builds of llama.cpp for Strix Halo**
* Document **exact system tuning steps** required to unlock large-memory inference
* Lower the barrier for running **70B–100B+ models locally** on AMD AI APUs

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

### **How to Apply (GRUB Example)**

Edit `/etc/default/grub`:

Update:

`GRUB_CMDLINE_LINUX_DEFAULT="quiet splash ttm.page_pool_size=25600000 ttm.pages_limit=25600000"`

Then apply and reboot:

```sh
sudo update-grub  
sudo reboot  
```

These values are conservative but proven stable on Strix Halo with 128GB memory. Advanced users may experiment further at their own risk.  

## **Installing ROCm (Required)**

`llama.cpp` on Strix Halo relies on **ROCm** for GPU acceleration.

Follow AMD’s official [ROCm instructions](https://rocm.docs.amd.com/projects/install-on-linux/en/docs-7.1.1/install/install-methods/package-manager-index.html)


## **Acknowledgements**

This project would not exist without the work and documentation from **Jeff Geerling**. In particular:
* [Increasing VRAM allocation on AMD AI APUs under Linux](https://www.jeffgeerling.com/blog/2025/increasing-vram-allocation-on-amd-ai-apus-under-linux/)
* [Beowulf AI Cluster discussion](https://github.com/geerlingguy/beowulf-ai-cluster/issues/5)

Thank you for making advanced Linux + AMD hardware knowledge accessible to the community.

## **Disclaimer**

This project is **not affiliated with AMD or the llama.cpp maintainers**.
Use at your own risk. Kernel parameter changes can affect system stability.
