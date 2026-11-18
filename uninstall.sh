#!/usr/bin/env bash
set -euo pipefail

PREFIX="/opt/antigravity"
if [[ $EUID -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
  PREFIX="${HOME}/.local/share/antigravity-webui"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX="$2"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--prefix DIR]" >&2
      exit 1
      ;;
  esac
done

run_priv() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files antigravity-webui.service >/dev/null 2>&1; then
    run_priv systemctl disable --now antigravity-webui.service || true
  fi
  if [[ -f /etc/systemd/system/antigravity-webui.service ]]; then
    run_priv rm -f /etc/systemd/system/antigravity-webui.service
    run_priv systemctl daemon-reload
  fi
fi

if [[ -d "${PREFIX}" ]]; then
  run_priv rm -rf "${PREFIX}"
  echo "Removed ${PREFIX}."
fi

echo "Uninstalled Antigravity WebUI. Chat history under ~/.gemini/ was preserved."
