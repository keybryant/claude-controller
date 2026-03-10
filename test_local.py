"""
本地测试脚本 - 无需启动 WebSocket 后端，直接调用命令处理器
用法: python test_local.py
"""
import asyncio
import json
import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

sys.path.insert(0, ".")
from handler import CommandHandler

handler = CommandHandler()


async def send(payload: dict) -> None:
    print("\n" + "=" * 60)
    print(f">>> 命令: {json.dumps(payload, ensure_ascii=False, indent=2)}")
    result = await handler.handle(payload)
    print(f"<<< 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")


async def main():
    # 1. 项目初始化（需要真实可访问的 Git 仓库）
    await send({
        "command": "project_init",
        "project_code": "demo_project",
        "remote_url": "https://github.com/octocat/Hello-World.git",
    })

    # 2. 需求初始化（切换 / 创建分支）
    await send({
        "command": "requirement_init",
        "project_code": "demo_project",
        "branch": "feature/my-task-001",
        "base_branch": "master",  # Hello-World 默认分支是 master
    })

    # 3. 写入提示词执行
    await send({
        "command": "write_prompt",
        "project_code": "demo_project",
        "branch": "feature/my-task-001",
        "prompt": "请列出当前目录的文件结构，并简单描述这个项目的用途。",
    })

    # 4. 查看所有会话
    await send({"command": "list_sessions"})


if __name__ == "__main__":
    asyncio.run(main())
