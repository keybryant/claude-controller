"""
Claude Code 控制器 - 主入口
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path

from config import Config
from handler import CommandHandler
from ws_client import WebSocketClient


def setup_logging() -> None:
    level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


async def main() -> None:
    setup_logging()
    logger = logging.getLogger("main")

    # 确保项目根目录存在
    base_dir = Path(Config.BASE_PROJECTS_DIR)
    base_dir.mkdir(parents=True, exist_ok=True)
    logger.info("项目根目录: %s", base_dir)
    logger.info("后端 WebSocket: %s", Config.WS_URL)
    logger.info("Claude 命令: %s", Config.CLAUDE_CMD)

    handler = CommandHandler()
    client = WebSocketClient(handler)

    loop = asyncio.get_running_loop()

    # 优雅退出：捕获 SIGINT / SIGTERM
    def _shutdown(sig_name: str) -> None:
        logger.info("收到信号 %s，正在退出...", sig_name)
        client.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig.name)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
