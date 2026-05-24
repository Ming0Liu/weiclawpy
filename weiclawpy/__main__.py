import base64
import json
import re
import sys
import time
import traceback
import uuid
from argparse import ArgumentParser
from pathlib import Path

from .cdn import download_and_decrypt
from .logger import log_response
from .opencode_api import (
    send_message as api_send_message,
    extract_reply_text,
    extract_reply_images,
    health,
)
from .opencode_cmd import handle_text as handle_cmd
from .opencode_serve import ensure_running, stop as stop_server
from .session_manager import (
    ensure_session, new_session, get_pref,
)
from .weixin import (
    CRED_DIR, load_credentials, login_via_qr, get_updates,
    send_message, send_image_by_url, send_file,
    extract_text, extract_media, set_verbose,
    save_context_token, load_context_tokens,
    get_weixin_config, send_typing, TokenExpiredError,
)

IMAGE_TTL = 300
_TYPING_CACHE: dict[str, dict] = {}
_TYPING_CACHE_TTL = 30


def _get_creds(relogin: bool) -> dict:
    creds = None if relogin else load_credentials()
    if not creds:
        print("📱 首次使用，请扫码登录微信\n")
        try:
            creds = login_via_qr(_display_qr)
        except Exception as e:
            print(f"❌ 登录失败: {e}")
            raise
        print("✅ 微信登录成功！")
    return creds


def _relogin() -> dict:
    """Token 过期后清除凭证并触发重新扫码登录."""
    print("\n⚠️ 微信 token 已过期，清除凭证并重新登录...", flush=True)
    cred_path = CRED_DIR / "credentials.json"
    try:
        cred_path.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        creds = login_via_qr(_display_qr)
        print("✅ 重新登录成功！", flush=True)
        return creds
    except Exception as e:
        print(f"❌ 重新登录失败: {e}，将在 10 秒后重试", flush=True)
        raise


def cmd_send(args) -> int:
    if args.verbose:
        set_verbose(True)

    if not args.text and not args.file:
        print("❌ 请至少指定 --text 或 --file", file=sys.stderr)
        return 1

    try:
        creds = _get_creds(args.relogin)
    except Exception:
        return 1

    token = creds["token"]
    to = args.to

    ctx_tokens = load_context_tokens()
    ctx = ctx_tokens.get(to, "")
    if not ctx:
        print(f"⚠️ 未找到与 {to} 的对话上下文，请先通过微信向机器人发送一条消息", file=sys.stderr)
        return 1

    ok = True
    if args.text:
        ok = send_message(token, to, args.text, ctx)
        if ok:
            print(f"✅ 文本消息已发送 → {args.to}")
        else:
            print(f"❌ 文本消息发送失败")

    if args.file:
        ok_file = send_file(token, to, args.file, ctx)
        if ok_file:
            print(f"✅ 文件已发送 → {args.to}")
        else:
            print(f"❌ 文件发送失败")
        ok = ok and ok_file

    return 0 if ok else 1


def cmd_run(args) -> int:
    if args.verbose:
        set_verbose(True)

    # Ensure opencode serve is running
    # print("🚀 启动 OpenCode 服务...", flush=True)
    try:
        ensure_running(timeout=30)
        h = health()
        print(f"✅ OpenCode 服务启动成功 (v{h.get('version', '?')})", flush=True)
    except Exception as e:
        print(f"❌ OpenCode 服务启动失败: {e}", flush=True)
        print("   请确保已安装: npm install -g opencode-ai", flush=True)
        return 1

    try:
        creds = _get_creds(args.relogin)
    except Exception:
        return 1

    print("✅ 微信登录成功")
    print(f"✅ 桥已启动 (现在是 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())})\n")

    buf = ""
    pending = {}
    bot_id = creds.get("account_id", "")
    last_heartbeat = time.time()
    first_heartbeat = time.time()

    try:
        while True:
            try:
                result = get_updates(creds["token"], buf, bot_id)
            except TokenExpiredError:
                try:
                    creds = _relogin()
                    buf = ""
                except Exception:
                    time.sleep(10)
                continue
            except Exception as e:
                print(f"⚠️ poll 异常: {e}", flush=True)
                time.sleep(3)
                continue

            buf = result["get_updates_buf"]
            msgs = result.get("msgs", [])

            if msgs:
                print(f"   poll: {len(msgs)} 条消息", flush=True)
            else:
                if time.time() - last_heartbeat > 60*60:
                    print(f"   ♡ 桥运行中... (已运行 {int(time.time() - first_heartbeat)}s, 现在是 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())})", flush=True)
                    last_heartbeat = time.time()
                time.sleep(1)

            for msg in msgs:
                try:
                    _handle_msg(msg, creds, pending)
                except TokenExpiredError:
                    try:
                        creds = _relogin()
                        buf = ""
                    except Exception:
                        time.sleep(10)
                    break
                except Exception as e:
                    print(f"⚠️ 处理消息异常: {e}", flush=True)
                    traceback.print_exc()
                last_heartbeat = time.time()
            _clean_expired(pending, IMAGE_TTL)

    except KeyboardInterrupt:
        print("\n桥已停止")
        stop_server()
        return 0


def main() -> int:
    p = ArgumentParser(description="远程连接微信与 OpenCode")
    p.add_argument("--version", action="version", version=f"weiclawpy {__import__('weiclawpy').__version__}")
    p.set_defaults(verbose=False, relogin=False)
    sub = p.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="启动微信-OpenCode 桥接服务（服务器模式）")
    p_run.add_argument("--verbose", "-v", action="store_true",
                       help="打印详细调试日志")
    p_run.add_argument("--relogin", action="store_true",
                       help="强制重新扫码登录")

    p_send = sub.add_parser("send", help="向微信用户发送消息")
    p_send.add_argument("--to", required=True,
                        help="接收者 user_id")
    p_send.add_argument("--text", default=None,
                        help="要发送的文本消息")
    p_send.add_argument("--file", default=None,
                        help="要发送的文件路径（支持 PDF 等）")
    p_send.add_argument("--verbose", "-v", action="store_true",
                        help="打印详细调试日志")
    p_send.add_argument("--relogin", action="store_true",
                        help="强制重新扫码登录")

    args = p.parse_args()

    if args.command == "send":
        return cmd_send(args)
    else:
        return cmd_run(args)


def _display_qr(qr_url: str) -> None:
    try:
        import qrcode as qr_mod
        qr = qr_mod.QRCode(border=1)
        qr.add_data(qr_url)
        qr.print_ascii()
    except Exception:
        print(f"扫码链接: {qr_url}")


def _handle_msg(msg: dict, creds: dict, pending: dict) -> None:
    user = msg.get("from_user_id", "")
    if not user:
        print(f"   [DEBUG] 消息缺少 from_user_id: {json.dumps(msg, ensure_ascii=False)[:200]}", flush=True)
        return

    ctx = msg.get("context_token", "")
    if ctx:
        save_context_token(user, ctx)

    text = extract_text(msg)
    media = extract_media(msg)

    # Dispatch by message type
    if media and media["type"] == "image" and not text:
        _handle_image_msg(user, ctx, media, creds, pending)
        return
    if media and media["type"] == "voice":
        _handle_voice_msg(user, ctx, media, text, creds)
        return
    if media and media["type"] == "file":
        _handle_file_msg(user, ctx, media, creds)
        return
    if text:
        _handle_text_msg(user, ctx, text, creds, pending)
        return

    print(f"   [DEBUG] 未识别消息类型: text={bool(text)} media_type={media and media.get('type')}", flush=True)


def _handle_image_msg(user: str, ctx: str, media: dict, creds: dict, pending: dict) -> None:
    print(f"← [微信] {user}: [图片] (等待文字...)", flush=True)
    try:
        img = download_and_decrypt(media["encrypt_query_param"], media.get("aes_key"))
        pending[user] = {
            "base64": base64.b64encode(img).decode(),
            "timestamp": time.time(),
            "context_token": ctx,
        }
        save_dir = Path("from_wechat")
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        img_path = save_dir / f"image_{ts}_{uuid.uuid4().hex[:6]}.jpg"
        img_path.write_bytes(img)
        print(f"   💾 已保存: {img_path.resolve()}", flush=True)
    except Exception as e:
        print(f"   图片下载失败: {e}", flush=True)


def _handle_text_msg(user: str, ctx: str, text: str, creds: dict, pending: dict) -> None:
    cmd_reply = handle_cmd(text, user)
    if cmd_reply is not None:
        print(f"→ [opencode cmd] {cmd_reply}\n", flush=True)
        send_message(creds["token"], user, cmd_reply, ctx)
        return

    cached = pending.pop(user, None)
    log_extra = None
    if cached and (time.time() - cached["timestamp"]) < IMAGE_TTL:
        print(f"← [微信] {user}: [图片+文字] {text[:80]}", flush=True)
        log_extra = {"type": "image", "base64": cached.get("base64", "")}
    else:
        print(f"← [微信] {user}: {text[:80]}{'...' if len(text) > 80 else ''}", flush=True)

    _reply_to_user(user, text, creds["token"], ctx, log_extra)


def _handle_voice_msg(user: str, ctx: str, media: dict, text: str, creds: dict) -> None:
    voice_text = media.get("voice_text") or text
    if not voice_text:
        send_message(creds["token"], user, "⚠️ 语音无法识别，请发文字", ctx)
        return

    print(f"← [微信] {user}: [语音] {voice_text[:80]}", flush=True)
    try:
        reply, _ = _ask_opencode(user, voice_text, creds["token"], ctx, {"type": "voice"})
        print(f"→ [opencode] {reply[:80]}\n", flush=True)
        if not send_message(creds["token"], user, reply, ctx):
            print(f"   消息发送失败", flush=True)
    except TokenExpiredError:
        raise
    except Exception as e:
        print(f"   opencode 错误: {e}", flush=True)


def _handle_file_msg(user: str, ctx: str, media: dict, creds: dict) -> None:
    file_name = media.get("file_name", "file.bin")
    print(f"← [微信] {user}: [文件] {file_name}", flush=True)

    save_dir = Path("from_wechat")
    try:
        file_data = download_and_decrypt(media["encrypt_query_param"], media.get("aes_key"))
        save_dir.mkdir(parents=True, exist_ok=True)
        saved_path = save_dir / file_name
        saved_path.write_bytes(file_data)
        print(f"   💾 已保存: {saved_path.resolve()}", flush=True)
        send_message(creds["token"], user, f"💾 文件已保存到 {saved_path.resolve()}", ctx)
    except TokenExpiredError:
        raise
    except Exception as e:
        print(f"   文件下载/保存失败: {e}", flush=True)
        send_message(creds["token"], user, f"⚠️ 文件下载/保存失败: {e}", ctx)


def _reply_to_user(user: str, prompt: str, token: str, ctx: str,
                   log_extra: dict | None = None) -> None:
    try:
        reply, image_urls = _ask_opencode(user, prompt, token, ctx, log_extra)
    except Exception as e:
        print(f"   opencode API 错误: {e}", flush=True)
        send_message(token, user, f"⚠️ {e}", ctx)
        return

    shown = reply[:80] + ("..." if len(reply) > 80 else "")
    print(f"→ [opencode] {shown}\n", flush=True)

    if not reply and not image_urls:
        send_message(token, user, "(无回复)", ctx)
        return

    if reply:
        img_match = re.search(r"!\[.*?\]\((https?://\S+)\)", reply)
        if img_match:
            text_part = re.sub(r"!\[.*?\]\(https?://\S+\)", "", reply).strip()
            if text_part:
                send_message(token, user, text_part, ctx)
            send_image_by_url(token, user, ctx, img_match[1])
        else:
            send_message(token, user, reply, ctx)

    for url in image_urls:
        send_image_by_url(token, user, ctx, url)


def _get_typing_ticket(token: str, user: str, ctx: str) -> str:
    cached = _TYPING_CACHE.get(user)
    if cached and time.time() - cached["ts"] < _TYPING_CACHE_TTL:
        return cached["ticket"]
    try:
        cfg = get_weixin_config(token, user, ctx)
        ticket = cfg.get("typing_ticket", "") if cfg else ""
        if ticket:
            _TYPING_CACHE[user] = {"ticket": ticket, "ts": time.time()}
        return ticket
    except Exception:
        return ""


def _ask_opencode(user: str, prompt: str, token: str, ctx: str,
                  extra: dict | None = None) -> tuple[str, list[str]]:
    ticket = _get_typing_ticket(token, user, ctx) if token and ctx else ""
    if ticket:
        try:
            send_typing(token, user, ticket, 1)
        except Exception:
            pass

    try:
        session_id = ensure_session(user)
        model = get_pref(user, "model")
        agent = get_pref(user, "agent")
        resp = api_send_message(session_id, prompt, model=model, agent=agent)
        log_response(user, prompt, resp, extra=extra)
        return extract_reply_text(resp), extract_reply_images(resp)
    finally:
        if ticket:
            try:
                send_typing(token, user, ticket, 2)
            except Exception:
                pass


def _clean_expired(pending: dict, ttl: float) -> None:
    now = time.time()
    expired = [u for u, p in pending.items() if now - p["timestamp"] > ttl]
    for u in expired:
        del pending[u]


if __name__ == "__main__":
    sys.exit(main())
