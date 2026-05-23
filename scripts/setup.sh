#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

find_bd_bin() {
  local shim_path="$ROOT_DIR/.venv/bin/bd"
  local candidate
  local dir
  local -a path_parts

  # Prefer a real bd executable already on PATH, but ignore the venv shim.
  IFS=':' read -r -a path_parts <<< "$PATH"
  for dir in "${path_parts[@]}"; do
    [ -n "$dir" ] || dir='.'
    candidate="$dir/bd"
    [ "$candidate" = "$shim_path" ] && continue
    if [ -x "$candidate" ] && [ ! -d "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  # If setup already created the shim, keep using its real target.
  if [ -L "$shim_path" ]; then
    candidate="$(readlink "$shim_path" || true)"
    if [ -n "$candidate" ] && [ "${candidate#/}" != "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  return 1
}

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

BD_BIN="$(find_bd_bin || true)"
if [ -z "$BD_BIN" ]; then
  cat >&2 <<'EOF'
`bd` installed, but it is still not on PATH.
Add the directory containing `bd` to PATH, then rerun `scripts/setup.sh`.
EOF
  exit 1
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

ln -sfn "$BD_BIN" .venv/bin/bd

echo "Installed c3x development environment."
echo "Activate it with: . .venv/bin/activate"
echo "bd shim installed at: .venv/bin/bd -> $BD_BIN"
