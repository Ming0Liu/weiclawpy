"""OpenCode HTTP API client — full REST client for opencode serve."""

import json
import requests

from .opencode_serve import ensure_running, SERVER_BASE, _get_auth_headers

_API_TIMEOUT = 60
_SHORT_TIMEOUT = 10

_providers_cache = None


def _api_get(path: str, timeout: int = _SHORT_TIMEOUT) -> dict:
    r = requests.get(f"{SERVER_BASE}{path}", timeout=timeout, headers=_get_auth_headers())
    r.raise_for_status()
    try:
        return r.json()
    except json.JSONDecodeError:
        return {"_raw": r.text}


def _api_post(path: str, body: dict | None = None, timeout: int = _API_TIMEOUT) -> dict:
    r = requests.post(f"{SERVER_BASE}{path}", json=body or {}, timeout=timeout, headers=_get_auth_headers())
    if not r.ok:
        detail = ""
        try:
            detail = r.text[:500]
        except Exception:
            pass
        msg = f"{r.status_code} {r.reason}"
        if detail:
            msg += f" — {detail}"
        raise requests.HTTPError(msg, response=r)
    try:
        return r.json()
    except json.JSONDecodeError:
        return {"_raw": r.text}


def _api_delete(path: str, timeout: int = _SHORT_TIMEOUT) -> dict:
    r = requests.delete(f"{SERVER_BASE}{path}", timeout=timeout, headers=_get_auth_headers())
    r.raise_for_status()
    try:
        return r.json()
    except json.JSONDecodeError:
        return {}


def _api_patch(path: str, body: dict, timeout: int = _SHORT_TIMEOUT) -> dict:
    r = requests.patch(f"{SERVER_BASE}{path}", json=body, timeout=timeout, headers=_get_auth_headers())
    r.raise_for_status()
    try:
        return r.json()
    except json.JSONDecodeError:
        return {}


# ── Health ──


def health() -> dict:
    ensure_running()
    return _api_get("/global/health", timeout=_SHORT_TIMEOUT)

# ── Sessions ──


def list_sessions() -> list[dict]:
    ensure_running()
    return _api_get("/session")


def create_session(parent_id: str = "", title: str = "") -> dict:
    ensure_running()
    body: dict[str, str] = {}
    if parent_id:
        body["parentID"] = parent_id
    if title:
        body["title"] = title
    return _api_post("/session", body)


def get_session(session_id: str) -> dict:
    ensure_running()
    return _api_get(f"/session/{session_id}")


def delete_session(session_id: str) -> bool:
    ensure_running()
    try:
        r = requests.delete(f"{SERVER_BASE}/session/{session_id}", timeout=_SHORT_TIMEOUT, headers=_get_auth_headers())
        return r.ok
    except Exception:
        return False


def abort_session(session_id: str) -> bool:
    ensure_running()
    result = _api_post(f"/session/{session_id}/abort")
    return result.get("success", False)


def fork_session(session_id: str, message_id: str = "") -> dict:
    ensure_running()
    body = {}
    if message_id:
        body["messageID"] = message_id
    return _api_post(f"/session/{session_id}/fork", body)


def get_session_status(session_id: str) -> dict:
    ensure_running()
    return _api_get(f"/session/{session_id}/todo")

# ── Model resolution ──


def _resolve_model(model: str) -> dict:
    """Convert model string to {providerID, modelID} object.

    Accepts:
      - "provider/model" format → {"providerID": "provider", "modelID": "model"}
      - plain model ID → tries to find matching provider, falls back to raw string
    """
    if not model:
        return None

    if isinstance(model, dict):
        return model

    if "/" in model:
        provider_id, model_id = model.split("/", 1)
        return {"providerID": provider_id.strip(), "modelID": model_id.strip()}

    global _providers_cache
    if _providers_cache is None:
        try:
            data = _api_get("/config/providers", timeout=_SHORT_TIMEOUT)
            _providers_cache = data.get("providers", [])
        except Exception:
            _providers_cache = []

    for p in _providers_cache:
        if not isinstance(p, dict):
            continue
        models = p.get("models", [])
        for m in models:
            m_id = m.get("id") if isinstance(m, dict) else m
            if m_id == model:
                return {"providerID": p.get("id", ""), "modelID": model}

    return model


def find_model(input_str: str) -> tuple[str | None, str | None, str | None]:
    """Resolve user input to (provider_id, model_id, display_name).

    Accepts:
      - "provider/model" → validates both against known providers
      - "model" → searches all providers
    Returns (None, None, None) if no match found.
    """
    try:
        data = _api_get("/config/providers", timeout=_SHORT_TIMEOUT)
        providers = data.get("providers", [])
    except Exception:
        return (None, None, None)

    if "/" in input_str:
        parts = input_str.split("/", 1)
        pid, mid = parts[0].strip(), parts[1].strip()
        for p in providers:
            if not isinstance(p, dict):
                continue
            if p.get("id", "").lower() == pid.lower():
                for m in p.get("models", []):
                    m_id = m.get("id") if isinstance(m, dict) else str(m)
                    if m_id.lower() == mid.lower():
                        return (p.get("id"), mid, f"{p.get('id')}/{mid}")
        return (None, None, None)

    for p in providers:
        if not isinstance(p, dict):
            continue
        for m in p.get("models", []):
            if isinstance(m, dict):
                if m.get("id", "").lower() == input_str.lower():
                    return (p.get("id"), m.get("id"), f"{p.get('id')}/{m.get('id')}")
            elif str(m).lower() == input_str.lower():
                return (p.get("id"), str(m), f"{p.get('id')}/{m}")

    return (None, None, None)


# ── Messages ──


def send_message(session_id: str, text: str,
                 model: str | None = None,
                 agent: str | None = None) -> dict:
    """Send a text message and wait for the response."""
    body: dict = {
        "parts": [{"type": "text", "text": text}],
    }
    if model:
        resolved = _resolve_model(model)
        if isinstance(resolved, dict):
            body["model"] = resolved
    if agent:
        body["agent"] = agent
    return _api_post(f"/session/{session_id}/message", body, timeout=300)


def execute_command(session_id: str, command: str,
                    arguments: str = "",
                    model: str | None = None,
                    agent: str | None = None) -> dict:
    """Execute an OpenCode slash command in a session."""
    body: dict = {
        "command": command,
        "arguments": arguments,
    }
    if model:
        resolved = _resolve_model(model)
        if isinstance(resolved, dict):
            body["model"] = resolved
    if agent:
        body["agent"] = agent
    return _api_post(f"/session/{session_id}/command", body, timeout=300)

# ── Config / Providers ──


def get_providers() -> dict:
    ensure_running()
    return _api_get("/config/providers")


def get_config() -> dict:
    ensure_running()
    return _api_get("/config")


def update_config(config: dict) -> dict:
    ensure_running()
    return _api_patch("/config", config)

# ── Agents ──


def list_agents() -> list[dict]:
    ensure_running()
    return _api_get("/agent")

# ── Commands ──


def list_opencode_commands() -> list[dict]:
    ensure_running()
    return _api_get("/command")

# ── Reply extraction ──


def extract_reply_text(response: dict) -> str:
    """Extract assistant text parts from a message/command response."""
    parts = response.get("parts", [])
    texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    return "".join(texts)


def extract_reply_images(response: dict) -> list[str]:
    """Extract image URLs from assistant response parts."""
    urls = []
    for p in response.get("parts", []):
        if p.get("type") in ("image", "img"):
            url = p.get("url") or p.get("source", {}).get("url", "")
            if url:
                urls.append(url)
    return urls
