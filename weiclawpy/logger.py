"""Log OpenCode conversation details to markdown files."""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_DIR = Path(os.environ.get("WEICLAWPY_DIR", Path.home() / ".weiclawpy"))
LOG_DIR = STATE_DIR / "logs"

_TZ_SHANGHAI = timezone(timedelta(hours=8))


def _log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(_TZ_SHANGHAI).strftime("%Y-%m-%d")
    return LOG_DIR / f"opencode-{today}.md"


def _ts() -> str:
    return datetime.now(_TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _escape_md(s: str) -> str:
    return s.replace("\\", "\\\\").replace("`", "\\`")


def _format_tool(tool: dict) -> str:
    name = tool.get("tool", "unknown")
    state = tool.get("state", {})
    status = state.get("status", "?")
    title = state.get("title", "")
    lines = [f"**{name}** ({status})" + (f" — {title}" if title else "")]
    input_data = state.get("input", {})
    if input_data:
        lines.append(f"  输入: `{_escape_md(str(input_data))[:500]}`")
    output = state.get("output", "")
    if output:
        lines.append(f"  输出: `{_escape_md(output)[:1000]}`")
    error = state.get("error", "")
    if error:
        lines.append(f"  错误: `{_escape_md(error)[:500]}`")
    attachments = state.get("attachments", [])
    if attachments:
        for a in attachments:
            url = a.get("url", "")
            if url:
                lines.append(f"  附件: ![]({url})")
    return "\n".join(lines)


def log_response(user_id: str, user_message: str, response: dict,
                 extra: dict | None = None) -> None:
    """Log a full conversation turn (user message + opencode response)."""

    parts = response.get("parts", [])
    if not parts:
        return

    t = _ts()
    lines = [
        f"## {t} | 用户: `{user_id}`",
        "",
    ]

    extra_type = extra.get("type", "") if extra else ""
    if extra_type == "image":
        prefix = f"**用户消息**: `{user_message}`\n**[附带图片]** (base64 长度: {len(extra.get('base64', ''))})"
    elif extra_type == "voice":
        prefix = f"**[语音转文字]**: `{user_message}`"
    elif extra_type == "file":
        prefix = f"**[文件]**: `{extra.get('file_name', '')}`\n**用户消息**: `{user_message}`"
    elif extra_type == "command":
        prefix = f"**[命令]**: `{extra.get('cmd', '')}`\n**参数**: `{user_message}`"
    else:
        prefix = f"**用户消息**: `{user_message}`"
    lines.extend([prefix, ""])

    reasoning_parts = [p for p in parts if p.get("type") == "reasoning"]
    tool_parts = [p for p in parts if p.get("type") == "tool"]
    text_parts = [p for p in parts if p.get("type") == "text"]
    file_parts = [p for p in parts if p.get("type") == "file"]
    step_finish_parts = [p for p in parts if p.get("type") == "step-finish"]
    patch_parts = [p for p in parts if p.get("type") == "patch"]

    if reasoning_parts:
        lines.append("### 思考过程")
        lines.append("")
        for p in reasoning_parts:
            text = p.get("text", "")
            lines.extend([
                "<details>",
                "<summary>思考 (展开查看)</summary>",
                "",
                "```",
                text,
                "```",
                "</details>",
                "",
            ])

    if tool_parts:
        lines.append("### 工具调用")
        lines.append("")
        for p in tool_parts:
            lines.append(_format_tool(p))
            lines.append("")

    if patch_parts:
        lines.append("### 文件修改")
        lines.append("")
        for p in patch_parts:
            files = p.get("files", [])
            for f in files:
                lines.append(f"- `{_escape_md(f)}`")
            hash_val = p.get("hash", "")
            if hash_val:
                lines.append(f"  (diff hash: `{hash_val[:16]}`)")
        lines.append("")

    if text_parts:
        lines.append("### AI 回复")
        lines.append("")
        for p in text_parts:
            text = p.get("text", "")
            lines.append(text)
            lines.append("")

    if file_parts:
        lines.append("### 附件")
        lines.append("")
        for p in file_parts:
            mime = p.get("mime", "")
            url = p.get("url", "")
            filename = p.get("filename", "")
            label = filename or url or mime
            if mime.startswith("image/"):
                lines.append(f"![{label}]({url})")
            else:
                lines.append(f"- [{label}]({url})")
        lines.append("")

    if step_finish_parts:
        lines.append("### 统计")
        lines.append("")
        for p in step_finish_parts:
            reason = p.get("reason", "")
            cost = p.get("cost", 0)
            tokens = p.get("tokens", {})
            lines.append(f"- 结束原因: {reason}")
            lines.append(f"- 费用: ${cost:.6f}" if cost else "- 费用: N/A")
            lines.append(f"- 输入 tokens: {tokens.get('input', 0)}")
            lines.append(f"- 输出 tokens: {tokens.get('output', 0)}")
            reasoning_t = tokens.get("reasoning", 0)
            if reasoning_t:
                lines.append(f"- 推理 tokens: {reasoning_t}")
            cache = tokens.get("cache", {})
            if cache:
                lines.append(f"- 缓存读取: {cache.get('read', 0)}, 写入: {cache.get('write', 0)}")
        lines.append("")

    lines.append("---")
    lines.append("")

    try:
        path = _log_path()
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
