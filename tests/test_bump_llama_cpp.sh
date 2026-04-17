#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "${repo_root}/bump_llama_cpp.sh"

sample_index_html='
<a href="therock-dist-linux-gfx1151-7.13.0a20260416.tar.gz">older</a>
<a href="therock-dist-linux-gfx1151-7.13.0a20260417.tar.gz">newer</a>
<a href="therock-dist-linux-gfx1151-7.12.0a20260418.tar.gz">different-series</a>
'

latest_tarball="$(extract_latest_rocm_713_tarball "${sample_index_html}")"
expected_tarball="therock-dist-linux-gfx1151-7.13.0a20260417.tar.gz"

if [ "${latest_tarball}" != "${expected_tarball}" ]; then
    echo "expected latest tarball ${expected_tarball}, got ${latest_tarball}" >&2
    exit 1
fi

download_url="$(build_rocm_tarball_url "https://therock-nightly-tarball.s3.amazonaws.com/" "${latest_tarball}")"
expected_url="https://therock-nightly-tarball.s3.amazonaws.com/${expected_tarball}"

if [ "${download_url}" != "${expected_url}" ]; then
    echo "expected download URL ${expected_url}, got ${download_url}" >&2
    exit 1
fi

echo "ok"
