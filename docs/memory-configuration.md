# Unified Memory Configuration for AMD Strix Halo (Linux)

AMD Strix Halo APUs (`gfx1151`) share physical LPDDR5X system memory between the Zen 5 CPU cores and the integrated GPU rather than using separate discrete VRAM. On Linux, how much of that unified memory the GPU can allocate depends on your **BIOS UMA setting**, **Linux kernel version**, and **TTM (Translation Table Maps)** limits.

> [!CAUTION]
> Modifying kernel boot parameters, `/etc/modprobe.d/` files, or initramfs images requires root privileges and affects system boot and memory stability. Read the [Check Before Changing](#check-before-changing) and [Safety and Rollback](#safety-and-rollback) sections before making changes.

## Table of Contents

- [How Strix Halo Memory Works on Linux](#how-strix-halo-memory-works-on-linux)
- [Check Before Changing](#check-before-changing)
- [BIOS UMA Setting](#bios-uma-setting)
- [Calculating TTM Page Limits](#calculating-ttm-page-limits)
- [Applying Kernel Parameters by Distribution](#applying-kernel-parameters-by-distribution)
- [Verifying the New Limits](#verifying-the-new-limits)
- [Safety and Rollback](#safety-and-rollback)

## How Strix Halo Memory Works on Linux

1. **BIOS-reserved UMA (carve-out VRAM):** A fixed slice of RAM reserved at boot exclusively for graphics. Keeping this large permanently hides RAM from the Linux OS.
2. **GTT (Graphics Translation Table) via TTM:** System memory dynamically mapped for GPU access by the Linux `amdgpu` and `ttm` subsystems. By default, TTM limits GPU-mappable system memory to roughly **50% of total physical RAM** (around 64 GiB on a 128 GiB machine).
3. **Inference memory budget:** Running an LLM on GPU requires space for **model weights**, **KV cache** (grows with context length and batch size), **compute scratch buffers**, and **Linux OS + desktop processes**. You cannot safely assign 100% of physical RAM to GPU allocations.

### Kernel Version Caveat

- **Linux `6.16.9+` / `6.18+`:** Upstream `amdkfd`/ROCm memory-allocation fixes resolved earlier issues where ROCm/HIP allocations could stall near 15.5 GiB despite larger GTT settings.
- **Older kernels (`<= 6.15`):** Users frequently encountered ROCm/HIP allocation caps even after raising TTM limits. Upgrading to a modern kernel (`6.16.9+` or `6.18+`) is strongly recommended before tuning parameters.

## Check Before Changing

If your models and KV cache fit within ~50% of your system RAM (for example, up to ~60 GiB on a 128 GiB system or ~30 GiB on a 64 GiB system), **you do not need to change kernel parameters**.

Inspect your current settings with read-only commands first:

```bash
# 1. Check active kernel version
uname -r

# 2. Check current TTM limits (in 4 KiB pages)
cat /sys/module/ttm/parameters/pages_limit
cat /sys/module/ttm/parameters/page_pool_size

# 3. Check what amdgpu initialized at boot (may require sudo on some distros)
sudo dmesg | grep -i "amdgpu.*memory"
```

Example `dmesg` output shows the fixed VRAM carve-out and the dynamic GTT pool in MiB:

```text
[drm] amdgpu: 512M of VRAM memory ready
[drm] amdgpu: 65536M of GTT memory ready
```

You can convert the `pages_limit` value to GiB with:

```bash
awk '{ printf "%.2f GiB (%d pages)\n", ($1 * 4096) / (1024^3), $1 }' /sys/module/ttm/parameters/pages_limit
```

## BIOS UMA Setting

On Strix Halo systems, set the BIOS **UMA Frame Buffer Size** (or equivalent iGPU VRAM allocation setting) to a minimal value such as **512 MB** or **1–2 GB** (unless another non-LLM application specifically requires a larger static carve-out).

This leaves nearly all physical memory available to Linux while allowing `amdgpu` and ROCm (`GGML_HIP_UMA=ON`) to allocate GTT memory dynamically up to the TTM limit.

## Calculating TTM Page Limits

Modern Linux kernels configure GTT capacity through `ttm.pages_limit` and `ttm.page_pool_size`. Both values are specified in **4 KiB pages** (assuming standard 4096-byte x86_64 pages; verify with `getconf PAGESIZE`).

- **Legacy parameter note:** `amdgpu.gttsize` is **deprecated** in modern Linux kernels (`Configuring gttsize via module parameter is deprecated, please use ttm.pages_limit`) and does not need to be set on current kernels.
- **Unit note:** `1 GiB` = `1024^3` bytes = `262,144` pages of `4 KiB`. Leaving at least **16–20 GiB** for the Linux kernel, filesystem cache, desktop session, and host processes helps avoid OOM kills on 128 GiB systems.

$$\text{pages} = \text{Target GiB} \times 262144$$

| Physical RAM | Target GPU-Mappable GTT | Recommended OS Headroom | `ttm.pages_limit` / `ttm.page_pool_size` | Notes |
|---|---|---|---|---|
| 64 GiB | 48 GiB | ~16 GiB | `12582912` | Conservative headroom for OS + host buffers |
| 128 GiB | ~97.7 GiB (`100 GB` decimal / `25600000`) | ~30 GiB | `25600000` | Conservative baseline used in earlier community setups |
| 128 GiB | 108 GiB | ~20 GiB | `28311552` | Balanced target for large models while preserving OS stability |
| 128 GiB | 112 GiB | ~16 GiB | `29360128` | Higher ceiling; monitor system memory under heavy context |

> [!WARNING]
> Pushing `ttm.pages_limit` to 120 GiB (`31457280` pages) or higher on a 128 GiB system leaves very little headroom for the Linux kernel and desktop applications, increasing the risk of OOM termination or system freezes under memory pressure.

## Applying Kernel Parameters by Distribution

Passing `ttm.pages_limit=<PAGES> ttm.page_pool_size=<PAGES>` on the **kernel command line** works regardless of whether `ttm` is compiled as a loadable module (`=m`) or built directly into the kernel (`=y`).

Replace `<PAGES>` below with your chosen page count (for example, `28311552` for 108 GiB on a 128 GiB machine).

### Option A: Kernel Command Line (Recommended Across Distributions)

#### Fedora / RHEL-based (`grubby`)

```bash
# Back up current kernel args before modifying
grubby --info=DEFAULT | tee ~/grubby-default-backup.txt

sudo grubby --update-kernel=ALL --args="ttm.pages_limit=<PAGES> ttm.page_pool_size=<PAGES>"
sudo reboot
```

#### Ubuntu / Debian (GRUB)

1. Back up `/etc/default/grub`:

   ```bash
   sudo cp /etc/default/grub /etc/default/grub.bak
   ```

2. Edit `/etc/default/grub` and append the parameters inside `GRUB_CMDLINE_LINUX_DEFAULT`:

   ```text
   GRUB_CMDLINE_LINUX_DEFAULT="quiet splash ttm.pages_limit=<PAGES> ttm.page_pool_size=<PAGES>"
   ```

   Keep your existing flags (such as `quiet splash`) intact.

3. Update GRUB and reboot:

   ```bash
   sudo update-grub
   sudo reboot
   ```

#### Arch Linux / CachyOS

- **If using GRUB:** Back up `/etc/default/grub` (`sudo cp /etc/default/grub /etc/default/grub.bak`), append `ttm.pages_limit=<PAGES> ttm.page_pool_size=<PAGES>` to `GRUB_CMDLINE_LINUX_DEFAULT`, then run:

  ```bash
  sudo grub-mkconfig -o /boot/grub/grub.cfg
  sudo reboot
  ```

- **If using `systemd-boot`:** Back up your active entry under `/boot/loader/entries/` and append `ttm.pages_limit=<PAGES> ttm.page_pool_size=<PAGES>` to the `options` line, then reboot.

### Option B: `modprobe.d` + Initramfs (Only When `ttm` Is a Module)

If you prefer module configuration via `/etc/modprobe.d/ttm-memory.conf`:

```text
options ttm pages_limit=<PAGES>
options ttm page_pool_size=<PAGES>
```

Because `amdgpu` and `ttm` are normally loaded during early boot (initramfs), you must regenerate your initramfs using your distribution's tool before rebooting:

- **Ubuntu / Debian:** `sudo update-initramfs -u -k all`
- **Arch Linux / CachyOS (`mkinitcpio`):** `sudo mkinitcpio -P`
- **Fedora / dracut-based systems:** `sudo dracut --regenerate-all --force`

Then run `sudo reboot`.

## Verifying the New Limits

After rebooting, run:

```bash
cat /sys/module/ttm/parameters/pages_limit
cat /sys/module/ttm/parameters/page_pool_size
sudo dmesg | grep -i "amdgpu.*memory"
```

Confirm that `pages_limit` matches your configured value and `dmesg` reports the expanded `GTT memory ready` size in MiB.

## Safety and Rollback

If you experience OOM kills, graphical instability, or boot issues after changing memory limits:

1. **Temporary one-boot bypass:** In the GRUB menu at boot, press `e`, remove `ttm.pages_limit=...` and `ttm.page_pool_size=...` from the `linux` line, and press `Ctrl+X` or `F10` to boot with defaults.
2. **Permanent rollback (Fedora `grubby`):**

   ```bash
   sudo grubby --update-kernel=ALL --remove-args="ttm.pages_limit ttm.page_pool_size"
   sudo reboot
   ```

3. **Permanent rollback (GRUB / `systemd-boot`):** Restore `/etc/default/grub.bak` (or remove the parameters from your `systemd-boot` entry), re-run `sudo update-grub` or `sudo grub-mkconfig -o /boot/grub/grub.cfg`, and reboot.
4. **Permanent rollback (`modprobe.d`):** Remove `/etc/modprobe.d/ttm-memory.conf`, regenerate initramfs with your distribution's tool, and reboot.
