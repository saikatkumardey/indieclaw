#!/usr/bin/env bash
set -e

REPO="saikatkumardey/indieclaw"
INSTALL_DIR="/usr/local/bin"

echo ""
echo "  IndieClaw installer"
echo ""

# Detect OS and arch
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  ARCH="x86_64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) ARCH="" ;;
esac

# Try binary install first
if [ -n "$ARCH" ] && command -v curl &>/dev/null; then
  BINARY_NAME="indieclaw-${OS}-${ARCH}"
  RELEASE_URL="https://github.com/${REPO}/releases/latest/download/${BINARY_NAME}"

  echo "→ Trying binary install ($BINARY_NAME)..."
  if curl -fsSL --head "$RELEASE_URL" &>/dev/null; then
    curl -fsSL "$RELEASE_URL" -o /tmp/indieclaw
    chmod +x /tmp/indieclaw
    if [ -w "$INSTALL_DIR" ]; then
      mv /tmp/indieclaw "$INSTALL_DIR/indieclaw"
    else
      sudo mv /tmp/indieclaw "$INSTALL_DIR/indieclaw"
    fi
    echo "→ Installed to $INSTALL_DIR/indieclaw"
    echo ""
    echo "→ Running setup..."
    echo ""
    indieclaw setup
    exit 0
  else
    echo "  No binary for this platform. Falling back to uv install."
  fi
fi

# Fallback: uv + pip install
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 not found. Install Python 3.12+ and try again."
  exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
  echo "Error: Python 3.12+ required (found $PY_VERSION)."
  exit 1
fi

if ! command -v uv &>/dev/null; then
  echo "→ Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "→ Installing indieclaw..."
uv tool install "git+https://github.com/${REPO}" --force
export PATH="$(uv tool dir)/../bin:$PATH"

echo ""
echo "→ Running setup..."
echo ""
indieclaw setup
