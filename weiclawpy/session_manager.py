"""Per-user session management — maps WeChat users to OpenCode sessions."""

import json
import os
from pathlib import Path
from threading import Lock

from . import opencode_api as api

STATE_DIR = Path(os.environ.get("WEICLAWPY_DIR", Path.home() / ".weiclawpy"))
STATE_FILE = STATE_DIR / "user_sessions.json"
_lock = Lock()


def _load() -> dict[str, dict]:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception:
        pass
    return {}


def _save(data: dict[str, dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, STATE_FILE)


def ensure_session(wx_user_id: str) -> str:
    """Get user's current session, creating one if needed."""
    with _lock:
        data = _load()
        user = data.get(wx_user_id, {})
        session_id = user.get("session_id", "")

        if session_id:
            try:
                api.get_session(session_id)
                return session_id
            except Exception:
                pass

        session = api.create_session(title=f"wx-{wx_user_id}")
        new_id = session.get("id") or session.get("session_id", "")
        if not new_id:
            raise RuntimeError(f"创建会话失败: {session}")
        data[wx_user_id] = {**user, "session_id": new_id}
        _save(data)
        return new_id


def new_session(wx_user_id: str) -> str:
    """Create a brand-new session for user, cleaning up the old one."""
    with _lock:
        data = _load()
        user = data.get(wx_user_id, {})
        old_id = user.get("session_id", "")

        session = api.create_session(title=f"wx-{wx_user_id}")
        new_id = session.get("id") or session.get("session_id", "")
        if not new_id:
            raise RuntimeError(f"创建会话失败: {session}")
        data[wx_user_id] = {**user, "session_id": new_id}
        _save(data)

        if old_id:
            try:
                api.delete_session(old_id)
            except Exception:
                pass
        return new_id


def set_pref(wx_user_id: str, key: str, value) -> None:
    with _lock:
        data = _load()
        if wx_user_id not in data:
            data[wx_user_id] = {}
        data[wx_user_id][key] = value
        _save(data)


def get_pref(wx_user_id: str, key: str, default=None):
    data = _load()
    return data.get(wx_user_id, {}).get(key, default)


def get_user_info(wx_user_id: str) -> dict:
    return dict(_load().get(wx_user_id, {}))


def get_all_users() -> dict[str, dict]:
    return dict(_load())


def delete_user(wx_user_id: str) -> None:
    with _lock:
        data = _load()
        data.pop(wx_user_id, None)
        _save(data)
