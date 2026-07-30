#!/bin/sh
# mesh CLI installer — downloads a release binary and VERIFIES it before
# installing (SHA-256 checksum + minisign signature against the public key
# shipped in the repository at cli/mesh-release.pub).
#
# Deliberately transparent: read this script before running it. Do NOT pipe
# curl to sh blindly (cli.md §5.4 / N4: no auto-update, no blind installs).
#
# Usage:
#   ./install.sh <version>            # e.g. ./install.sh cli-v0.1.0
#   MESH_CLI_PUBKEY=/path/to/mesh-release.pub ./install.sh cli-v0.1.0
set -eu

VERSION="${1:?usage: install.sh <version>   e.g. cli-v0.1.0}"
REPO="${MESH_CLI_REPO:-cnwenf/Mesh}"
INSTALL_DIR="${MESH_CLI_INSTALL_DIR:-/usr/local/bin}"
PUBKEY="${MESH_CLI_PUBKEY:-cli/mesh-release.pub}"

case "$(uname -s)" in
  Linux)  os=linux ;;
  Darwin) os=darwin ;;
  *) echo "unsupported OS (build your own binary with pyinstaller — see cli/README.md)"; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x86_64 ;;
  arm64|aarch64) arch=aarch64 ;;
  *) echo "unsupported architecture"; exit 1 ;;
esac

ASSET="mesh-${os}-${arch}"
BASE="https://github.com/${REPO}/releases/download/${VERSION}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "fetching ${ASSET} (${VERSION})…"
curl -fsSL -o "${WORK}/${ASSET}" "${BASE}/${ASSET}"
curl -fsSL -o "${WORK}/${ASSET}.sha256" "${BASE}/${ASSET}.sha256"

echo "verifying SHA-256…"
( cd "$WORK" && printf '%s  %s\n' "$(cat "${ASSET}.sha256")" "$ASSET" | sha256sum -c - )

if command -v minisign >/dev/null 2>&1; then
  if [ ! -f "$PUBKEY" ]; then
    echo "public key $PUBKEY not found — fetching from the repository…"
    curl -fsSL -o "${WORK}/mesh-release.pub" \
      "https://raw.githubusercontent.com/${REPO}/main/cli/mesh-release.pub"
    PUBKEY="${WORK}/mesh-release.pub"
  fi
  curl -fsSL -o "${WORK}/${ASSET}.minisig" "${BASE}/${ASSET}.minisig"
  echo "verifying minisign signature…"
  minisign -V -p "$PUBKEY" -m "${WORK}/${ASSET}"
else
  echo "minisign not installed — SKIPPING signature verification (install sha256-verified binary anyway?)"
  printf 'continue? [y/N] '
  read -r answer
  [ "$answer" = "y" ] || exit 1
fi

chmod +x "${WORK}/${ASSET}"
install -m 0755 "${WORK}/${ASSET}" "${INSTALL_DIR}/mesh"
echo "installed mesh → ${INSTALL_DIR}/mesh"
mesh version
