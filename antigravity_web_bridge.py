#!/usr/bin/env python3
"""Self-contained local proxy bridge for Antigravity's internal WebUI server."""

import asyncio
import base64
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

LISTEN_HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("BRIDGE_PORT", "8888"))
BACKEND_HOST = "127.0.0.1"
PRIMARY_PORT = int(os.environ.get("BACKEND_PORT", "18888"))
STANDBY_PORT = PRIMARY_PORT + 1
ACTIVE_BACKEND_PORT = PRIMARY_PORT

LS_BIN = os.environ.get(
    "ANTIGRAVITY_LS_BIN",
    os.path.join(SCRIPT_DIR, "resources", "bin", "language_server"),
)
if not os.path.exists(LS_BIN) and os.path.exists("/opt/antigravity/resources/bin/language_server"):
    LS_BIN = "/opt/antigravity/resources/bin/language_server"

ICON_PATH = os.environ.get("ANTIGRAVITY_ICON_PATH", os.path.join(SCRIPT_DIR, "icon.png"))
if not os.path.exists(ICON_PATH) and os.path.exists("/opt/antigravity/icon.png"):
    ICON_PATH = "/opt/antigravity/icon.png"

APP_DATA_DIR = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "antigravity-cli")
CLI_DATA_DIR = os.environ.get(
    "ANTIGRAVITY_CLI_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".gemini", APP_DATA_DIR),
)


def detect_ide_version() -> str:
    if env_ver := os.environ.get("ANTIGRAVITY_IDE_VERSION"):
        return env_ver
    for candidate in (
        os.path.join(SCRIPT_DIR, "version"),
        "/opt/antigravity/version",
    ):
        if os.path.isfile(candidate):
            try:
                val = open(candidate, "r", encoding="utf-8").read().strip()
                if val:
                    return val.split("-", 1)[0]
            except OSError:
                pass
    return "2.33.0"


IDE_VERSION = detect_ide_version()
SYNC_ENABLED = os.environ.get("BRIDGE_SYNC_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")

LOGIN_GATE_ENABLED = os.environ.get("BRIDGE_LOGIN_GATE", "0").strip().lower() in ("1", "true", "yes", "on")
AUTH_PASSWORD = os.environ.get("BRIDGE_AUTH_PASSWORD", "")
SESSION_SECRET = (os.environ.get("BRIDGE_SESSION_SECRET") or secrets.token_hex(32)).encode("utf-8")
GATE_COOKIE_NAME = "agy_gate_session"
GATE_SESSION_TTL = 7 * 24 * 3600

# In-memory store for async login sessions: id -> {"done": Event, "status": int, "content_type": str, "body": bytes}
LOGIN_SESSIONS: dict[str, dict] = {}
LAST_WEBUI_MUTATION: float = 0.0
MUTATION_RPCS = (
    b"/exa.language_server_pb.LanguageServerService/StartCascade",
    b"/exa.language_server_pb.LanguageServerService/SendUserCascadeMessage",
    b"/exa.language_server_pb.LanguageServerService/HandleCascadeUserInteraction",
    b"/exa.language_server_pb.LanguageServerService/CancelCascadeInvocation",
    b"/exa.language_server_pb.LanguageServerService/RevertToCascadeStep",
    b"/exa.language_server_pb.LanguageServerService/DeleteCascadeTrajectory",
)

LOGIN_PAGE_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Sign in · Antigravity</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <link rel="icon" type="image/png" href="/antigravity-icon.png" />
    <link rel="stylesheet" href="/jetbox.css" />
    <link rel="stylesheet" href="/compiled_tailwind.css" />
    <script>
      (() => {
        const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        document.documentElement.style.colorScheme = dark ? "dark" : "light";
        if (dark) document.documentElement.classList.add("dark");
      })();
    </script>
    <style>
      :root {
        --bg: #ffffff;
        --fg: #111827;
        --muted: #6b7280;
        --card: #f9fafb;
        --border: #e5e7eb;
        --input-bg: #ffffff;
        --btn-bg: #111827;
        --btn-fg: #ffffff;
        --err: #dc2626;
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --bg: #0f1115;
          --fg: #f3f4f6;
          --muted: #9ca3af;
          --card: #161920;
          --border: #262b36;
          --input-bg: #0f1115;
          --btn-bg: #f3f4f6;
          --btn-fg: #111827;
          --err: #f87171;
        }
      }
      html, body {
        height: 100%;
        margin: 0;
        background: var(--bg);
        color: var(--fg);
        font-family: "Google Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      .gate-wrap {
        min-height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 24px;
      }
      .gate-card {
        width: 100%;
        max-width: 380px;
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 32px 28px;
        box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08);
        text-align: center;
      }
      .gate-logo {
        width: 48px;
        height: 48px;
        margin: 0 auto 16px;
        border-radius: 12px;
      }
      .gate-title {
        font-size: 20px;
        font-weight: 600;
        margin: 0 0 6px;
        letter-spacing: -0.01em;
      }
      .gate-sub {
        font-size: 13px;
        color: var(--muted);
        margin: 0 0 24px;
      }
      .gate-input {
        width: 100%;
        box-sizing: border-box;
        padding: 10px 14px;
        border-radius: 10px;
        border: 1px solid var(--border);
        background: var(--input-bg);
        color: var(--fg);
        font-size: 14px;
        outline: none;
        margin-bottom: 12px;
      }
      .gate-input:focus {
        border-color: #4f46e5;
      }
      .gate-btn {
        width: 100%;
        padding: 10px 16px;
        border-radius: 10px;
        border: none;
        background: var(--btn-bg);
        color: var(--btn-fg);
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
      }
      .gate-btn:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      .gate-err {
        min-height: 18px;
        margin-top: 12px;
        font-size: 12.5px;
        color: var(--err);
      }
    </style>
  </head>
  <body>
    <main class="gate-wrap">
      <section class="gate-card">
        <img class="gate-logo" src="/antigravity-icon.png" alt="Antigravity" />
        <h1 class="gate-title">Sign in to Antigravity</h1>
        <p class="gate-sub">Enter your instance access password to continue.</p>
        <form id="gate-form">
          <input id="gate-password" class="gate-input" type="password" placeholder="Access password" autocomplete="current-password" required autofocus />
          <button id="gate-submit" class="gate-btn" type="submit">Continue</button>
        </form>
        <div id="gate-err" class="gate-err" role="alert"></div>
      </section>
    </main>
    <script>
      const form = document.getElementById("gate-form");
      const pwEl = document.getElementById("gate-password");
      const btn = document.getElementById("gate-submit");
      const errEl = document.getElementById("gate-err");
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        errEl.textContent = "";
        btn.disabled = true;
        try {
          const resp = await fetch("/_bridge/gate_login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: pwEl.value }),
          });
          if (resp.ok) {
            const next = new URLSearchParams(window.location.search).get("next") || "/";
            window.location.href = next.startsWith("/") ? next : "/";
          } else {
            const data = await resp.json().catch(() => ({}));
            errEl.textContent = data.error || "Invalid password.";
            btn.disabled = false;
            pwEl.focus();
          }
        } catch {
          errEl.textContent = "Network error. Please try again.";
          btn.disabled = false;
        }
      });
    </script>
  </body>
</html>
"""

INJECTED_SCRIPT = b"""<script>
(() => {
  if (window.__agyBridgeInstalled) return;
  window.__agyBridgeInstalled = true;
  const origFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isLogin = url.includes('/exa.language_server_pb.LanguageServerService/Login') &&
                    !url.includes('LoginWithBrowser');
    if (!isLogin) {
      return origFetch(input, init);
    }

    let popup = null;
    try { popup = window.open('about:blank', '_blank'); } catch {}

    const reqHeaders = new Headers(
      (init && init.headers) || (typeof input !== 'string' && input && input.headers) || {}
    );
    let bodyBuf = undefined;
    if (init && init.body !== undefined) {
      bodyBuf = init.body;
    } else if (typeof input !== 'string' && input && input.clone) {
      try { bodyBuf = await input.clone().arrayBuffer(); } catch {}
    }

    const startResp = await origFetch('/_bridge/start_login', {
      method: 'POST',
      headers: reqHeaders,
      body: bodyBuf,
    });
    if (!startResp.ok) {
      if (popup) try { popup.close(); } catch {}
      return startResp;
    }
    const { id, authUrl } = await startResp.json();
    if (authUrl) {
      if (popup && !popup.closed) {
        try { popup.location.href = authUrl; } catch { window.open(authUrl, '_blank', 'noopener'); }
      } else {
        window.open(authUrl, '_blank', 'noopener');
      }
    } else if (popup) {
      try { popup.close(); } catch {}
    }

    while (true) {
      const pollResp = await origFetch('/_bridge/poll_login?id=' + encodeURIComponent(id), {
        method: 'GET',
        cache: 'no-store',
      });
      if (pollResp.status === 202) {
        continue;
      }
      const status = parseInt(pollResp.headers.get('X-Bridge-Status') || String(pollResp.status), 10);
      const ct = pollResp.headers.get('X-Bridge-Content-Type') || 'application/proto';
      const buf = await pollResp.arrayBuffer();
      const outHeaders = new Headers();
      outHeaders.set('Content-Type', ct);
      return new Response(buf, { status, headers: outHeaders });
    }
  };
})();
</script>"""


def make_gate_cookie() -> str:
    exp = str(int(time.time()) + GATE_SESSION_TTL).encode("ascii")
    nonce = secrets.token_urlsafe(12).encode("ascii")
    payload = base64.urlsafe_b64encode(exp + b"." + nonce)
    sig = hmac.new(SESSION_SECRET, payload, hashlib.sha256).hexdigest().encode("ascii")
    return (payload + b"." + sig).decode("ascii")


def verify_gate_cookie(raw_headers: bytes) -> bool:
    if not LOGIN_GATE_ENABLED:
        return True
    cookie_hdr = ""
    for line in raw_headers.split(b"\r\n")[1:]:
        if line.lower().startswith(b"cookie:"):
            cookie_hdr = line.split(b":", 1)[1].decode("ascii", errors="ignore")
            break
    if not cookie_hdr:
        return False
    token = ""
    for part in cookie_hdr.split(";"):
        k, _, v = part.strip().partition("=")
        if k == GATE_COOKIE_NAME:
            token = v.strip()
            break
    if not token or "." not in token:
        return False
    payload_s, _, sig_s = token.rpartition(".")
    expected = hmac.new(SESSION_SECRET, payload_s.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_s, expected):
        return False
    try:
        decoded = base64.urlsafe_b64decode(payload_s.encode("ascii"))
        exp = int(decoded.split(b".", 1)[0])
        return time.time() < exp
    except Exception:
        return False


def rewrite_headers(
    raw_header_block: bytes, backend_port: int, force_identity: bool = False
) -> tuple[bytes, bytes, bool, int, bool, str | None]:
    """Rewrite Host/Origin for loopback backend and inspect framing/upgrade."""
    lines = raw_header_block.split(b"\r\n")
    request_line = lines[0]
    parts = request_line.split(b" ")
    path = parts[1].split(b"?", 1)[0] if len(parts) >= 2 else b""
    out_lines = [request_line]
    is_upgrade = False
    content_length = 0
    is_chunked = False
    csrf_token = None

    for line in lines[1:]:
        if not line:
            continue
        colon = line.find(b":")
        if colon == -1:
            out_lines.append(line)
            continue
        name = line[:colon].strip()
        lower = name.lower()
        val = line[colon + 1 :].strip()

        if lower == b"host":
            out_lines.append(f"Host: {BACKEND_HOST}:{backend_port}".encode("ascii"))
        elif lower == b"origin":
            out_lines.append(f"Origin: http://{BACKEND_HOST}:{backend_port}".encode("ascii"))
        elif lower == b"referer":
            out_lines.append(f"Referer: http://{BACKEND_HOST}:{backend_port}/".encode("ascii"))
        elif lower == b"accept-encoding" and force_identity:
            out_lines.append(b"Accept-Encoding: identity")
        elif lower == b"x-codeium-csrf-token":
            csrf_token = val.decode("ascii", errors="ignore")
            out_lines.append(line)
        elif lower == b"connection":
            if b"upgrade" in val.lower():
                is_upgrade = True
                out_lines.append(line)
            else:
                out_lines.append(b"Connection: close")
        elif lower == b"upgrade":
            is_upgrade = True
            out_lines.append(line)
        elif lower == b"content-length":
            try:
                content_length = int(val)
            except ValueError:
                content_length = 0
            out_lines.append(line)
        elif lower == b"transfer-encoding" and b"chunked" in val.lower():
            is_chunked = True
            out_lines.append(line)
        else:
            out_lines.append(line)

    if not is_upgrade and not any(l.lower().startswith(b"connection:") for l in out_lines[1:]):
        out_lines.append(b"Connection: close")

    return (
        b"\r\n".join(out_lines) + b"\r\n\r\n",
        path,
        is_upgrade,
        content_length,
        is_chunked,
        csrf_token,
    )


async def read_http_body(
    reader: asyncio.StreamReader, content_length: int, is_chunked: bool
) -> bytes:
    if content_length > 0:
        return await reader.readexactly(content_length)
    if is_chunked:
        chunks = []
        while True:
            line = await reader.readline()
            if not line:
                break
            size = int(line.strip().split(b";", 1)[0], 16)
            if size == 0:
                while True:
                    trailer = await reader.readline()
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                break
            data = await reader.readexactly(size + 2)
            chunks.append(data[:-2])
        return b"".join(chunks)
    return b""


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def backend_rpc_json(rpc_name: str, csrf_token: str, payload: dict) -> dict:
    port = ACTIVE_BACKEND_PORT
    try:
        reader, writer = await asyncio.open_connection(BACKEND_HOST, port)
        body = json.dumps(payload).encode("utf-8")
        req = (
            f"POST /exa.language_server_pb.LanguageServerService/{rpc_name} HTTP/1.1\r\n"
            f"Host: {BACKEND_HOST}:{port}\r\n"
            f"Content-Type: application/json\r\n"
            f"x-codeium-csrf-token: {csrf_token}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii") + body
        writer.write(req)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=5.0)
        writer.close()
        _, _, resp_body = raw.partition(b"\r\n\r\n")
        return json.loads(resp_body.decode("utf-8"))
    except Exception:
        return {}


async def run_backend_login(
    session_id: str, raw_headers: bytes, body: bytes
) -> None:
    sess = LOGIN_SESSIONS[session_id]
    port = ACTIVE_BACKEND_PORT
    try:
        lines = raw_headers.split(b"\r\n")
        lines[0] = b"POST /exa.language_server_pb.LanguageServerService/Login HTTP/1.1"
        rewritten, *_ = rewrite_headers(b"\r\n".join(lines), port, force_identity=True)

        reader, writer = await asyncio.open_connection(
            BACKEND_HOST, port, limit=1024 * 1024
        )
        writer.write(rewritten + body)
        await writer.drain()

        resp_hdr_buf = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=600.0)
        hdr_lines = resp_hdr_buf[:-4].split(b"\r\n")
        be_chunked = any(hl.lower() == b"transfer-encoding: chunked" for hl in hdr_lines[1:])
        be_cl = 0
        for hl in hdr_lines[1:]:
            if hl.lower().startswith(b"content-length:"):
                try:
                    be_cl = int(hl.split(b":", 1)[1].strip())
                except ValueError:
                    be_cl = 0
        if be_chunked or be_cl > 0:
            resp_body = await asyncio.wait_for(read_http_body(reader, be_cl, be_chunked), timeout=600.0)
        else:
            resp_body = await asyncio.wait_for(reader.read(), timeout=600.0)
        writer.close()

        status_code = 200
        try:
            status_code = int(hdr_lines[0].split(b" ")[1])
        except Exception:
            pass
        ct = "application/proto"
        for hl in hdr_lines[1:]:
            if hl.lower().startswith(b"content-type:"):
                ct = hl.split(b":", 1)[1].strip().decode("ascii", errors="ignore")
                break

        sess["status"] = status_code
        sess["content_type"] = ct
        sess["body"] = resp_body
    except Exception as exc:
        sess["status"] = 500
        sess["content_type"] = "application/json"
        sess["body"] = json.dumps({"code": "unknown", "message": str(exc)}).encode("utf-8")
    finally:
        sess["done"].set()


async def handle_start_login(
    raw_headers: bytes,
    csrf_token: str | None,
    body: bytes,
    client_writer: asyncio.StreamWriter,
) -> None:
    session_id = secrets.token_urlsafe(16)
    LOGIN_SESSIONS[session_id] = {
        "done": asyncio.Event(),
        "status": 200,
        "content_type": "application/proto",
        "body": b"",
    }
    asyncio.create_task(run_backend_login(session_id, raw_headers, body))

    auth_url = ""
    if csrf_token:
        await asyncio.sleep(0.5)
        res = await backend_rpc_json("SwitchLoginToAuthCode", csrf_token, {"openBrowser": False})
        auth_url = res.get("authUrl", "")
        if not auth_url:
            res2 = await backend_rpc_json("GetLoginSession", csrf_token, {})
            auth_url = res2.get("authUrl", "")

    resp_json = json.dumps({"id": session_id, "authUrl": auth_url}).encode("utf-8")
    client_writer.write(
        (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(resp_json)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii")
        + resp_json
    )
    await client_writer.drain()
    client_writer.close()


async def handle_poll_login(
    query: str, client_writer: asyncio.StreamWriter
) -> None:
    qs = parse_qs(query)
    session_id = (qs.get("id") or [""])[0]
    sess = LOGIN_SESSIONS.get(session_id)
    if not sess:
        msg = b'{"error":"unknown login session"}'
        client_writer.write(
            f"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\nContent-Length: {len(msg)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + msg
        )
        await client_writer.drain()
        client_writer.close()
        return

    try:
        await asyncio.wait_for(sess["done"].wait(), timeout=15.0)
    except asyncio.TimeoutError:
        client_writer.write(
            b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\nContent-Length: 2\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{}"
        )
        await client_writer.drain()
        client_writer.close()
        return

    body = sess["body"]
    status = sess["status"]
    ct = sess["content_type"]
    LOGIN_SESSIONS.pop(session_id, None)
    client_writer.write(
        (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/octet-stream\r\n"
            f"X-Bridge-Status: {status}\r\n"
            f"X-Bridge-Content-Type: {ct}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii")
        + body
    )
    await client_writer.drain()
    client_writer.close()


async def handle_icon(client_writer: asyncio.StreamWriter) -> None:
    try:
        with open(ICON_PATH, "rb") as f:
            data = f.read()
        client_writer.write(
            (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: image/png\r\n"
                f"Content-Length: {len(data)}\r\n"
                f"Cache-Control: public, max-age=86400\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii")
            + data
        )
    except OSError:
        client_writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    await client_writer.drain()
    client_writer.close()


def patch_html(html: bytes) -> bytes:
    html = re.sub(
        rb'<link\s+rel="icon".*?/>',
        b'<link rel="icon" type="image/png" href="/antigravity-icon.png" />',
        html,
        count=1,
        flags=re.DOTALL,
    )
    if b"</head>" in html and b"__agyBridgeInstalled" not in html:
        html = html.replace(b"</head>", INJECTED_SCRIPT + b"\n  </head>", 1)
    return html


async def handle_login_page(client_writer: asyncio.StreamWriter) -> None:
    body = LOGIN_PAGE_HTML.encode("utf-8")
    client_writer.write(
        (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii")
        + body
    )
    await client_writer.drain()
    client_writer.close()


async def handle_gate_login(
    raw_headers: bytes, body: bytes, client_writer: asyncio.StreamWriter
) -> None:
    try:
        data = json.loads(body.decode("utf-8"))
        pw = str(data.get("password", ""))
    except Exception:
        pw = ""
    if AUTH_PASSWORD and hmac.compare_digest(pw, AUTH_PASSWORD):
        tok = make_gate_cookie()
        is_https = b"x-forwarded-proto: https" in raw_headers.lower()
        sec_flag = "; Secure" if is_https else ""
        resp = b'{"ok":true}'
        client_writer.write(
            (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Set-Cookie: {GATE_COOKIE_NAME}={tok}; HttpOnly; SameSite=Lax; Path=/; Max-Age={GATE_SESSION_TTL}{sec_flag}\r\n"
                f"Content-Length: {len(resp)}\r\n"
                f"Cache-Control: no-store\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii")
            + resp
        )
    else:
        resp = b'{"error":"Invalid access password."}'
        client_writer.write(
            (
                f"HTTP/1.1 401 Unauthorized\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(resp)}\r\n"
                f"Cache-Control: no-store\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii")
            + resp
        )
    await client_writer.drain()
    client_writer.close()


async def handle_gate_logout(client_writer: asyncio.StreamWriter) -> None:
    resp = b'{"ok":true}'
    client_writer.write(
        (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Set-Cookie: {GATE_COOKIE_NAME}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0\r\n"
            f"Content-Length: {len(resp)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii")
        + resp
    )
    await client_writer.drain()
    client_writer.close()


async def handle_client(
    client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
) -> None:
    global LAST_WEBUI_MUTATION
    backend_writer = None
    try:
        header_buf = await asyncio.wait_for(
            client_reader.readuntil(b"\r\n\r\n"), timeout=15.0
        )
    except Exception:
        client_writer.close()
        return

    raw_headers = header_buf[:-4]
    first_line = raw_headers.split(b"\r\n", 1)[0]
    parts = first_line.split(b" ")
    method = parts[0] if len(parts) >= 1 else b"GET"
    target = parts[1].decode("ascii", errors="ignore") if len(parts) >= 2 else "/"
    parsed = urlparse(target)

    if parsed.path in ("/antigravity-icon.png", "/favicon.ico"):
        await handle_icon(client_writer)
        return

    if parsed.path == "/login" and method == b"GET":
        await handle_login_page(client_writer)
        return

    is_html_candidate = method == b"GET" and (
        parsed.path in ("/", "/onboarding", "/customizations") or "." not in parsed.path.rsplit("/", 1)[-1]
    )

    backend_port = ACTIVE_BACKEND_PORT
    rewritten, req_path, is_upgrade, content_length, is_chunked, csrf_token = rewrite_headers(
        raw_headers, backend_port, force_identity=is_html_candidate
    )

    if parsed.path == "/_bridge/gate_login" and method == b"POST":
        body = await read_http_body(client_reader, content_length, is_chunked)
        await handle_gate_login(raw_headers, body, client_writer)
        return

    if parsed.path == "/_bridge/gate_logout" and method == b"POST":
        await handle_gate_logout(client_writer)
        return

    is_public_asset = parsed.path in ("/jetbox.css", "/compiled_tailwind.css")
    if LOGIN_GATE_ENABLED and not is_public_asset and not verify_gate_cookie(raw_headers):
        accepts_html = b"text/html" in raw_headers.lower()
        if method == b"GET" and (is_html_candidate or accepts_html):
            loc = f"/login?next={quote(target)}" if target and target != "/" else "/login"
            client_writer.write(
                f"HTTP/1.1 302 Found\r\nLocation: {loc}\r\nContent-Length: 0\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".encode("ascii")
            )
        else:
            msg = b'{"error":"authentication required"}'
            client_writer.write(
                f"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\nContent-Length: {len(msg)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + msg
            )
        await client_writer.drain()
        client_writer.close()
        return

    if req_path in MUTATION_RPCS:
        LAST_WEBUI_MUTATION = time.monotonic()

    if parsed.path == "/_bridge/start_login" and method == b"POST":
        body = await read_http_body(client_reader, content_length, is_chunked)
        await handle_start_login(raw_headers, csrf_token, body, client_writer)
        return

    if parsed.path == "/_bridge/poll_login" and method == b"GET":
        await handle_poll_login(parsed.query, client_writer)
        return

    try:
        backend_reader, backend_writer = await asyncio.open_connection(
            BACKEND_HOST, backend_port, limit=1024 * 1024
        )
    except Exception:
        client_writer.write(
            b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 11\r\nConnection: close\r\n\r\nBad Gateway"
        )
        await client_writer.drain()
        client_writer.close()
        return

    backend_writer.write(rewritten)
    await backend_writer.drain()

    if is_upgrade:
        await asyncio.gather(
            pipe(client_reader, backend_writer),
            pipe(backend_reader, client_writer),
            return_exceptions=True,
        )
        return

    if is_html_candidate:
        try:
            resp_hdr_buf = await asyncio.wait_for(
                backend_reader.readuntil(b"\r\n\r\n"), timeout=15.0
            )
            hdr_text = resp_hdr_buf[:-4]
            if b"text/html" in hdr_text.lower():
                hdr_lower = hdr_text.lower()
                be_chunked = b"transfer-encoding: chunked" in hdr_lower
                be_cl = 0
                for hl in hdr_text.split(b"\r\n")[1:]:
                    if hl.lower().startswith(b"content-length:"):
                        try:
                            be_cl = int(hl.split(b":", 1)[1].strip())
                        except ValueError:
                            be_cl = 0
                if be_chunked or be_cl > 0:
                    resp_body = await read_http_body(backend_reader, be_cl, be_chunked)
                else:
                    resp_body = await backend_reader.read()
                patched = patch_html(resp_body)
                out_hdrs = [
                    l
                    for l in hdr_text.split(b"\r\n")
                    if not l.lower().startswith((b"content-length:", b"content-encoding:", b"transfer-encoding:"))
                ]
                out_hdrs.append(f"Content-Length: {len(patched)}".encode("ascii"))
                if not any(l.lower().startswith(b"connection:") for l in out_hdrs[1:]):
                    out_hdrs.append(b"Connection: close")
                client_writer.write(b"\r\n".join(out_hdrs) + b"\r\n\r\n" + patched)
                await client_writer.drain()
                client_writer.close()
                backend_writer.close()
                return
            else:
                client_writer.write(resp_hdr_buf)
                await client_writer.drain()
                await pipe(backend_reader, client_writer)
                backend_writer.close()
                return
        except Exception:
            client_writer.close()
            backend_writer.close()
            return

    async def forward_request_body() -> None:
        try:
            if content_length > 0:
                remaining = content_length
                while remaining > 0:
                    chunk = await client_reader.read(min(65536, remaining))
                    if not chunk:
                        break
                    backend_writer.write(chunk)
                    await backend_writer.drain()
                    remaining -= len(chunk)
            elif is_chunked:
                while True:
                    line = await client_reader.readline()
                    if not line:
                        break
                    backend_writer.write(line)
                    size = int(line.strip().split(b";", 1)[0], 16)
                    if size == 0:
                        while True:
                            trailer = await client_reader.readline()
                            backend_writer.write(trailer)
                            if trailer in (b"\r\n", b"\n", b""):
                                break
                        await backend_writer.drain()
                        break
                    data = await client_reader.readexactly(size + 2)
                    backend_writer.write(data)
                    await backend_writer.drain()
        except Exception:
            pass

    await asyncio.gather(
        forward_request_body(),
        pipe(backend_reader, client_writer),
        return_exceptions=True,
    )

    if backend_writer is not None:
        try:
            backend_writer.close()
        except Exception:
            pass


def start_language_server(csrf_token: str, port: int) -> subprocess.Popen:
    cmd = [
        LS_BIN,
        "--standalone",
        "--override_ide_name=antigravity",
        "--subclient_type=hub",
        f"--override_ide_version={IDE_VERSION}",
        "--override_user_agent_name=antigravity",
        f"--http_server_port={port}",
        "--https_server_port=0",
        f"--csrf_token={csrf_token}",
        f"--app_data_dir={APP_DATA_DIR}",
        "--api_server_url=https://generativelanguage.googleapis.com",
        "--cloud_code_endpoint=https://daily-cloudcode-pa.googleapis.com",
        "--enable_sidecars",
    ]
    return subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def wait_for_backend(port: int, timeout: float = 15.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            _, writer = await asyncio.open_connection(BACKEND_HOST, port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.15)
    raise TimeoutError(f"Backend {BACKEND_HOST}:{port} did not start in time")


def snapshot_cli_conversations() -> tuple[tuple[str, int, int], ...]:
    """Snapshot conversation DB/WAL/PB mtimes and sizes to detect agy updates."""
    entries = []
    patterns = [
        os.path.join(CLI_DATA_DIR, "conversations", "*.db*"),
        os.path.join(CLI_DATA_DIR, "conversations", "*.pb"),
        os.path.join(CLI_DATA_DIR, "jetbox_summaries_proto.pb"),
    ]
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            try:
                st = os.stat(path)
                entries.append((path, st.st_mtime_ns, st.st_size))
            except OSError:
                pass
    return tuple(entries)


async def sync_watcher(
    csrf_token: str,
    state: dict,
    stop_event: asyncio.Event,
) -> None:
    """Blue-green hot-swap language_server when agy updates conversation files."""
    global ACTIVE_BACKEND_PORT
    last_snap = snapshot_cli_conversations()
    last_swap = time.monotonic()

    while not stop_event.is_set():
        await asyncio.sleep(2.0)
        if LOGIN_SESSIONS or (time.monotonic() - LAST_WEBUI_MUTATION < 60.0):
            last_snap = snapshot_cli_conversations()
            continue
        cur_snap = snapshot_cli_conversations()
        if cur_snap == last_snap:
            continue
        traj_res = await backend_rpc_json("GetAllCascadeTrajectories", csrf_token, {})
        summaries = traj_res.get("trajectorySummaries", {}) if isinstance(traj_res, dict) else {}
        if any(v.get("status") == "CASCADE_RUN_STATUS_RUNNING" for v in summaries.values()):
            last_snap = cur_snap
            continue
        await asyncio.sleep(1.0)
        if LOGIN_SESSIONS or (time.monotonic() - LAST_WEBUI_MUTATION < 60.0):
            last_snap = snapshot_cli_conversations()
            continue
        cur_snap = snapshot_cli_conversations()
        if cur_snap == last_snap:
            continue
        if time.monotonic() - last_swap < 15.0:
            last_snap = cur_snap
            continue

        new_port = STANDBY_PORT if ACTIVE_BACKEND_PORT == PRIMARY_PORT else PRIMARY_PORT
        old_proc = state.get("proc")
        try:
            new_proc = start_language_server(csrf_token, new_port)
            await wait_for_backend(new_port, timeout=15.0)
            await asyncio.sleep(0.3)
            if LOGIN_SESSIONS or (time.monotonic() - LAST_WEBUI_MUTATION < 60.0):
                await asyncio.to_thread(stop_process, new_proc)
                last_snap = snapshot_cli_conversations()
                continue
            traj_res2 = await backend_rpc_json("GetAllCascadeTrajectories", csrf_token, {})
            summaries2 = traj_res2.get("trajectorySummaries", {}) if isinstance(traj_res2, dict) else {}
            if any(v.get("status") == "CASCADE_RUN_STATUS_RUNNING" for v in summaries2.values()):
                await asyncio.to_thread(stop_process, new_proc)
                last_snap = snapshot_cli_conversations()
                continue
            ACTIVE_BACKEND_PORT = new_port
            state["proc"] = new_proc
            last_swap = time.monotonic()
            last_snap = snapshot_cli_conversations()
            print(
                f"[antigravity-web-bridge] Synced agy state via hot-swap -> port {new_port}",
                flush=True,
            )
            await asyncio.sleep(0.5)
            await asyncio.to_thread(stop_process, old_proc)
        except Exception as exc:
            print(f"[antigravity-web-bridge] Hot-swap failed: {exc}", flush=True)
            last_snap = cur_snap


async def main() -> None:
    if not os.path.isfile(LS_BIN):
        raise FileNotFoundError(f"language_server binary not found at {LS_BIN}")
    csrf_token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN") or secrets.token_urlsafe(24)
    state = {"proc": start_language_server(csrf_token, ACTIVE_BACKEND_PORT)}

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    watcher_task = None
    try:
        await wait_for_backend(ACTIVE_BACKEND_PORT)
        if SYNC_ENABLED:
            watcher_task = asyncio.create_task(sync_watcher(csrf_token, state, stop_event))
        server = await asyncio.start_server(
            handle_client, LISTEN_HOST, LISTEN_PORT, reuse_address=True, limit=1024 * 1024
        )
        print(
            f"[antigravity-web-bridge] Serving Antigravity WebUI on http://{LISTEN_HOST}:{LISTEN_PORT} -> http://{BACKEND_HOST}:{ACTIVE_BACKEND_PORT} (gate={'on' if LOGIN_GATE_ENABLED else 'off'})",
            flush=True,
        )
        async with server:
            await stop_event.wait()
    finally:
        if watcher_task is not None:
            watcher_task.cancel()
        stop_process(state.get("proc"))


if __name__ == "__main__":
    asyncio.run(main())
EOF