#!/usr/bin/env bash
# Script to publish ai-office-kernel to GitHub
set -euo pipefail

REPO_NAME="ai-office-kernel"
DESCRIPTION="Telegram-driven multi-agent AI office kernel for local and CLI-backed agents"

echo "Checking git status..."
if [ ! -d ".git" ]; then
    echo "Initializing git repository..."
    git init
fi

git add .

if git diff --cached --quiet; then
    echo "No changes to commit."
else
    echo "Committing changes..."
    git commit -m "Initial commit: AI-Office Kernel MVP"
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "Error: GitHub CLI (gh) is not installed. Please install it first."
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "Error: gh is not authenticated. Run 'gh auth login' first."
    exit 1
fi

echo "Checking if repository already exists on GitHub..."
if ! gh repo view "$REPO_NAME" >/dev/null 2>&1; then
    echo "Creating GitHub repository..."
    gh repo create "$REPO_NAME" --public --description "$DESCRIPTION" --source=. --remote=origin --push
else
    echo "Repository already exists. Pushing to origin..."
    git push -u origin main || git push -u origin master
fi

echo "Done! Project published to GitHub."
