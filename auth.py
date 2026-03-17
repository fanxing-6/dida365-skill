#!/usr/bin/env python3
"""Dida365 OAuth helpers."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

TOKEN_FILE = Path(__file__).with_name(".dida-token.json")
DEFAULT_CALLBACK_PORT = 18365
OAUTH_BASE = "https://dida365.com/oauth"


def load_env_file() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def save_token(token_data: dict[str, Any]) -> None:
    payload = dict(token_data)
    payload["obtained_at"] = int(time.time())
    TOKEN_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_token_data() -> dict[str, Any]:
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))


def get_access_token() -> str:
    load_env_file()
    manual_token = os.environ.get("DIDA_ACCESS_TOKEN")
    if manual_token:
        return manual_token

    if TOKEN_FILE.exists():
        token_data = load_token_data()
        access_token = token_data.get("access_token")
        if access_token:
            return access_token

    raise RuntimeError("未找到 access token。请先运行: python3 index.py auth")


def _require_oauth_client() -> tuple[str, str]:
    load_env_file()
    client_id = os.environ.get("DIDA_CLIENT_ID")
    client_secret = os.environ.get("DIDA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("缺少 DIDA_CLIENT_ID 或 DIDA_CLIENT_SECRET。请在 .env 文件中配置。")
    return client_id, client_secret


def _request_token(payload: dict[str, str], client_id: str, client_secret: str) -> dict[str, Any]:
    basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{OAUTH_BASE}/token",
        data=body,
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Token 换取失败: HTTP {exc.code} - {text}") from exc


def exchange_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict[str, Any]:
    return _request_token(
        {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        client_id,
        client_secret,
    )


def refresh_access_token() -> str:
    load_env_file()
    if os.environ.get("DIDA_ACCESS_TOKEN"):
        raise RuntimeError("使用 DIDA_ACCESS_TOKEN 时无法自动刷新，请更新环境变量中的 token。")

    if not TOKEN_FILE.exists():
        raise RuntimeError("未找到 token 文件，无法刷新 access token。")

    token_data = load_token_data()
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("当前 token 不包含 refresh_token，请重新运行授权流程。")

    client_id, client_secret = _require_oauth_client()
    refreshed = _request_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "tasks:write tasks:read",
        },
        client_id,
        client_secret,
    )

    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh_token

    save_token(refreshed)
    return refreshed["access_token"]


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if error:
            self.server.auth_result = {"error": error}  # type: ignore[attr-defined]
            self.wfile.write(
                "<h1>授权失败</h1><p>请关闭此页面，返回终端重试。</p>".encode("utf-8")
            )
            return

        if code:
            self.server.auth_result = {"code": code}  # type: ignore[attr-defined]
            self.wfile.write(
                "<h1>授权成功</h1><p>请关闭此页面，返回终端继续。</p>".encode("utf-8")
            )
            return

        self.server.auth_result = {"error": "missing_code"}  # type: ignore[attr-defined]
        self.wfile.write("<h1>未收到授权码</h1>".encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        return


def _wait_for_callback(port: int) -> str:
    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.auth_result = {}  # type: ignore[attr-defined]
    try:
        server.handle_request()
        auth_result = server.auth_result  # type: ignore[attr-defined]
    finally:
        server.server_close()

    error = auth_result.get("error")
    if error:
        raise RuntimeError(f"授权失败: {error}")

    code = auth_result.get("code")
    if not code:
        raise RuntimeError("授权失败，未获取到 authorization code")
    return code


def run_oauth_flow(authorization_code: str | None = None) -> None:
    load_env_file()
    client_id, client_secret = _require_oauth_client()
    port = int(os.environ.get("DIDA_CALLBACK_PORT", str(DEFAULT_CALLBACK_PORT)))
    redirect_uri = f"http://localhost:{port}/callback"
    scope = "tasks:write tasks:read"
    auth_url = (
        f"{OAUTH_BASE}/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "scope": scope,
                "redirect_uri": redirect_uri,
                "response_type": "code",
            }
        )
    )

    print("\n=== 滴答清单 OAuth 授权 ===\n")

    if authorization_code:
        code = authorization_code
        print("使用手动提供的 authorization code 交换 access token...\n")
    else:
        print("请在浏览器中打开以下链接完成授权:\n")
        print(auth_url)
        print(
            "\n等待授权回调...\n"
            "如果当前环境是远程服务器，可在本机浏览器完成授权后，"
            "从重定向地址里复制 code，再执行:\n"
            "python3 index.py auth --code <authorization_code>\n"
        )
        code = _wait_for_callback(port)
        print("收到授权码，正在换取 access token...")

    token_data = exchange_token(code, client_id, client_secret, redirect_uri)
    save_token(token_data)
    print("授权成功。Token 已保存到 .dida-token.json")
    print("现在可以使用其他命令了，如: python3 index.py projects")
