#!/usr/bin/env bash
/usr/local/bin/start-tailscale.sh
if [ ! -f /root/watchdog/watchdog.pid ] || ! kill -0 $(cat /root/watchdog/watchdog.pid 2>/dev/null) 2>/dev/null; then
  setsid /root/watchdog/watchdog.sh </dev/null >/var/log/watchdog.log 2>&1 &
fi
