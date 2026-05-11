#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Install Python 3.11+ first."
  exit 1
fi

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .

python -m ai_office_kernel setup --auto "$@"

echo
echo "Bootstrap finished."
echo "Next shell:"
echo "  cd $(pwd)"
echo "  source .venv/bin/activate"
echo "  set -a && source .env && set +a"
echo "  ai-office-kernel telegram"
