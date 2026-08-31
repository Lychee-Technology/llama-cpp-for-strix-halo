#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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

main() {
    local llama_cpp_tag
    local current_llama_cpp_ver
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

    current_llama_cpp_ver="$(get_version_value "LLAMA_CPP_VER")"

    if [ "${current_llama_cpp_ver}" = "${llama_cpp_tag}" ]; then
        echo -e "${YELLOW}llama_cpp_version is already up to date. Nothing to do.${NC}"
        exit 0
    fi

    if [ "${current_llama_cpp_ver}" != "${llama_cpp_tag}" ] && git rev-parse "${llama_cpp_tag}" >/dev/null 2>&1; then
        echo -e "${RED}Error: Tag ${llama_cpp_tag} already exists locally.${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Updating llama_cpp_version file...${NC}"
    upsert_version_value "LLAMA_CPP_VER" "${llama_cpp_tag}"
    rm -f llama_cpp_version.bak

    echo -e "${GREEN}Updated llama_cpp_version:${NC}"
    cat llama_cpp_version

    commit_message="bump llama.cpp to ${llama_cpp_tag}"

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
