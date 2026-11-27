# Antigravity WebUI (`agy-webui`)

Production deployment for running `/opt/antigravity` as a native, 1:1 WebApp behind an Edge Auth Proxy and host-multiplexed Nginx reverse proxy (no virtual displays, no pixel streaming, no `systemd` required).

## Architecture

```text
Internet / Cloudflare (https://agy.abighozali.com)
        │
        ▼  (0.0.0.0:25323)
  Nginx (auth_request /_auth/verify + host map)
        ├── agy.abighozali.com ─────────► antigravity-web-shim.py (127.0.0.1:25216)
        │                                       │
        │                                       ▼  (HTTP + WebSocket + Polyfill)
        │                                 language_server (127.0.0.1:25214)
        └── hermes-agent.abighozali.com ► Hermes Gateway (127.0.0.1:25324 / 8642)

Tailscale Private Access (https://us-lax01.taila59c7b.ts.net/)
        │
        ▼  (127.0.0.1:25215)
  Nginx ──► antigravity-web-shim.py (127.0.0.1:25216) ──► language_server (127.0.0.1:25214)
```

## Components

- **`scripts/start-antigravity-webui.sh`**: Launches `/opt/antigravity/resources/bin/language_server` in `--standalone --persistent_mode` on `127.0.0.1:25214`, dynamically reading the IDE version from `/opt/antigravity/resources/app.asar` and persisting a CSRF token at `/root/.config/antigravity/webui-csrf-token`.
- **`scripts/antigravity-web-shim.py`**:
  - Serves the Edge Auth Proxy (`/_auth/login`, `/_auth/verify`, `/_auth/logout`, `/_auth/icon.png`) backed by `/root/.config/Antigravity/auth_proxy.json` with HMAC-SHA256 signed `HttpOnly; Secure; SameSite=Lax` cookies.
  - Injects `/_shim/electron-polyfill.js` bridging Electron preload/IPC APIs (`nativeStorage`, `dialog`, `nativeNotifications`, `electronNative`, `wcv`, `deepLink`, `logs`, `ide`, `sshRemote`, `desktopMic`).
  - Coordinates Antigravity's native Google OAuth `authCode` PKCE flow (`https://antigravity.google/oauth-callback`) and trims submitted authorization codes.
  - Proxies HTTP and WebSocket (`/connect-websocket`) traffic with `TCP_NODELAY` and 4 MiB stream buffers.
- **`nginx/`**:
  - `sites-available/default`: Port `25323` listener with `auth_request /_auth/verify`, `proxy_buffering off`, `proxy_request_buffering off`, `proxy_socket_keepalive on`, `tcp_nodelay on`, and 3600s timeouts.
  - `conf.d/antigravity-public.conf`: Maps `$http_host|$http_x_forwarded_host` so `agy.abighozali.com` routes to `127.0.0.1:25216` while `hermes-agent.abighozali.com` routes to `127.0.0.1:25324`.
  - `conf.d/antigravity-ts.conf`: Localhost proxy on `127.0.0.1:25215` for `tailscale serve`.
- **`scripts/start.sh` & `scripts/watchdog.sh`**: Boot entrypoint and 5-second supervision loop (with asynchronous backup execution) for `nginx`, `hermes`, `antigravity-webui`, and `antigravity-web-shim`.
- **`assets/`**: Official Antigravity gradient emblem (`icon.png`) and favicon (`favicon.ico`).

## Setup

1. Copy `config/auth_proxy.example.json` to `/root/.config/Antigravity/auth_proxy.json` (`chmod 600`) and set your own `passkey` and `cookie_secret` (or let `antigravity-web-shim.py` auto-generate it on first run).
2. Install scripts to `/usr/local/bin/`, `/root/sandbox/`, and `/root/watchdog/`, and Nginx configs to `/etc/nginx/`.
3. Install `assets/icon.png` and `assets/favicon.ico` to `/opt/antigravity/resources/`.
4. Start supervision via `/root/sandbox/start.sh`.
