#!/usr/bin/env bash
# Sanitized publish script for ai-office-kernel
set -euo pipefail

REPO_NAME="ai-office-kernel"
DESCRIPTION="Telegram-driven multi-agent AI office kernel (Sanitized version)"

echo "🔄 Cleaning up git state..."
# We remove the .git folder to start completely fresh and avoid picking up old history/files
rm -rf .git

echo "🆕 Initializing new git repository..."
git init

echo "📝 Checking .gitignore..."
# Ensure we don't pick up the personal prompts or other projects
git add .gitignore
git add .

echo "🔍 Verifying files to be committed (excluding ignored)..."
git status

echo "💾 Committing sanitized files..."
git commit -m "Initial commit: AI-Office Kernel MVP (Sanitized)"

if ! command -v gh >/dev/null 2>&1; then
    echo "❌ Error: GitHub CLI (gh) is not installed."
    exit 1
fi

echo "🚀 Creating/Updating GitHub repository..."
# Check if repo exists, if so just push, else create
if ! gh repo view "$REPO_NAME" >/dev/null 2>&1; then
    gh repo create "$REPO_NAME" --public --description "$DESCRIPTION" --source=. --remote=origin --push
else
    git remote add origin "https://github.com/$(gh api user -q .login)/$REPO_NAME.git"
    git branch -M main
    git push -u origin main --force
fi

echo "✅ Done! Project published to GitHub without personal data or extra projects."
echo "💡 Your personal prompts are safe in 'prompts/personal/' and are NOT uploaded."
