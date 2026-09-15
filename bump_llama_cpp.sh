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

# Upstream publishes two kinds of release tags:
#   - b<N>   : frequent rolling build tags (e.g. b10985)
#   - v<X.Y.Z>: semantic-versioned stable releases (e.g. v0.4.1)
# Track the newest of each kind independently so that a v* release never
# hides a b* release (or vice versa). Output: one tag per line, oldest
# published first, so bumps are applied in chronological order.
fetch_latest_llama_cpp_tags() {
    curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100" \
        | jq -r '
            [ .[] | select(.draft | not) ] as $releases
            | ( $releases | map(select(.tag_name | test("^b[0-9]+$"))) | max_by(.published_at) ),
              ( $releases | map(select(.tag_name | test("^v[0-9]"))) | max_by(.published_at) )
            | select(. != null)
            | "\(.published_at)\t\(.tag_name)"' \
        | sort \
        | cut -f2
}

tag_exists() {
    git rev-parse -q --verify "refs/tags/${1}" >/dev/null 2>&1
}

bump_to_tag() {
    local llama_cpp_tag="${1}"
    local commit_message="bump llama.cpp to ${llama_cpp_tag}"

    echo -e "${YELLOW}Updating llama_cpp_version file to ${llama_cpp_tag}...${NC}"
    upsert_version_value "LLAMA_CPP_VER" "${llama_cpp_tag}"
    rm -f llama_cpp_version.bak

    echo -e "${GREEN}Updated llama_cpp_version:${NC}"
    cat llama_cpp_version

    echo -e "${YELLOW}Committing changes...${NC}"
    git commit -am "${commit_message}"

    echo -e "${YELLOW}Pushing commit...${NC}"
    git push

    echo -e "${YELLOW}Creating tag ${llama_cpp_tag}...${NC}"
    git tag "${llama_cpp_tag}"

    echo -e "${YELLOW}Pushing tag ${llama_cpp_tag}...${NC}"
    git push origin "${llama_cpp_tag}"

    echo -e "${GREEN}Done! ${commit_message}${NC}"
}

main() {
    local latest_tags
    local llama_cpp_tag
    local current_branch
    local uncommitted
    local unpushed
    local unpulled
    local tags_to_bump=()

    echo -e "${YELLOW}Checking git status...${NC}"

    uncommitted=$(git status --porcelain)
    if [ -n "${uncommitted}" ]; then
        echo -e "${RED}Error: You have uncommitted changes:${NC}"
        git status --short
        echo -e "${RED}Please commit or stash these changes before running this script.${NC}"
        exit 1
    fi

    git fetch origin --tags

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

    echo -e "${YELLOW}Fetching latest b* and v* tags from llama.cpp...${NC}"
    latest_tags="$(fetch_latest_llama_cpp_tags)"
    if [ -z "${latest_tags}" ]; then
        echo -e "${RED}Error: Failed to fetch latest tags from llama.cpp${NC}"
        exit 1
    fi

    for llama_cpp_tag in ${latest_tags}; do
        if tag_exists "${llama_cpp_tag}"; then
            echo -e "${GREEN}Latest llama.cpp tag ${llama_cpp_tag} is already mirrored.${NC}"
        else
            echo -e "${GREEN}New llama.cpp tag: ${llama_cpp_tag}${NC}"
            tags_to_bump+=("${llama_cpp_tag}")
        fi
    done

    if [ "${#tags_to_bump[@]}" -eq 0 ]; then
        echo -e "${YELLOW}llama_cpp_version is already up to date. Nothing to do.${NC}"
        exit 0
    fi

    for llama_cpp_tag in "${tags_to_bump[@]}"; do
        bump_to_tag "${llama_cpp_tag}"
    done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
