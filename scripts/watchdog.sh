#!/usr/bin/env bash
set -u
LOCK_FILE="/root/watchdog/watchdog.pid"
STATE_DIR="/root/watchdog/state"
LOG_FILE="/var/log/watchdog.log"
mkdir -p "${STATE_DIR}"

if [ -f "${LOCK_FILE}" ]; then
  OLD_PID=$(cat "${LOCK_FILE}" 2>/dev/null || true)
  if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null; then
    exit 0
  fi
fi
echo $$ > "${LOCK_FILE}"
trap 'rm -f "${LOCK_FILE}"' EXIT

set -a
. /root/watchdog/.env
set +a

send_tg() {
  local msg="$1"
  curl -fsS -m 10 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"     -d "chat_id=${TELEGRAM_CHAT_ID}"     --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
}

check_and_heal() {
  local name="$1"
  local check_cmd="$2"
  local start_cmd="$3"
  local fail_file="${STATE_DIR}/${name}.fail"

  if eval "${check_cmd}" >/dev/null 2>&1; then
    echo 0 > "${fail_file}"
    return 0
  fi

  echo "[$(date -u)] [us-lax01] ${name} down; attempting restart..." >> "${LOG_FILE}"
  eval "${start_cmd}" >> "${LOG_FILE}" 2>&1 || true
  sleep 5

  if eval "${check_cmd}" >/dev/null 2>&1; then
    echo 0 > "${fail_file}"
    echo "[$(date -u)] [us-lax01] ${name} recovered." >> "${LOG_FILE}"
    return 0
  fi

  local fails=$(cat "${fail_file}" 2>/dev/null || echo 0)
  fails=$((fails + 1))
  echo "${fails}" > "${fail_file}"
  echo "[$(date -u)] [us-lax01] ${name} failed restart (${fails}/3)." >> "${LOG_FILE}"
  if [ "${fails}" -eq 3 ]; then
    send_tg "🚨 [us-lax01] Service '${name}' failed to recover after 3 consecutive restart attempts."
  fi
}

while true; do
  check_and_heal "tailscaled"     "pgrep -x tailscaled"     "/usr/local/bin/start-tailscale.sh"

  check_and_heal "ts-omniroute-fwd" "pgrep -f ts-omniroute-fwd.py" "setsid python3 /usr/local/bin/ts-omniroute-fwd.py </dev/null >/var/log/ts-omniroute-fwd.log 2>&1 &"

  check_and_heal "nginx"     "pgrep -x nginx"     "nginx -t && nginx"

  check_and_heal "hermes"     "curl -sf -m 5 http://127.0.0.1:8642/health"     "setsid /root/.local/bin/hermes gateway run </dev/null >/var/log/hermes-gateway.log 2>&1 &"

  check_and_heal "tg_retry_daemon" "pgrep -f tg_retry_daemon.py" "setsid python3 /root/watchdog/tg_retry_daemon.py </dev/null >/var/log/tg-retry-daemon.log 2>&1 &"

  check_and_heal "orca"     "curl -sf -m 5 http://127.0.0.1:25213/"     "setsid /usr/local/bin/orca-ide serve --bind 0.0.0.0 --port 25213 --pairing-address wss://orca.abighozali.com --allow-public --supervise </dev/null >/var/log/orca-server.log 2>&1 &"

  check_and_heal "antigravity-webui" "curl -sf -m 5 http://127.0.0.1:25214/" "setsid /usr/local/bin/start-antigravity-webui.sh </dev/null >/var/log/antigravity-webui.log 2>&1 &"

  check_and_heal "antigravity-web-shim" "curl -sf -m 5 http://127.0.0.1:25216/_shim/health" "setsid python3 /usr/local/bin/antigravity-web-shim.py </dev/null >/var/log/antigravity-web-shim.log 2>&1 &"

  # 6-hourly backup (21600s) — run asynchronously so it never blocks service supervision
  NOW=$(date +%s)
  LAST=$(cat /root/watchdog/last_backup_ts 2>/dev/null || echo 0)
  if [ $((NOW - LAST)) -ge 21600 ] && ! pgrep -f '/root/watchdog/backup.sh' >/dev/null 2>&1; then
    setsid /root/watchdog/backup.sh </dev/null >>"${LOG_FILE}" 2>&1 &
  fi

  sleep 5
done
