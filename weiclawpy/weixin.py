"""WeChat ilinkai API client — 登录、轮询收消息、发消息、消息解析."""

import hashlib
import json
import os
import time
import random
import base64
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Callable

import requests

from .cdn import aes_ecb_encrypt

BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"

LONG_POLL_TIMEOUT = 35
API_TIMEOUT = 15

CRED_DIR = Path.home() / ".weiclawpy"
CRED_FILE = CRED_DIR / "credentials.json"
CTX_FILE = CRED_DIR / "context_tokens.json"
OLD_CRED_DIR = Path.home() / ".weiclaw"
OLD_CRED_FILE = OLD_CRED_DIR / "credentials.json"


# ── 凭证管理 ────────────────────────────────────────────────

def load_credentials() -> Optional[dict]:
    try:
        if CRED_FILE.exists():
            data = json.loads(CRED_FILE.read_text("utf-8"))
            return data if data.get("token") else None
        if OLD_CRED_FILE.exists():
            data = json.loads(OLD_CRED_FILE.read_text("utf-8"))
            if data.get("token"):
                save_credentials(data)
                return data
        return None
    except Exception:
        return None


def save_credentials(data: dict) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    data["saved_at"] = datetime.now(timezone.utc).isoformat()
    CRED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def save_context_token(user_id: str, context_token: str) -> None:
    tokens = load_context_tokens()
    if not context_token:
        return
    tokens[user_id] = context_token
    CTX_FILE.write_text(json.dumps(tokens, indent=2, ensure_ascii=False), "utf-8")


def load_context_tokens() -> dict[str, str]:
    try:
        if CTX_FILE.exists():
            return json.loads(CTX_FILE.read_text("utf-8"))
    except Exception:
        pass
    return {}


# ── 登录 ────────────────────────────────────────────────────

def get_qrcode() -> dict:
    r = requests.get(f"{BASE_URL}/ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}",
                     timeout=API_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if _VERBOSE:
        print(f"   [DEBUG] QR code: {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)
    return data


def poll_qrcode_status(qrcode: str) -> dict:
    r = requests.get(
        f"{BASE_URL}/ilink/bot/get_qrcode_status?qrcode={qrcode}",
        timeout=LONG_POLL_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def login_via_qr(display_qr: Callable[[str], None]) -> dict:
    qr = get_qrcode()
    display_qr(qr["qrcode_img_content"])

    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            status = poll_qrcode_status(qr["qrcode"])
        except requests.ReadTimeout:
            continue
        except requests.RequestException:
            time.sleep(1)
            continue

        s = status.get("status")
        if s == "scaned":
            print("👀 已扫码，请在微信确认...", flush=True)
        elif s == "confirmed":
            if _VERBOSE:
                print(f"   [DEBUG] 登录响应: {json.dumps(status, ensure_ascii=False)[:500]}", flush=True)
            creds = {
                "token": status["bot_token"],
                "user_id": status.get("ilink_user_id"),
                "account_id": status.get("ilink_bot_id"),
            }
            save_credentials(creds)
            return creds
        elif s == "expired":
            raise RuntimeError("二维码已过期，请重试")

        time.sleep(1)

    raise RuntimeError("登录超时")


# ── HTTP 工具 ──────────────────────────────────────────────

_VERBOSE = False
_poll_count = 0


def set_verbose(v: bool) -> None:
    global _VERBOSE
    _VERBOSE = v


def _build_headers(token: str, body_bytes: bytes) -> dict[str, str]:
    uin = str(random.randint(0, 0xFFFFFFFF))
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "Content-Length": str(len(body_bytes)),
        "X-WECHAT-UIN": base64.b64encode(uin.encode()).decode(),
        "iLink-App-ClientVersion": "1",
    }


def _api_post(endpoint: str, body: dict, token: str, timeout: int) -> Optional[dict]:
    url = f"{BASE_URL}/{endpoint}"
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = _build_headers(token, body_bytes)
    try:
        t0 = time.time()
        r = requests.post(url, headers=headers, data=body_bytes, timeout=timeout)
        elapsed = time.time() - t0
        if _VERBOSE:
            print(f"   [HTTP] POST {endpoint} → {r.status_code} ({len(r.content)}B, {elapsed:.1f}s)", flush=True)
            if elapsed < 2.0:
                print(f"   [HTTP] body: {r.text[:500]}", flush=True)
        if not r.ok:
            if _VERBOSE:
                print(f"   [HTTP] body: {r.text[:500]}", flush=True)
            raise RuntimeError(f"{endpoint} {r.status_code}: {r.text[:200]}")
        return r.json()
    except requests.ReadTimeout:
        if _VERBOSE:
            print(f"   [HTTP] POST {endpoint} → 超时 (long-poll)", flush=True)
        return None
    except requests.RequestException as e:
        print(f"   [{endpoint}] 请求异常: {e}", flush=True)
        return None
    except ValueError as e:
        print(f"   [{endpoint}] JSON 解析失败: {e}", flush=True)
        return None


# ── 消息 API ───────────────────────────────────────────────

def _build_msg_body(to: str, context_token: str, item_list: list) -> dict:
    return {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": str(uuid.uuid4()),
            "message_type": 2,
            "message_state": 2,
            "item_list": item_list,
            "context_token": context_token,
        },
        "base_info": {},
    }


def _call_send_api(body: dict, token: str, label: str = "send") -> bool:
    resp = _api_post("ilink/bot/sendmessage", body, token, API_TIMEOUT)
    if resp is None:
        print(f"   {label}: API 无响应（超时）", flush=True)
        return False
    if resp.get("ret") not in (0, None):
        print(f"   {label}: ret={resp.get('ret')} {resp.get('errmsg', '')}", flush=True)
        return False
    return True


def get_updates(token: str, buf: str = "", bot_id: str = "") -> dict:
    """Long-poll 获取新消息. 返回 {'msgs': [...], 'get_updates_buf': str}."""
    global _poll_count
    _poll_count += 1

    body = {"get_updates_buf": buf, "base_info": {}}
    if bot_id:
        body["ilink_bot_id"] = bot_id

    resp = _api_post("ilink/bot/getupdates", body, token, LONG_POLL_TIMEOUT)
    if resp is None:
        if _VERBOSE and _poll_count <= 3:
            print(f"   [poll #{_poll_count}] 无消息 (超时/空响应)", flush=True)
        return {"msgs": [], "get_updates_buf": buf}
    if resp.get("ret") not in (0, None):
        print(f"   getUpdates ret={resp.get('ret')} {resp.get('errmsg', '')}, token 可能已过期", flush=True)
        if _VERBOSE:
            print(f"   [poll #{_poll_count}] 响应: {json.dumps(resp, ensure_ascii=False)[:500]}", flush=True)
        return {"msgs": [], "get_updates_buf": buf}

    msgs = resp.get("msgs", [])
    if _VERBOSE:
        print(f"   [poll #{_poll_count}] 收到 {len(msgs)} 条消息, buf_len={len(resp.get('get_updates_buf', ''))}", flush=True)
        if msgs and _poll_count <= 3:
            print(f"   [poll #{_poll_count}] 原始消息: {json.dumps(msgs, ensure_ascii=False)[:800]}", flush=True)

    return {"msgs": msgs, "get_updates_buf": resp.get("get_updates_buf", buf)}


def send_message(token: str, to: str, text: str, context_token: str = "") -> bool:
    body = _build_msg_body(to, context_token, [{"type": 1, "text_item": {"text": text}}])
    return _call_send_api(body, token, "send_message")


def send_image_by_url(token: str, to: str, context_token: str, image_url: str) -> bool:
    body = _build_msg_body(to, context_token, [{"type": 2, "image_item": {"url": image_url}}])
    return _call_send_api(body, token, "send_image_by_url")


def send_file_by_url(token: str, to: str, context_token: str,
                     file_url: str, file_name: str) -> bool:
    body = _build_msg_body(to, context_token, [{
        "type": 4,
        "file_item": {"url": file_url, "file_name": file_name},
    }])
    return _call_send_api(body, token, "send_file_by_url")


def get_upload_url(token: str, filekey: str, to_user_id: str,
                   media_type: int, rawsize: int, rawfilemd5: str,
                   filesize: int, aeskey_hex: str) -> dict | None:
    """获取 CDN 上传预签名 URL."""
    body = {
        "filekey": filekey,
        "to_user_id": to_user_id,
        "media_type": media_type,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": filesize,
        "no_need_thumb": True,
        "aeskey": aeskey_hex,
        "base_info": {},
    }
    resp = _api_post("ilink/bot/getuploadurl", body, token, API_TIMEOUT)
    if resp is None:
        print("   getuploadurl: API 无响应（超时）", flush=True)
        return None
    if resp.get("ret") not in (0, None):
        print(f"   getuploadurl: ret={resp.get('ret')} {resp.get('errmsg', '')}", flush=True)
        return None
    return resp


def send_file(token: str, to: str, file_path: str, context_token: str = "") -> bool:
    """发送本地文件（PDF 等）到微信用户."""

    path = Path(file_path)
    if not path.exists():
        print(f"   文件不存在: {file_path}", flush=True)
        return False

    file_bytes = path.read_bytes()
    file_name = path.name
    rawsize = len(file_bytes)
    rawfilemd5 = hashlib.md5(file_bytes).hexdigest()

    aes_key = os.urandom(16)
    aes_key_hex = aes_key.hex()
    filekey = os.urandom(16).hex()

    encrypted = aes_ecb_encrypt(file_bytes, aes_key)
    filesize = len(encrypted)

    print(f"   文件: {file_name} ({rawsize} bytes, md5={rawfilemd5})", flush=True)

    upload_info = get_upload_url(token, filekey, to, 3,
                                 rawsize, rawfilemd5, filesize, aes_key_hex)
    if not upload_info:
        print(f"   获取上传 URL 失败", flush=True)
        return False

    upload_param = upload_info.get("upload_param", "")
    cdn_base = upload_info.get("cdn_base") or "https://novac2c.cdn.weixin.qq.com/c2c"

    upload_url = f"{cdn_base}/upload?encrypted_query_param={upload_param}&filekey={filekey}"
    if upload_info.get("upload_full_url"):
        upload_url = upload_info["upload_full_url"]

    print(f"   上传到 CDN: {upload_url[:80]}...", flush=True)
    try:
        r = requests.post(upload_url, data=encrypted,
                          headers={"Content-Type": "application/octet-stream"},
                          timeout=60)
        if not r.ok:
            print(f"   CDN upload: HTTP {r.status_code} {r.text[:200]}", flush=True)
            return False

        encrypt_query_param = r.headers.get("x-encrypted-param", "")
        if not encrypt_query_param:
            print(f"   CDN upload: 未返回 x-encrypted-param", flush=True)
            return False
    except Exception as e:
        print(f"   CDN upload 异常: {e}", flush=True)
        return False

    aes_key_b64 = base64.b64encode(aes_key_hex.encode()).decode()

    body = _build_msg_body(to, context_token, [{
        "type": 4,
        "file_item": {
            "media": {
                "encrypt_query_param": encrypt_query_param,
                "aes_key": aes_key_b64,
                "encrypt_type": 1,
            },
            "file_name": file_name,
            "len": str(rawsize),
            "md5": rawfilemd5,
        },
    }])
    return _call_send_api(body, token, "send_file")


# ── 消息解析 ────────────────────────────────────────────────

def extract_text(msg: dict) -> str:
    """从消息中提取文本（含语音转文字）."""
    for item in msg.get("item_list", []):
        if item.get("type") == 1:
            return item.get("text_item", {}).get("text", "")
        if item.get("type") == 3:
            return item.get("voice_item", {}).get("text", "")
    return ""


def extract_media(msg: dict) -> Optional[dict]:
    """从消息中提取多媒体信息. 返回 {type, encrypt_query_param, aes_key, ...}."""
    for item in msg.get("item_list", []):
        t = item.get("type")

        if t == 2:  # image
            img = item.get("image_item", {})
            m = img.get("media", {})
            if m.get("encrypt_query_param"):
                aes_key = m.get("aes_key")
                if not aes_key and img.get("aeskey"):
                    aes_key = base64.b64encode(
                        bytes.fromhex(img["aeskey"])
                    ).decode()
                return {"type": "image",
                        "encrypt_query_param": m["encrypt_query_param"],
                        "aes_key": aes_key}

        elif t == 3:  # voice
            v = item.get("voice_item", {})
            m = v.get("media", {})
            if m.get("encrypt_query_param"):
                return {"type": "voice",
                        "encrypt_query_param": m["encrypt_query_param"],
                        "aes_key": m.get("aes_key"),
                        "voice_text": v.get("text")}

        elif t == 4:  # file
            f = item.get("file_item", {})
            m = f.get("media", {})
            if m.get("encrypt_query_param"):
                return {"type": "file",
                        "encrypt_query_param": m["encrypt_query_param"],
                        "aes_key": m.get("aes_key"),
                        "file_name": f.get("file_name", "file.bin")}

    return None


def get_weixin_config(token: str, user_id: str, context_token: str = "") -> dict | None:
    body = {
        "ilink_user_id": user_id,
        "context_token": context_token,
    }
    return _api_post("ilink/bot/getconfig", body, token, API_TIMEOUT)


def send_typing(token: str, user_id: str, typing_ticket: str, status: int) -> None:
    body = {
        "ilink_user_id": user_id,
        "typing_ticket": typing_ticket,
        "status": status,
    }
    _api_post("ilink/bot/sendtyping", body, token, API_TIMEOUT)
