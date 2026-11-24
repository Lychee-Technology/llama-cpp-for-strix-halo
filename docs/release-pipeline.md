# Release Pipeline and Versioning

Upstream tracking is maintainer-triggered (there is no scheduled cron job in GitHub Actions), while compilation and GitHub Release publishing are automated on tag pushes:

```mermaid
flowchart LR
    A["Upstream ggml-org/llama.cpp Release (b* or v*)"] --> B["Maintainer runs ./bump_llama_cpp.sh"]
    B --> C["Updates llama_cpp_version, commits, and pushes Git tag"]
    C --> D["GitHub Actions: Build Matrix (ROCm 7.14.1, 10.1.0, 10.2.0)"]
    D --> E["Packages build/bin/*.tar.xz"]
    E --> F["GitHub Release published"]
```

- **Maintainer step:** [`bump_llama_cpp.sh`](../bump_llama_cpp.sh) queries `ggml-org/llama.cpp` releases, tracks the newest `b*` rolling tag and `v*` semantic tag independently, updates [`llama_cpp_version`](../llama_cpp_version), and pushes a matching commit and Git tag.
- **Automated CI step:** Pushing a `b*` or `v*` tag triggers [`.github/workflows/build-and-release.yml`](../.github/workflows/build-and-release.yml), which builds all three ROCm variants on `blacksmith-2vcpu-ubuntu-2404` and publishes the `.tar.xz` archives via `softprops/action-gh-release`.
- **Verification boundary:** CI validates compilation and packaging only; no Strix Halo GPU execution tests run in GitHub Actions.
