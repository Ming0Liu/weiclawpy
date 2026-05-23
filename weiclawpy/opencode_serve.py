"""opencode serve process manager — start, stop, health check."""

import base64
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import requests

OPENCODE_SERVE_PORT = 4096
OPENCODE_SERVE_HOST = "127.0.0.1"
SERVER_BASE = f"http://{OPENCODE_SERVE_HOST}:{OPENCODE_SERVE_PORT}"
_HEALTH_URL = f"{SERVER_BASE}/global/health"

_server_proc: subprocess.Popen | None = None
_server_lock = threading.Lock()


def _get_auth_headers() -> dict[str, str]:
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if not password:
        return {}
    username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {auth}"}


def _find_opencode() -> str:
    """Locate the opencode executable."""
    exe = shutil.which("opencode")
    if exe:
        return exe

    if os.name == "nt":
        npm_root = subprocess.run(
            ["npm.cmd", "root", "-g"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if npm_root:
            for candidate in [
                Path(npm_root) / ".bin" / "opencode.cmd",
                Path(npm_root) / ".bin" / "opencode",
                Path(npm_root) / "opencode-ai" / "bin" / "opencode",
            ]:
                if candidate.exists():
                    return str(candidate)

    raise FileNotFoundError(
        "opencode 未找到。请安装: npm install -g opencode-ai"
    )


def is_running() -> bool:
    try:
        r = requests.get(_HEALTH_URL, timeout=2, headers=_get_auth_headers())
        return r.ok
    except Exception:
        return False


def ensure_running(timeout: float = 15) -> None:
    if is_running():
        return

    with _server_lock:
        if is_running():
            return

        global _server_proc
        try:
            opencode_path = _find_opencode()
            print(f"🚀 OpenCode 服务启动中 (Host: {OPENCODE_SERVE_HOST}, Port: {OPENCODE_SERVE_PORT})...")
            _server_proc = subprocess.Popen(
                [opencode_path, "serve",
                 "--port", str(OPENCODE_SERVE_PORT),
                 "--hostname", OPENCODE_SERVE_HOST],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
        except FileNotFoundError:
            raise RuntimeError(
                "opencode CLI 未安装，请运行: npm install -g opencode-ai"
            )
        except Exception as e:
            raise RuntimeError(f"启动 opencode serve 失败: {e}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_running():
                return
            time.sleep(0.5)

    raise RuntimeError(f"opencode serve 在 {timeout}s 内未就绪")


def stop() -> None:
    global _server_proc
    with _server_lock:
        if _server_proc is not None:
            try:
                _server_proc.terminate()
                _server_proc.wait(timeout=3)
            except Exception:
                try:
                    _server_proc.kill()
                except Exception:
                    pass
            _server_proc = None
