"""
WebSocket 客户端
- 接收命令 → 调用 handler → 立即返回 ACK
- 独立的 event_sender 协程持续消费 event_queue，将事件推送给后端
- 断线重连：event_queue 是实例级持久队列，断线期间堆积的事件重连后全部发出
"""
import asyncio
import json
import logging
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from config import Config
from handler import CommandHandler

logger = logging.getLogger(__name__)


class WebSocketClient:
    def __init__(self, handler: CommandHandler):
        self.url = Config.WS_URL
        self.handler = handler
        self._stop = asyncio.Event()
        self._event_queue: asyncio.Queue = asyncio.Queue()  # 全局唯一，跨连接持久

    def _build_url(self) -> str:
        params = {"name": Config.WS_NAME, "api_key": Config.WS_API_KEY}
        return f"{self.url}?{urlencode(params)}"

    async def run(self) -> None:
        logger.info("WebSocket 客户端启动，目标: %s", self.url)
        while not self._stop.is_set():
            try:
                await self._connect()
            except (ConnectionClosed, WebSocketException, OSError) as e:
                logger.warning("连接断开: %s，%d 秒后重连...", e, Config.WS_RECONNECT_INTERVAL)
            except Exception as e:
                logger.exception("未预期异常: %s，%d 秒后重连...", e, Config.WS_RECONNECT_INTERVAL)

            if not self._stop.is_set():
                await asyncio.sleep(Config.WS_RECONNECT_INTERVAL)

        logger.info("WebSocket 客户端已停止")

    async def _connect(self) -> None:
        ping_interval = Config.WS_PING_INTERVAL or None
        url = self._build_url()
        async with websockets.connect(
            url,
            ping_interval=ping_interval,
            additional_headers={"X-Client-Id": Config.CLIENT_ID},
        ) as ws:
            logger.info("已连接到后端: %s", url)
            await ws.send(json.dumps({"type": "handshake", "client_id": Config.CLIENT_ID}))

            # sender 绑定当前 ws，但共享同一个持久 queue
            sender = asyncio.create_task(self._event_sender(ws, self._event_queue))

            try:
                async for raw in ws:
                    await self._on_message(ws, raw)
            finally:
                sender.cancel()  # 取消当前 sender，queue 不丢失

    async def _event_sender(self, ws, event_queue: asyncio.Queue) -> None:
        """持续从队列读取事件并推送给后端"""
        while True:
            event = await event_queue.get()
            try:
                await ws.send(json.dumps(event, ensure_ascii=False))
                logger.debug("推送事件: %s %s", event.get("event"), event.get("task_id", "")[:8])
            except Exception as e:
                logger.error("事件推送失败，重新入队: %s", e)
                await event_queue.put(event)  # 断线后重连时补发
                raise  # 让连接中断触发重连

    async def _on_message(self, ws, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await self._send(ws, {"status": "error", "message": "JSON 格式错误"})
            return

        msg_id = payload.get("msg_id")
        command_payload = payload.get("data", payload)

        # 每条命令独立 task，不阻塞接收循环
        asyncio.create_task(self._dispatch(ws, msg_id, command_payload))

    async def _dispatch(self, ws, msg_id: str | None, payload: dict) -> None:
        """调用 handler，立即将 ACK 发回后端；msg_id 同时作为 task_id 传入"""
        try:
            result = await self.handler.handle(payload, self._event_queue, task_id=msg_id)
        except Exception as e:
            logger.exception("_dispatch 未捕获异常")
            result = {"status": "error", "message": str(e), "data": None}
        await self._send(ws, {"msg_id": msg_id, **result})

    async def _send(self, ws, data: dict) -> None:
        try:
            await ws.send(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.error("发送失败: %s", e)

    def stop(self) -> None:
        self._stop.set()
