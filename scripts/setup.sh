#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! python3 -m ensurepip --version >/dev/null 2>&1; then
  cat >&2 <<'EOF'
python3 venv/pip support is not available.

On Debian/Ubuntu, install it with:
  sudo apt install python3-venv python3-pip

Then rerun:
  scripts/setup.sh
EOF
  exit 1
fi

if ! command -v bd >/dev/null 2>&1; then
  echo "Installing Beads bd CLI..."
  if command -v brew >/dev/null 2>&1; then
    brew install beads
  else
    curl -fsSL https://raw.githubusercontent.com/gastownhall/beads/main/scripts/install.sh | bash
  fi
else
  echo "bd already installed: $(bd version 2>/dev/null || true)"
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

echo "Installed c3x development environment."
echo "Activate it with: . .venv/bin/activate"
