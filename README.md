# agy-webui

Standalone, persistent WebUI bridge for **Antigravity** (`agy` / `antigravity-cli`).

Run the official Antigravity WebUI on port `8888` as a standalone service decoupled from transient `agy` remote sessions, with real-time conversation sync, live responses, and an optional login gate.

## Features

- **Standalone Persistent Service**: Runs independently of `agy` remote session lifecycles via `systemd` (`antigravity-webui.service`).
- **Direct CLI Storage & Real-Time Sync**: Points directly to `~/.gemini/antigravity-cli` (zero file duplication) and automatically hot-swaps conversations when new `agy` sessions or updates occur.
- **Full Streaming & Live Responses**: Bridges chunked protobuf/HTML responses cleanly between the Antigravity `language_server` and the browser.
- **Toggleable Login Gate**: Optional password-protected `/login` screen matching the official Antigravity UI (`BRIDGE_LOGIN_GATE=1`), with HMAC-SHA256 signed `HttpOnly` session cookies.
- **Portable & Self-Installing**: Automatically detects Linux `x86_64` or `arm64`, downloads the official Antigravity `language_server` and assets, and sets up `systemd`.

## Quick Install

```bash
git clone https://github.com/abighozali/agy-webui.git
cd agy-webui
./install.sh
```

Or enable the login gate right away during installation:

```bash
./install.sh --enable-login-gate
```

### Installer Options

```text
--prefix DIR             Install directory (default: /opt/antigravity)
--host HOST              Bind address (default: 0.0.0.0)
--port PORT              WebUI port (default: 8888)
--backend-port PORT      Internal language_server port (default: 19876)
--app-data-dir NAME      App data dir name under ~/.gemini/ (default: antigravity-cli)
--enable-login-gate      Enable login gate with a generated password in auth.env
--no-systemd             Skip systemd service installation
--tarball-url URL        Override official Antigravity tarball URL
```

## Configuration & Environment Variables

All runtime settings can be customized via environment variables or `/opt/antigravity/auth.env`:

| Variable | Default | Description |
| --- | --- | --- |
| `BRIDGE_HOST` | `0.0.0.0` | Listen host for the WebUI bridge |
| `BRIDGE_PORT` | `8888` | Listen port for the WebUI bridge |
| `BACKEND_PORT` | `19876` | Internal port for `language_server` |
| `ANTIGRAVITY_APP_DATA_DIR` | `antigravity-cli` | Folder name inside `~/.gemini/` |
| `ANTIGRAVITY_CLI_DATA_DIR` | `~/.gemini/antigravity-cli` | Full path to Antigravity CLI data directory |
| `BRIDGE_SYNC_ENABLED` | `1` | Enable real-time watcher for external `agy` updates (`1` or `0`) |
| `BRIDGE_LOGIN_GATE` | `0` | Enable `/login` gate (`1` or `0`) |
| `BRIDGE_AUTH_PASSWORD` | `""` | Password required when `BRIDGE_LOGIN_GATE=1` |
| `BRIDGE_SESSION_SECRET` | random per-boot | Secret key used to sign session cookies |

### Toggling the Login Gate

Edit `/opt/antigravity/auth.env` (see [auth.env.example](auth.env.example)):

```dotenv
BRIDGE_LOGIN_GATE=1
BRIDGE_AUTH_PASSWORD=your-strong-password
BRIDGE_SESSION_SECRET=your-64-char-hex-secret
```

Then restart the service:

```bash
sudo systemctl restart antigravity-webui.service
```

## Uninstall

```bash
./uninstall.sh
```

This stops/removes `antigravity-webui.service` and `/opt/antigravity` while preserving your chat history in `~/.gemini/`.

## License

MIT — see [LICENSE](LICENSE).
