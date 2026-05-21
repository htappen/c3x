#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATION_DIR="$ROOT_DIR/.tmp/validation"
PROJECT_DIR="$VALIDATION_DIR/project"
HOME_DIR="$VALIDATION_DIR/home"

rm -rf "$VALIDATION_DIR"
mkdir -p "$PROJECT_DIR"
mkdir -p "$HOME_DIR"

cd "$PROJECT_DIR"
git init --quiet

cat > README.md <<'EOF'
# c3x Validation Project

Temporary project used for local c3x validation.
EOF

git add README.md
git commit --quiet -m "Initial validation project" >/dev/null 2>&1 || true

cat > "$VALIDATION_DIR/env" <<EOF
export C3X_VALIDATION_DIR="$VALIDATION_DIR"
export C3X_VALIDATION_PROJECT="$PROJECT_DIR"
export HOME="$HOME_DIR"
EOF

echo "$PROJECT_DIR"
