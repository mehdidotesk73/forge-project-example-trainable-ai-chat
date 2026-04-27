#!/usr/bin/env bash
# setup.sh — Create the project venv and install dependencies.
# Run this once after cloning, or after adding new packages to requirements.txt.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
# shellcheck disable=SC1091
if [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate   # Windows / Git Bash
else
    source .venv/bin/activate
fi
pip install -q -r requirements.txt
echo "Setup complete. Activate the venv with:"
echo "  source .venv/bin/activate     (Mac/Linux)"
echo "  .venv\\Scripts\\activate       (Windows)"
