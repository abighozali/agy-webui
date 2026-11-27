#!/usr/bin/env bash
set -euo pipefail

ASAR_PATH="/opt/antigravity/resources/app.asar"
TOKEN_FILE="/root/.config/antigravity/webui-csrf-token"

mkdir -p "$(dirname "${TOKEN_FILE}")"
if [ ! -s "${TOKEN_FILE}" ]; then
  python3 -c 'import secrets; print(secrets.token_hex(32))' > "${TOKEN_FILE}"
  chmod 600 "${TOKEN_FILE}"
fi

ANTIGRAVITY_SERVER_CSRF_TOKEN="$(tr -d '\r\n' < "${TOKEN_FILE}")"
export ANTIGRAVITY_SERVER_CSRF_TOKEN
export ANTIGRAVITY_VSCODE_HOST=1

IDE_VERSION="$(python3 - "${ASAR_PATH}" <<'PY'
import json, struct, sys
try:
    with open(sys.argv[1], "rb") as f:
        f.read(12)
        str_len = struct.unpack("<I", f.read(4))[0]
        hdr = json.loads(f.read(str_len).decode("utf-8"))
        base = 16 + str_len
        cur = hdr["files"]["package.json"]
        f.seek(base + int(cur["offset"]))
        print(json.loads(f.read(cur["size"]))["version"])
except Exception:
    print("2.35.2")
PY
)"

exec /opt/antigravity/resources/bin/language_server \
  --standalone \
  --persistent_mode \
  --override_ide_name=antigravity \
  --subclient_type=hub \
  --override_ide_version="${IDE_VERSION}" \
  --override_user_agent_name=antigravity \
  --http_server_port=25214 \
  --app_data_dir=antigravity \
  --api_server_url=https://generativelanguage.googleapis.com \
  --cloud_code_endpoint=https://daily-cloudcode-pa.googleapis.com \
  --enable_sidecars
