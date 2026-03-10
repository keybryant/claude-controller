"""
模拟后端 WebSocket 服务 - 仅用于本地联调
新协议：
  发送命令 → 收到 ACK（含 task_id） → 持续收到进度事件 → 收到 done/error 事件
用法: python mock_server.py
"""
import asyncio
import json
import logging

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mock_server")

COMMANDS = [
    {
        "msg_id": "msg-001",
        "data": {
            "command": "project_init",
            "project_code": "demo_project",
            "remote_url": "https://github.com/octocat/Hello-World.git",
        },
    },
    {
        "msg_id": "msg-002",
        "data": {
            "command": "requirement_init",
            "project_code": "demo_project",
            "branch": "feature/my-task-001",
            "base_branch": "master",
        },
    },
    # 多轮对话：第1轮
    {
        "msg_id": "msg-003",
        "data": {
            "command": "write_prompt",
            "project_code": "demo_project",
            "branch": "feature/my-task-001",
            "prompt": "请列出当前目录的文件结构，并简单描述这个项目的用途。",
        },
    },
    # 多轮对话：第2轮（续上下文）
    {
        "msg_id": "msg-004",
        "data": {
            "command": "write_prompt",
            "project_code": "demo_project",
            "branch": "feature/my-task-001",
            "prompt": "根据上面的文件结构，帮我在 README.md 里补充一个 Features 章节。",
        },
    },
]


async def handler(ws):
    logger.info("客户端已连接: %s", ws.remote_address)

    # 等待握手
    raw = await ws.recv()
    logger.info("握手: %s", json.loads(raw))

    # 跟踪待完成的 write_prompt 任务：task_id → msg_id
    pending_tasks: dict[str, str] = {}

    async def send_cmd(cmd: dict) -> dict:
        """发送命令并等待 ACK"""
        await ws.send(json.dumps(cmd, ensure_ascii=False))
        raw = await ws.recv()
        return json.loads(raw)

    async def wait_task_done(task_id: str) -> None:
        """等待指定 task_id 的 done/error 事件"""
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") != "event":
                logger.warning("非事件消息（忽略）: %s", msg)
                continue

            event = msg.get("event")
            tid = msg.get("task_id", "")[:8]

            if event == "progress":
                logger.info("  [进度 %s] %s", tid, msg.get("data", {}).get("line", ""))
            elif event == "started":
                logger.info("  [开始 %s] Claude 开始执行", tid)
            elif event == "done":
                output = msg.get("data", {}).get("output", "")
                logger.info("  [完成 %s] 输出 %d 字符:\n%s", tid, len(output), output[:300])
                return
            elif event == "error":
                logger.error("  [错误 %s] %s", tid, msg.get("data", {}).get("message"))
                return

    try:
        for cmd in COMMANDS:
            await asyncio.sleep(1)
            logger.info("─" * 50)
            logger.info("发送: %s", cmd["msg_id"])

            ack = await send_cmd(cmd)
            logger.info(
                "ACK [%s]: status=%s, message=%s",
                ack.get("msg_id"), ack.get("status"), ack.get("message"),
            )

            # write_prompt 返回 task_id，需等待事件流完成再发下一条
            if ack.get("status") == "ok" and ack.get("data", {}).get("task_id"):
                task_id = ack["data"]["task_id"]
                logger.info("等待任务完成: %s", task_id)
                await wait_task_done(task_id)

        logger.info("═" * 50)
        logger.info("所有命令执行完毕")
        await asyncio.sleep(3)

    except websockets.exceptions.ConnectionClosed:
        logger.info("客户端断开")


async def main():
    stop = asyncio.get_event_loop().create_future()

    async def _handler(ws):
        await handler(ws)
        if not stop.done():
            stop.set_result(None)

    logger.info("模拟后端启动: ws://localhost:8080/ws")
    async with websockets.serve(_handler, "localhost", 8080):
        await stop
        logger.info("测试完成，服务器退出")


if __name__ == "__main__":
    asyncio.run(main())
