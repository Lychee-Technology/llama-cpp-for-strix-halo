#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ROCM_7_13_TARBALL_VAR="THEROCK_TARBALL_NAME"

extract_latest_rocm_713_tarball() {
    local index_html="${1}"

    printf '%s' "${index_html}" | \
        grep -oE 'therock-dist-linux-gfx1151-7\.13[^"]+\.tar\.gz' | \
        sort -V | \
        tail -n 1
}

build_rocm_tarball_url() {
    local base_url="${1%/}"
    local tarball_name="${2}"

    printf '%s\n' "${base_url}/${tarball_name}"
}

get_version_value() {
    local key="${1}"

    grep "^${key}=" llama_cpp_version | cut -d'=' -f2-
}

upsert_version_value() {
    local key="${1}"
    local value="${2}"

    if grep -q "^${key}=" llama_cpp_version; then
        sed -i.bak "s/^${key}=.*/${key}=${value}/" llama_cpp_version
    else
        printf '%s=%s\n' "${key}" "${value}" >> llama_cpp_version
    fi
}

fetch_latest_llama_cpp_tag() {
    curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases" | jq -r '.[].tag_name' | head -n 1
}

fetch_latest_rocm_713_tarball() {
    local index_html

    index_html="$(curl -s "https://therock-nightly-tarball.s3.amazonaws.com/index.html")"
    extract_latest_rocm_713_tarball "${index_html}"
}

main() {
    local llama_cpp_tag
    local rocm_713_tarball
    local rocm_713_tarball_url
    local current_llama_cpp_ver
    local current_rocm_713_tarball
    local current_branch
    local uncommitted
    local unpushed
    local unpulled
    local commit_message

    echo -e "${YELLOW}Checking git status...${NC}"

    uncommitted=$(git status --porcelain)
    if [ -n "${uncommitted}" ]; then
        echo -e "${RED}Error: You have uncommitted changes:${NC}"
        git status --short
        echo -e "${RED}Please commit or stash these changes before running this script.${NC}"
        exit 1
    fi

    git fetch origin

    current_branch=$(git rev-parse --abbrev-ref HEAD)

    unpushed=$(git log origin/${current_branch}..HEAD --oneline 2>/dev/null || echo "")
    if [ -n "${unpushed}" ]; then
        echo -e "${RED}Error: You have unpushed commits:${NC}"
        echo "${unpushed}"
        echo -e "${RED}Please push or reset these commits before running this script.${NC}"
        exit 1
    fi

    unpulled=$(git log HEAD..origin/${current_branch} --oneline 2>/dev/null || echo "")
    if [ -n "${unpulled}" ]; then
        echo -e "${YELLOW}Found unpulled commits, pulling...${NC}"
        git pull origin "${current_branch}"
    fi

    echo -e "${GREEN}Git is in sync with remote.${NC}"

    echo -e "${YELLOW}Fetching latest tag from llama.cpp...${NC}"
    llama_cpp_tag="$(fetch_latest_llama_cpp_tag)"
    if [ -z "${llama_cpp_tag}" ]; then
        echo -e "${RED}Error: Failed to fetch latest tag from llama.cpp${NC}"
        exit 1
    fi
    echo -e "${GREEN}Latest llama.cpp tag: ${llama_cpp_tag}${NC}"

    echo -e "${YELLOW}Fetching latest ROCm 7.13 tarball...${NC}"
    rocm_713_tarball="$(fetch_latest_rocm_713_tarball)"
    if [ -z "${rocm_713_tarball}" ]; then
        echo -e "${RED}Error: Failed to fetch latest ROCm 7.13 tarball${NC}"
        exit 1
    fi
    rocm_713_tarball_url="$(build_rocm_tarball_url "https://therock-nightly-tarball.s3.amazonaws.com/" "${rocm_713_tarball}")"
    echo -e "${GREEN}Latest ROCm 7.13 tarball: ${rocm_713_tarball}${NC}"
    echo -e "${GREEN}ROCm 7.13 tarball URL: ${rocm_713_tarball_url}${NC}"

    current_llama_cpp_ver="$(get_version_value "LLAMA_CPP_VER")"
    current_rocm_713_tarball="$(get_version_value "${ROCM_7_13_TARBALL_VAR}" || get_version_value "ROCM_7_13_TARBALL" || true)"

    if [ "${current_llama_cpp_ver}" = "${llama_cpp_tag}" ] && [ "${current_rocm_713_tarball}" = "${rocm_713_tarball}" ]; then
        echo -e "${YELLOW}llama_cpp_version is already up to date. Nothing to do.${NC}"
        exit 0
    fi

    if [ "${current_llama_cpp_ver}" != "${llama_cpp_tag}" ] && git rev-parse "${llama_cpp_tag}" >/dev/null 2>&1; then
        echo -e "${RED}Error: Tag ${llama_cpp_tag} already exists locally.${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Updating llama_cpp_version file...${NC}"
    upsert_version_value "LLAMA_CPP_VER" "${llama_cpp_tag}"
    upsert_version_value "${ROCM_7_13_TARBALL_VAR}" "${rocm_713_tarball}"
    rm -f llama_cpp_version.bak

    echo -e "${GREEN}Updated llama_cpp_version:${NC}"
    cat llama_cpp_version

    if [ "${current_llama_cpp_ver}" != "${llama_cpp_tag}" ] && [ "${current_rocm_713_tarball}" != "${rocm_713_tarball}" ]; then
        commit_message="bump llama.cpp to ${llama_cpp_tag} and ROCm 7.13 tarball to ${rocm_713_tarball}"
    elif [ "${current_llama_cpp_ver}" != "${llama_cpp_tag}" ]; then
        commit_message="bump llama.cpp to ${llama_cpp_tag}"
    else
        commit_message="bump ROCm 7.13 tarball to ${rocm_713_tarball}"
    fi

    echo -e "${YELLOW}Committing changes...${NC}"
    git commit -am "${commit_message}"

    echo -e "${YELLOW}Pushing commit...${NC}"
    git push

    if [ "${current_llama_cpp_ver}" != "${llama_cpp_tag}" ]; then
        echo -e "${YELLOW}Creating tag ${llama_cpp_tag}...${NC}"
        git tag "${llama_cpp_tag}"

        echo -e "${YELLOW}Pushing tag ${llama_cpp_tag}...${NC}"
        git push origin "${llama_cpp_tag}"
    fi

    echo -e "${GREEN}Done! ${commit_message}${NC}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
