# Claude Controller

基于 WebSocket 的 Claude Code 控制器：连接后端服务，接收命令并驱动本地 Claude CLI 执行任务（项目初始化、需求分支、写 prompt 等），通过事件队列异步推送执行进度与结果。

## 功能概览

- **WebSocket 长连接**：连接后端，断线自动重连，支持心跳
- **项目与分支管理**：`project_init` 克隆仓库并初始化基准分支，`requirement_init` 为需求创建 Git worktree 并注册会话
- **Claude 任务执行**：`write_prompt` 提交 prompt 后立即返回 `task_id`，后台执行并通过事件推送进度/结果
- **会话管理**：按 `(project_code, branch)` 维护独立会话，支持 `list_sessions`、`remove_session`

## 环境要求

- Python 3.10+
- 已安装并可用 [Claude Code](https://claude.com/code) CLI（命令行 `claude`）
- Git（用于克隆、worktree、分支操作）

## 安装

```bash
# 克隆仓库后进入目录
cd claude-controller

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux / macOS

# 安装依赖
pip install -r requirements.txt
```

## 配置

在项目根目录创建 `.env` 文件，参考以下变量（必填项需根据实际后端填写）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WS_URL` | 后端 WebSocket 地址 | （必填） |
| `WS_NAME` | 连接身份标识 | `VICTOR` |
| `WS_API_KEY` | 认证密钥 | （必填，若后端需要） |
| `WS_RECONNECT_INTERVAL` | 断线重连间隔（秒） | `5` |
| `WS_PING_INTERVAL` | 心跳间隔（秒），0 为关闭 | `30` |
| `BASE_PROJECTS_DIR` | 本地项目根目录，所有项目与 worktree 创建于此 | `~/projects` |
| `CLAUDE_CMD` | Claude 可执行命令 | `claude` |
| `CLAUDE_TIMEOUT` | 单次任务超时（秒） | `600` |
| `DEFAULT_BASE_BRANCH` | 默认基准分支（如 develop） | `develop` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `CLIENT_ID` | 控制器标识，上报给后端 | `claude-controller-1` |

示例 `.env`（请勿提交真实密钥）：

```env
WS_URL=ws://your-backend-host/aiProject/ws/ai-tool
WS_NAME=controller-1
WS_API_KEY=your_api_key

BASE_PROJECTS_DIR=C:\workspace\projects
CLAUDE_CMD=claude
CLAUDE_TIMEOUT=600
DEFAULT_BASE_BRANCH=develop
LOG_LEVEL=INFO
CLIENT_ID=claude-controller-1
```

## 运行

```bash
python main.py
```

启动后会连接 `WS_URL`，并在 `BASE_PROJECTS_DIR` 下创建/管理项目。按 `Ctrl+C` 可优雅退出。

## 支持的命令（由后端下发）

| 命令 | 说明 |
|------|------|
| `project_init` | 克隆远程仓库并初始化基准分支，字段：`project_code`, `remote_url`, `base_branch?` |
| `requirement_init` | 为需求分支创建 worktree 并注册会话，字段：`project_code`, `branch`, `base_branch?` |
| `write_prompt` | 向已注册会话提交 prompt，立即返回 `task_id`，后续通过事件推送进度/结果；字段：`project_code`, `branch`, `prompt`, `auto_commit?` |
| `list_sessions` | 返回当前所有会话列表 |
| `remove_session` | 移除会话，可选删除 worktree；字段：`project_code`, `branch`, `remove_worktree?` |

`write_prompt` 的流程：控制器先返回 ACK（含 `task_id`），再在后台调用 Claude CLI 执行，并通过 WebSocket 事件推送进度与最终结果。

## 项目结构

```
claude-controller/
├── main.py          # 入口，启动 WebSocket 客户端与命令处理
├── config.py        # 配置（从 .env 读取）
├── ws_client.py     # WebSocket 连接、重连、心跳、消息收发
├── handler.py       # 命令分发与处理（project_init / requirement_init / write_prompt 等）
├── claude_runner.py # Claude 会话管理与 prompt 执行（按 project_code:branch 隔离）
├── git_ops.py       # Git 克隆、worktree、分支操作
├── mock_server.py   # 本地 mock WebSocket 服务，便于联调
├── test_local.py    # 本地测试脚本
├── requirements.txt
└── .env             # 本地配置（不提交）
```

## 本地调试

- 使用 `mock_server.py` 启动本地 WebSocket 服务，将 `.env` 中 `WS_URL` 指向该服务进行联调。
- 运行 `test_local.py` 可做简单本地验证（具体用法见脚本内说明）。

## 许可证

按项目约定使用。
