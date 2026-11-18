#!/usr/bin/env bash
set -euo pipefail

# Standalone Antigravity WebUI Installer
# Supports Linux x86_64 and arm64/aarch64.

DEFAULT_PREFIX="/opt/antigravity"
if [[ $EUID -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
  DEFAULT_PREFIX="${HOME}/.local/share/antigravity-webui"
fi

PREFIX="${DEFAULT_PREFIX}"
BRIDGE_HOST="0.0.0.0"
BRIDGE_PORT="8888"
BACKEND_PORT="19876"
APP_DATA_DIR="antigravity-cli"
ENABLE_LOGIN_GATE="0"
USE_SYSTEMD="1"
TARBALL_URL=""

usage() {
  cat <<'EOF'
Usage: install.sh [OPTIONS]

Options:
  --prefix DIR             Install directory (default: /opt/antigravity)
  --host HOST              Bind address (default: 0.0.0.0)
  --port PORT              WebUI port (default: 8888)
  --backend-port PORT      Internal language_server port (default: 19876)
  --app-data-dir NAME      App data dir name under ~/.gemini/ (default: antigravity-cli)
  --enable-login-gate      Enable login gate with a generated password in auth.env
  --no-systemd             Skip systemd service installation
  --tarball-url URL        Override official Antigravity tarball URL
  -h, --help               Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX="$2"
      shift 2
      ;;
    --host)
      BRIDGE_HOST="$2"
      shift 2
      ;;
    --port)
      BRIDGE_PORT="$2"
      shift 2
      ;;
    --backend-port)
      BACKEND_PORT="$2"
      shift 2
      ;;
    --app-data-dir)
      APP_DATA_DIR="$2"
      shift 2
      ;;
    --enable-login-gate)
      ENABLE_LOGIN_GATE="1"
      shift
      ;;
    --no-systemd)
      USE_SYSTEMD="0"
      shift
      ;;
    --tarball-url)
      TARBALL_URL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_priv() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

# 1. Check dependencies
for cmd in python3 curl tar; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: required command '$cmd' not found." >&2
    exit 1
  fi
done

# 2. Detect architecture
UNAME_S="$(uname -s)"
if [[ "${UNAME_S}" != "Linux" ]]; then
  echo "Error: only Linux is supported (detected ${UNAME_S})." >&2
  exit 1
fi

UNAME_M="$(uname -m)"
case "${UNAME_M}" in
  x86_64|amd64)
    ARCH_DIR="linux-x64"
    LS_SUFFIX="x64"
    ;;
  aarch64|arm64)
    ARCH_DIR="linux-arm"
    LS_SUFFIX="arm"
    ;;
  *)
    echo "Error: unsupported architecture '${UNAME_M}'." >&2
    exit 1
    ;;
esac

echo "[1/5] Preparing install directory at ${PREFIX} (${ARCH_DIR})..."
run_priv mkdir -p "${PREFIX}"

# 3. Resolve and download Antigravity release tarball if language_server is missing
if [[ -z "${TARBALL_URL}" ]]; then
  TARBALL_URL="$(
    python3 - "${ARCH_DIR}" <<'PY'
import re
import sys
import urllib.request

arch_dir = sys.argv[1]
fallback = f"https://storage.googleapis.com/antigravity-public/antigravity-hub/2.33.0-4731201358004224/{arch_dir}/Antigravity.tar.gz"
try:
    req = urllib.request.Request(
        "https://antigravity.google/download",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    pattern = rf"https://storage\.googleapis\.com/antigravity-public/antigravity-hub/[^\"'\s]+/{re.escape(arch_dir)}/Antigravity\.tar\.gz"
    matches = re.findall(pattern, html)
    if matches:
        print(matches[0])
    else:
        print(fallback)
except Exception:
    print(fallback)
PY
  )"
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

echo "[2/5] Downloading official Antigravity bundle..."
echo "      URL: ${TARBALL_URL}"
curl -fsSL "${TARBALL_URL}" -o "${TMP_DIR}/Antigravity.tar.gz"

echo "[3/5] Extracting language_server, version, and UI assets..."
tar -xzf "${TMP_DIR}/Antigravity.tar.gz" -C "${TMP_DIR}"

LS_SRC="$(find "${TMP_DIR}" -type f -name "language_server_linux_${LS_SUFFIX}" | head -n 1 || true)"
if [[ -z "${LS_SRC}" ]]; then
  LS_SRC="$(find "${TMP_DIR}" -type f -name "language_server_linux_*" | head -n 1 || true)"
fi
if [[ -z "${LS_SRC}" ]]; then
  echo "Error: could not locate language_server binary in downloaded archive." >&2
  exit 1
fi

run_priv install -m 0755 "${LS_SRC}" "${PREFIX}/language_server"

VERSION_FILE="$(find "${TMP_DIR}" -type f -name "version" | head -n 1 || true)"
if [[ -n "${VERSION_FILE}" ]]; then
  run_priv install -m 0644 "${VERSION_FILE}" "${PREFIX}/version"
fi

ASAR_FILE="$(find "${TMP_DIR}" -type f -name "app.asar" | head -n 1 || true)"
if [[ -n "${ASAR_FILE}" ]]; then
  python3 - "${ASAR_FILE}" "${TMP_DIR}/icon.png" <<'PY'
import json
import struct
import sys

asar_path, out_path = sys.argv[1], sys.argv[2]
with open(asar_path, "rb") as f:
    header_size_bytes = f.read(16)
    if len(header_size_bytes) < 16:
        sys.exit(0)
    json_len = struct.unpack("<I", header_size_bytes[12:16])[0]
    header = json.loads(f.read(json_len).decode("utf-8"))
    base_offset = 16 + json_len

    def find_file(node, target_names):
        files = node.get("files", {})
        for name, info in files.items():
            if name in target_names and "offset" in info and "size" in info:
                return int(info["offset"]), int(info["size"])
            if "files" in info:
                res = find_file(info, target_names)
                if res:
                    return res
        return None

    found = find_file(header, {"antigravity.png", "icon.png", "logo.png"})
    if found:
        offset, size = found
        f.seek(base_offset + offset)
        with open(out_path, "wb") as out:
            out.write(f.read(size))
PY
  if [[ -f "${TMP_DIR}/icon.png" ]]; then
    run_priv install -m 0644 "${TMP_DIR}/icon.png" "${PREFIX}/icon.png"
  fi
fi

# 4. Install bridge script and auth.env
echo "[4/5] Installing antigravity_web_bridge.py and configuration..."
BRIDGE_SRC="${SCRIPT_DIR}/antigravity_web_bridge.py"
if [[ ! -f "${BRIDGE_SRC}" ]]; then
  echo "Error: ${BRIDGE_SRC} not found." >&2
  exit 1
fi
run_priv install -m 0755 "${BRIDGE_SRC}" "${PREFIX}/antigravity_web_bridge.py"

AUTH_ENV="${PREFIX}/auth.env"
if [[ ! -f "${AUTH_ENV}" ]]; then
  GEN_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
  GEN_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "${TMP_DIR}/auth.env" <<EOF
BRIDGE_LOGIN_GATE=${ENABLE_LOGIN_GATE}
BRIDGE_AUTH_PASSWORD=${GEN_PASS}
BRIDGE_SESSION_SECRET=${GEN_SECRET}
EOF
  run_priv install -m 0600 "${TMP_DIR}/auth.env" "${AUTH_ENV}"
  if [[ "${ENABLE_LOGIN_GATE}" == "1" ]]; then
    echo "      Generated login gate password in ${AUTH_ENV}: ${GEN_PASS}"
  fi
elif [[ "${ENABLE_LOGIN_GATE}" == "1" ]]; then
  if grep -q '^BRIDGE_LOGIN_GATE=' "${AUTH_ENV}"; then
    run_priv sed -i 's/^BRIDGE_LOGIN_GATE=.*/BRIDGE_LOGIN_GATE=1/' "${AUTH_ENV}"
  else
    echo "BRIDGE_LOGIN_GATE=1" | run_priv tee -a "${AUTH_ENV}" >/dev/null
  fi
fi

# 5. Install and start systemd service if available
SERVICE_USER="${SUDO_USER:-$(id -un)}"
SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
SERVICE_HOME="${SERVICE_HOME:-$HOME}"

if [[ "${USE_SYSTEMD}" == "1" ]] && [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  echo "[5/5] Installing systemd service antigravity-webui.service..."
  cat > "${TMP_DIR}/antigravity-webui.service" <<EOF
[Unit]
Description=Antigravity Standalone WebUI Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${PREFIX}
Environment=HOME=${SERVICE_HOME}
Environment=SSH_CONNECTION=127.0.0.1 1 127.0.0.1 22
Environment=BRIDGE_HOST=${BRIDGE_HOST}
Environment=BRIDGE_PORT=${BRIDGE_PORT}
Environment=BACKEND_PORT=${BACKEND_PORT}
Environment=ANTIGRAVITY_LS_BIN=${PREFIX}/language_server
Environment=ANTIGRAVITY_ICON_PATH=${PREFIX}/icon.png
Environment=ANTIGRAVITY_APP_DATA_DIR=${APP_DATA_DIR}
Environment=ANTIGRAVITY_CLI_DATA_DIR=${SERVICE_HOME}/.gemini/${APP_DATA_DIR}
EnvironmentFile=-${AUTH_ENV}
ExecStart=/usr/bin/python3 ${PREFIX}/antigravity_web_bridge.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  run_priv install -m 0644 "${TMP_DIR}/antigravity-webui.service" /etc/systemd/system/antigravity-webui.service
  run_priv systemctl daemon-reload
  run_priv systemctl enable --now antigravity-webui.service
  echo ""
  echo "Installation complete! Service status:"
  systemctl --no-pager --full status antigravity-webui.service || true
else
  echo "[5/5] Skipping systemd setup. Run manually with:"
  echo "      BRIDGE_HOST=${BRIDGE_HOST} BRIDGE_PORT=${BRIDGE_PORT} ANTIGRAVITY_APP_DATA_DIR=${APP_DATA_DIR} python3 ${PREFIX}/antigravity_web_bridge.py"
fi

echo ""
echo "WebUI URL: http://127.0.0.1:${BRIDGE_PORT}"
