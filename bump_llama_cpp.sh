#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Checking git status...${NC}"

# Check for uncommitted changes
uncommitted=$(git status --porcelain)
if [ -n "$uncommitted" ]; then
    echo -e "${RED}Error: You have uncommitted changes:${NC}"
    git status --short
    echo -e "${RED}Please commit or stash these changes before running this script.${NC}"
    exit 1
fi

# Fetch remote to get latest state
git fetch origin

# Get current branch
current_branch=$(git rev-parse --abbrev-ref HEAD)

# Check for unpushed commits
unpushed=$(git log origin/${current_branch}..HEAD --oneline 2>/dev/null || echo "")
if [ -n "$unpushed" ]; then
    echo -e "${RED}Error: You have unpushed commits:${NC}"
    echo "$unpushed"
    echo -e "${RED}Please push or reset these commits before running this script.${NC}"
    exit 1
fi

# Check for unpulled commits
unpulled=$(git log HEAD..origin/${current_branch} --oneline 2>/dev/null || echo "")
if [ -n "$unpulled" ]; then
    echo -e "${YELLOW}Found unpulled commits, pulling...${NC}"
    git pull origin ${current_branch}
fi

echo -e "${GREEN}Git is in sync with remote.${NC}"

# Get the latest tag from llama.cpp repo
echo -e "${YELLOW}Fetching latest tag from llama.cpp...${NC}"
llama_cpp_tag=$(curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases" | jq -r '.[].tag_name' | head -n1)

if [ -z "$llama_cpp_tag" ]; then
    echo -e "${RED}Error: Failed to fetch latest tag from llama.cpp${NC}"
    exit 1
fi

echo -e "${GREEN}Latest llama.cpp tag: ${llama_cpp_tag}${NC}"

# Check if llama_cpp_version already has the latest tag
current_ver=$(grep "^LLAMA_CPP_VER=" llama_cpp_version | cut -d'=' -f2)
if [ "$current_ver" = "$llama_cpp_tag" ]; then
    echo -e "${YELLOW}LLAMA_CPP_VER is already ${llama_cpp_tag}. Nothing to do.${NC}"
    exit 0
fi

# Check if tag already exists locally
if git rev-parse "$llama_cpp_tag" >/dev/null 2>&1; then
    echo -e "${YELLOW}Tag ${llama_cpp_tag} already exists locally. Nothing to do.${NC}"
    exit 0
fi

# Update llama_cpp_version file
echo -e "${YELLOW}Updating llama_cpp_version file...${NC}"
sed -i.bak "s/^LLAMA_CPP_VER=.*/LLAMA_CPP_VER=${llama_cpp_tag}/" llama_cpp_version
rm -f llama_cpp_version.bak

# Show the change
echo -e "${GREEN}Updated llama_cpp_version:${NC}"
cat llama_cpp_version

# Commit the change
echo -e "${YELLOW}Committing changes...${NC}"
git commit -am "bump llama.cpp to ${llama_cpp_tag}"

# Push the commit
echo -e "${YELLOW}Pushing commit...${NC}"
git push

# Create and push the tag
echo -e "${YELLOW}Creating tag ${llama_cpp_tag}...${NC}"
git tag "${llama_cpp_tag}"

echo -e "${YELLOW}Pushing tag ${llama_cpp_tag}...${NC}"
git push origin "${llama_cpp_tag}"

echo -e "${GREEN}Done! Successfully bumped llama.cpp to ${llama_cpp_tag}${NC}"
