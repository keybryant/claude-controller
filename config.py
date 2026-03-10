import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # WebSocket 后端地址
    WS_URL: str = os.getenv("WS_URL", "ws://43.139.194.139/aiProject/ws/ai-tool")
    # 断线后重连间隔（秒）
    WS_RECONNECT_INTERVAL: int = int(os.getenv("WS_RECONNECT_INTERVAL", "5"))
    # 心跳间隔（秒），0 表示关闭
    WS_PING_INTERVAL: int = int(os.getenv("WS_PING_INTERVAL", "30"))

    # 本地项目根目录，所有项目文件夹都创建在此处
    BASE_PROJECTS_DIR: str = os.getenv(
        "BASE_PROJECTS_DIR", os.path.expanduser("~/projects")
    )

    # Claude Code 可执行命令
    CLAUDE_CMD: str = os.getenv("CLAUDE_CMD", "claude")
    # Claude 单次任务超时（秒）
    CLAUDE_TIMEOUT: int = int(os.getenv("CLAUDE_TIMEOUT", "600"))

    # 默认基准分支，需求分支从此拉取
    DEFAULT_BASE_BRANCH: str = os.getenv("DEFAULT_BASE_BRANCH", "develop")

    # 日志级别
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # 控制器标识，上报给后端
    CLIENT_ID: str = os.getenv("CLIENT_ID", "claude-controller-1")

    # WebSocket 连接身份标识与认证密钥
    WS_NAME: str = os.getenv("WS_NAME", "VICTOR")
    WS_API_KEY: str = os.getenv("WS_API_KEY", "ak_QcDy2V1Pd88douPu5amLDcM8A4PCrFeC")
