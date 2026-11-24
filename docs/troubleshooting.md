# Troubleshooting

Follow **Observe → Diagnose → Resolve → Verify** before modifying system files.

| Symptom | Possible Cause | Diagnostic Action | Resolution |
|---|---|---|---|
| `error while loading shared libraries: libllama.so` or `libhip*.so` | Dynamic linker cannot find bundled `llama.cpp` libs or ROCm `lib/` | `ldd ./llama-cli \| grep "not found"` | Set `export LD_LIBRARY_PATH="$PWD:/opt/rocm/lib:${LD_LIBRARY_PATH:-}"` (or your ROCm lib path) and re-run `ldd`. |
| `--list-devices` shows no HIP/GPU device | Missing `render`/`video` group, `amdgpu` driver issue, or ROCm mismatch | `groups`; `ls -l /dev/kfd /dev/dri`; `rocminfo` | Add user to `render,video`, re-login, and confirm `rocminfo` lists `gfx1151`. |
| Inference runs slowly on CPU | `-ngl` omitted/zero, or HIP backend failed to initialize | Check stderr startup logs for `offloaded` layers and HIP errors | Pass `-ngl 99` (or `auto`) and resolve any HIP/library errors shown in startup logs. |
| Large model fails to allocate GPU memory | Default TTM (~50% RAM) reached, or Linux kernel `<= 6.15` KFD limit | `uname -r`; `cat /sys/module/ttm/parameters/pages_limit`; `sudo dmesg \| grep -i "amdgpu.*memory"` | Upgrade to kernel `6.16.9+`/`6.18+` if older; configure TTM limits via [memory-configuration.md](memory-configuration.md). |
| `Illegal instruction (core dumped)` | Host CPU is not Zen 5 / lacks AVX-512 instructions | `lscpu \| grep -i avx512` | These builds require Zen 5 / AVX-512; rebuild from source for your CPU target if needed (see [build-from-source.md](build-from-source.md)). |
| System freezes or OOM kills during inference | TTM limit set too close to physical RAM without OS headroom | `dmesg -T \| grep -iE "oom\|killed process"` | Lower `ttm.pages_limit` (leave ~16–20 GiB for OS on 128 GiB systems) or use smaller quant/context. |
