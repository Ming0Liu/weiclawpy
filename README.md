# weiclawpy

<p>
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

在微信与 OpenCode 之间建立双向连接，支持消息互通与文件互传。

**weiclawpy** 是一个 Python CLI 桥接工具，连接微信（ilinkai 机器人 API）与 OpenCode AI（终端 AI 编码助手）。微信用户可通过自然语言与 OpenCode 交互：发送文本、图片、语音消息或文件，并接收 OpenCode 的文字回复或文件。它不仅将微信变为 OpenCode 的对话前端，还实现了双向文件传输。

---

## 目录

- [快速开始](#快速开始)
- [CLI 用法](#cli-用法)
- [架构概览](#架构概览)
- [模块详解](#模块详解)
- [数据持久化](#数据持久化)
- [环境变量](#环境变量)
- [FAQ](#faq)

---

## 快速开始

### 前置条件

```bash
# Python >= 3.9
# 安装 OpenCode（npm 全局包）
npm install -g opencode-ai

# 安装 weiclawpy
pip install weiclawpy
```

### 启动桥接服务

```bash
weiclawpy run
```

首次运行时，终端会打印微信二维码，扫码登录后即开始自动轮询消息。后续运行会自动复用凭证，无需重复扫码。**运行中若 token 过期，会自动弹出二维码重新登录，无需重启服务。**

---

## CLI 用法

```
weiclawpy
├── weiclawpy run [选项]      (默认命令) 启动长驻桥接服务
│   ├── --verbose, -v         打印详细 HTTP 调试日志
│   └── --relogin             强制重新扫码登录
│
└── weiclawpy send            一次性消息发送工具
    ├── --to <user_id>        接收者微信 user_id（必填）
    ├── --text <文本>          发送文本消息
    ├── --file <路径>          发送文件（支持 PDF 等）
    ├── --verbose, -v
    └── --relogin
```

### weiclawpy run — 主服务模式

启动后会依次执行：

1. 自动启动 `opencode serve` 子进程（监听 `127.0.0.1:4096`）
2. 加载微信凭证或弹出二维码登录
3. 进入无限消息轮询循环

主循环中，每小时会在终端打印一次心跳 `桥运行中...`。按 `Ctrl+C` 即可停止运行。

### weiclawpy send — 一次性发送

用于从命令行主动向微信用户发送消息。需要接收者的 `user_id`（对方必须先通过微信向机器人发过消息，才能获取上下文令牌）。

```bash
# 发送文本
weiclawpy send --to <user_id> --text "你好"

# 发送文件
weiclawpy send --to <user_id> --file report.pdf
```

---

## 架构概览

| 模块 | 职责 |
|---|---|
| `__main__.py` | CLI 入口 + 主循环编排器 |
| `weixin.py` | 微信 ilinkai API 客户端 |
| `opencode_api.py` | OpenCode HTTP REST 客户端 |
| `opencode_cmd.py` | 微信斜杠命令路由器 |
| `opencode_serve.py` | OpenCode 子进程管理器 |
| `session_manager.py` | 微信用户 ↔ OpenCode 会话映射 |
| `cdn.py` | 微信 CDN AES-128-ECB 加解密 |
| `logger.py` | Markdown 对话日志 |

### 消息处理流程

```
微信用户发送消息
        │
        ▼
__main__.py 轮询循环 (get_updates, 35s long-poll)
        │
        ▼
_handle_msg() 按消息类型分发
        │
        ├── 图片 ────────→ 下载解密 → 暂存 pending，等待关联文字
        │
        ├── 语音 ────────→ 提取转文字 → 发给 OpenCode → 回复
        │
        ├── 文件 ────────→ 下载解密 → 保存到 from_wechat/
        │
        └── 文字 ────────→ 以 / 开头 → opencode_cmd 命令路由
                │
                └── 普通文字 → _ask_opencode()
                        ├── 获取/创建 OpenCode 会话
                        ├── 读取用户的 model/agent 偏好
                        ├── 发送"正在输入"状态到微信
                        ├── opencode_api.send_message() → AI 回复
                        ├── logger.log_response() 记录日志
                        └── 回复拆分为文本/图片，发回微信
```

---

## 模块详解

### `__main__.py` — 核心编排器

这是应用的**大脑**，负责编排所有其他模块。

**关键设计——图片缓冲机制：**

微信将图片和文字作为**两条独立消息**发送（图片先到，文字后到）。`pending` 字典（位于内存，5 分钟 TTL）用于暂存图片 base64，待文字到来后组合为"图片+文字"一起发给 OpenCode。若 5 分钟内无文字到来，图片自动丢弃。

### `weixin.py` — 微信 API 客户端

对接微信 ilinkai 开放平台，基础地址 `https://ilinkai.weixin.qq.com`，机器人类型 `"3"`。

| 功能 | 说明 |
|---|---|
| 二维码登录 | 获取二维码 → 轮询扫描/确认/过期状态，得到 bot_token |
| 消息轮询 | 35 秒 long-poll，每次返回 get_updates_buf 做游标 |
| 发送消息 | 支持文本(type=1)、图片(type=2)、文件(type=4) |
| 文件上传 | 本地文件 AES-128-ECB 加密后上传微信 CDN，再发送媒体引用 |
| 输入状态 | send_typing(status=1 开始输入, status=2 停止输入) |
| 上下文令牌 | 多轮对话需要 context_token，该令牌自动保存到本地文件 |

### `opencode_api.py` — OpenCode HTTP 客户端

对 `opencode serve` 的 HTTP API（`http://127.0.0.1:4096`）的完整封装。

| 端点 | 用途 |
|---|---|
| `GET /global/health` | 健康检查 |
| `POST /session` | 创建会话 |
| `POST /session/{id}/message` | 发送消息（超时 300 秒） |
| `POST /session/{id}/command` | 执行斜杠命令 |
| `GET /config/providers` | 获取可用模型列表 |
| `GET /agent` | 获取可用 Agent 列表 |

**模型解析：** 支持 `"provider/model"` 格式（如 `"anthropic/claude-sonnet-4-20250514"`），也支持纯 model ID 自动查找 provider。

### `opencode_cmd.py` — 命令路由器

在微信内通过斜杠命令控制 OpenCode 的行为。

| 命令 | 功能 |
|---|---|
| `/help` | 显示帮助 + 当前模型/agent |
| `/new` | 开启新对话（创建新会话） |
| `/sessions` | 列出所有 OpenCode 会话 |
| `/models` | 列出所有可用模型 |
| `/model <名称>` | 切换模型（如 `anthropic/claude-sonnet-4-20250514`） |
| `/agents` | 列出所有可用 Agent |
| `/agent <名称>` | 切换当前 Agent |
| `/abort` | 中止当前 AI 生成 |
| `/config` | 查看 OpenCode 配置 |
| `/shell <命令>` | 在服务器上运行 Windows 命令（60 秒超时） |
| `/clear` | 清空对话（同 /new） |

### `opencode_serve.py` — 子进程管理

管理 `opencode serve` 进程的完整生命周期：
- 查找 `opencode` 可执行文件（PATH → npm 全局目录）
- 启动后轮询 `/global/health` 等待就绪
- 停止时先 SIGTERM，3 秒未响应则 SIGKILL
- 支持通过 `OPENCODE_SERVER_PASSWORD` 环境变量配置 Basic 认证

### `session_manager.py` — 用户会话管理

将微信用户 ID 映射到 OpenCode 会话 ID，持久化在 `~/.weiclawpy/user_sessions.json`。
- 线程安全读写（`threading.Lock` + 临时文件 + `os.replace()`）
- `ensure_session()`：检查会话存活，存活则复用，否则创建新会话
- 存储每个用户的模型偏好、agent 偏好

### `cdn.py` — CDN 加解密

微信多媒体文件的 AES-128-ECB 加解密处理。

### `logger.py` — 对话日志

将每次 AI 交互记录为 Markdown 文件，`~/.weiclawpy/logs/opencode-YYYY-MM-DD.md`，每日轮换。包含：

```
思考过程（details 折叠）
工具调用（输入/输出/错误）
文件修改（修改的文件路径列表）
AI 回复（纯文本）
附件（图片/文件 URL）
统计（费用、tokens、缓存命中率）
```

---

## 数据持久化

| 文件 | 用途 |
|---|---|
| `~/.weiclawpy/credentials.json` | 微信 API token、user_id、account_id |
| `~/.weiclawpy/context_tokens.json` | 每个用户的微信多轮对话上下文令牌 |
| `~/.weiclawpy/user_sessions.json` | 每个用户的 OpenCode 会话 ID、模型/agent 偏好 |
| `~/.weiclawpy/logs/opencode-YYYY-MM-DD.md` | 每日轮换的详细对话日志 |
| `./from_wechat/`（工作目录下） | 从微信下载的图片和文件 |

---

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `WEICLAWPY_DIR` | 覆盖数据存储目录 | `~/.weiclawpy` |
| `OPENCODE_SERVER_PASSWORD` | OpenCode 服务 Basic 认证密码 | （无认证） |
| `OPENCODE_SERVER_USERNAME` | OpenCode 服务 Basic 认证用户名 | `opencode` |

---

## FAQ

### 支持的微信消息类型？

文本、图片、语音（自动转文字）、文件。

### 如何切换 AI 模型？

在微信中发送 `/models` 查看可用模型列表，然后发送 `/model provider/model_name` 切换。例如：

```
/model anthropic/claude-sonnet-4-20250514
```

### 数据存储在哪里？

默认在 `~/.weiclawpy/`，可通过 `WEICLAWPY_DIR` 环境变量更改。

### 需要公网服务器吗？

不需要。weiclawpy 连接微信 ilinkai API（公网）和本地运行的 `opencode serve`，可在个人电脑上运行。

### 微信凭证过期怎么办？

**服务运行中过期：** weiclawpy 会自动检测 token 过期（HTTP 401/403），清除过期凭证并在终端重新展示二维码，扫码后自动恢复，无需手动干预。

**启动时强制重登：** 运行 `weiclawpy run --relogin` 可跳过已有凭证，强制重新扫码登录。

---

## License

[MIT](LICENSE) © 2025 weiclawpy
