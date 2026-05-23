"""Command router — handles / commands from WeChat messages using the OpenCode server API."""

import json
import subprocess

from . import opencode_api as api
from .opencode_api import find_model
from . import session_manager as sm
from .logger import log_response

BUILTIN_COMMANDS = {
    "/help": "显示此帮助",
    "/new [内容]": "开启新对话（不带上下文）",
    "/sessions": "列出所有 OpenCode 会话",
    "/models": "列出可用模型",
    "/model <名称>": "切换当前会话使用的模型",
    "/agents": "列出可用 Agent",
    "/agent <名称>": "切换当前会话使用的 Agent",
    "/abort": "中止当前正在生成的回复",
    "/clear": "清空当前对话上下文（等效 /new）",
    "/config": "查看当前配置",
    "/shell <命令>": "运行 Windows 命令行命令",
}


def _fmt_help(wx_user_id: str) -> str:
    lines = ["**支持的命令：**"]
    for cmd, desc in BUILTIN_COMMANDS.items():
        lines.append(f"  • `{cmd}` — {desc}")
    lines.append("")
    lines.append(f"当前模型：`{sm.get_pref(wx_user_id, 'model', '默认')}`")
    lines.append(f"当前 Agent：`{sm.get_pref(wx_user_id, 'agent', '默认')}`")
    return "\n".join(lines)


def handle_help(wx_user_id: str, arg: str) -> str:
    return _fmt_help(wx_user_id)


# def handle_new_session(wx_user_id: str, arg: str) -> str:
#     content = arg.strip() or "开始新对话"
#     session_id = sm.new_session(wx_user_id)
#     model = sm.get_pref(wx_user_id, "model")
#     agent = sm.get_pref(wx_user_id, "agent")
#     try:
#         resp = api.send_message(session_id, content, model=model, agent=agent)
#         log_response(wx_user_id, content, resp,
#                      extra={"type": "command", "cmd": "/new"})
#         return api.extract_reply_text(resp) or "(无回复)"
#     except Exception as e:
#         return f"⚠️ {e}"


def handle_new_session(wx_user_id: str, arg: str) -> str:
    session_id = sm.new_session(wx_user_id)
    return f"✅ 已开启新对话 (session: {session_id})"


def handle_sessions(wx_user_id: str, arg: str) -> str:
    try:
        sessions = api.list_sessions()
        if not sessions:
            return "📭 当前没有会话"
        lines = [f"**OpenCode 会话（共 {len(sessions)} 个）：**"]
        for s in sessions[:20]:
            sid = s.get("id", "?")
            title = s.get("title", "") or s.get("id", "")
            lines.append(f"  • `{sid}` — {title}")
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ 获取会话列表失败: {e}"


def handle_models(wx_user_id: str, arg: str) -> str:
    try:
        data = api.get_providers()
        providers = data.get("providers", data if isinstance(data, list) else [])
        if not providers:
            return "🤖 没有可用模型"
        lines = ["🤖 **可用模型：**"]
        for p in providers:
            if not isinstance(p, dict):
                continue
            pid = p.get("id", "?")
            for m in p.get("models", []):
                mid = m.get("id") if isinstance(m, dict) else str(m)
                lines.append(f"  • `{pid}/{mid}`")
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ 获取模型列表失败: {e}"


def handle_set_model(wx_user_id: str, arg: str) -> str:
    model = arg.strip()
    if not model:
        return "❌ 请指定模型名称，格式: `provider/model`\n例如: `/model anthropic/claude-sonnet-4-20250514`\n发送 `/models` 查看可用模型。"

    pid, mid, display = find_model(model)
    if pid and mid:
        sm.set_pref(wx_user_id, "model", f"{pid}/{mid}")
        return f"✅ 模型已切换为 `{display}`"

    if "/" in model:
        return f"❌ 未找到模型 `{model}`\n请发送 `/models` 查看可用模型列表。"
    else:
        return f"❌ 未找到模型 `{model}`\n提示: 请使用 `provider/model` 格式\n例如: `/model anthropic/claude-sonnet-4-20250514`\n发送 `/models` 查看可用模型。"


def handle_agents(wx_user_id: str, arg: str) -> str:
    try:
        agents = api.list_agents()
        if not agents:
            return "👤 没有可用 Agent"
        lines = [f"👤 **可用 Agent（共 {len(agents)} 个）：**"]
        for i, a in enumerate(agents, 1):
            if isinstance(a, dict):
                name = a.get("name") or a.get("id", f"Agent {i}")
                desc = a.get("description", "")
                lines.append(f"  {i}. **{name}**{f' — {desc}' if desc else ''}")
            else:
                lines.append(f"  {i}. {a}")
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ 获取 Agent 列表失败: {e}"


def handle_set_agent(wx_user_id: str, arg: str) -> str:
    agent = arg.strip()
    if not agent:
        return "❌ 请指定 Agent 名称，例如 `/agent coding`"
    sm.set_pref(wx_user_id, "agent", agent)
    return f"✅ Agent 已切换为 `{agent}`"


def handle_abort(wx_user_id: str, arg: str) -> str:
    try:
        session_id = sm.get_pref(wx_user_id, "session_id", "")
        if not session_id:
            return "⚠️ 没有活跃会话"
        ok = api.abort_session(session_id)
        return "✅ 已中止回复" if ok else "⚠️ 中止失败"
    except Exception as e:
        return f"⚠️ {e}"


def handle_config(wx_user_id: str, arg: str) -> str:
    try:
        cfg = api.get_config()
        return f"```\n{json.dumps(cfg, indent=2, ensure_ascii=False)[:5000]}\n```"
    except Exception as e:
        return f"⚠️ 获取配置失败: {e}"


def handle_shell(wx_user_id: str, arg: str) -> str:
    cmd = arg.strip()
    if not cmd:
        return "❌ 用法：`/shell <命令>`\n例如：`/shell dir` 或 `/shell python --version`"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=60, shell=True,
        )
        out = proc.stdout.strip() or proc.stderr.strip() or "(无输出)"
        if len(out) > 5000:
            out = out[:5000] + f"\n... (已截断，共 {len(out)} 字符)"
        return f"```\n{out}\n```"
    except subprocess.TimeoutExpired:
        return "⚠️ 命令执行超时 (60s)"
    except Exception as e:
        return f"⚠️ 命令执行失败: {e}"


COMMAND_ROUTER = {
    "/help": handle_help,
    "/new": handle_new_session,
    "/sessions": handle_sessions,
    "/models": handle_models,
    "/model": handle_set_model,
    "/agents": handle_agents,
    "/agent": handle_set_agent,
    "/abort": handle_abort,
    "/config": handle_config,
    "/shell": handle_shell,
}


def handle_text(text: str, wx_user_id: str) -> str | None:
    """Try to handle a / command. Returns reply text, or None if not a command."""
    text = text.strip()
    if not text.startswith("/"):
        return None

    cmd = text.split()[0].lower()
    arg = text[len(cmd):].strip()
    handler = COMMAND_ROUTER.get(cmd)
    if not handler:
        return f"❌ 未知命令: `{cmd}`\n\n发送 `/help` 查看可用命令。"
    try:
        return handler(wx_user_id, arg)
    except Exception as e:
        return f"⚠️ 命令 `{cmd}` 执行出错: {e}"
