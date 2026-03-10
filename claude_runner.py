"""
Claude 运行器
- 每个 (project_code, branch) 对应一个独立会话
- submit_prompt 立即返回 task_id，后台执行并通过 event_queue 推送事件
- 同一会话的多条 prompt 通过 asyncio.Lock 串行排队执行
"""
import asyncio
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field

import git_ops
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    project_dir: str
    branch: str
    has_prior_turn: bool = False
    # 同一会话的 prompt 串行执行
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ClaudeRunner:

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._submitted_tasks: set[str] = set()  # 已提交的 task_id，用于去重

    def _key(self, project_code: str, branch: str) -> str:
        return f"{project_code}:{branch}"

    def register_session(self, project_code: str, branch: str, project_dir: str) -> None:
        key = self._key(project_code, branch)
        if key not in self._sessions:
            self._sessions[key] = SessionState(project_dir=project_dir, branch=branch)
            logger.info("注册会话: %s -> %s", key, project_dir)

    def session_exists(self, project_code: str, branch: str) -> bool:
        return self._key(project_code, branch) in self._sessions

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def remove_session(self, project_code: str, branch: str) -> None:
        self._sessions.pop(self._key(project_code, branch), None)

    # ------------------------------------------------------------------
    # 公共接口：立即返回 task_id，后台推送事件
    # ------------------------------------------------------------------
    def submit_prompt(
        self,
        project_code: str,
        branch: str,
        prompt: str,
        event_queue: asyncio.Queue,
        task_id: str | None = None,
        auto_commit: bool = False,
    ) -> str:
        """提交 prompt，立即返回 task_id，异步执行并将事件写入 event_queue。
        task_id 优先使用调用方传入的值（通常直接用 msg_id），没有时才自动生成。
        """
        key = self._key(project_code, branch)
        if key not in self._sessions:
            raise KeyError(f"会话 {key} 不存在，请先执行 requirement_init")

        task_id = task_id or str(uuid.uuid4())

        if task_id in self._submitted_tasks:
            logger.warning("任务 %s 已提交，忽略重复请求", task_id[:8])
            return task_id
        self._submitted_tasks.add(task_id)

        session = self._sessions[key]

        # 创建后台任务，不等待
        asyncio.create_task(
            self._execute_async(session, prompt, task_id, event_queue, auto_commit),
            name=f"claude-{task_id[:8]}",
        )
        return task_id

    # ------------------------------------------------------------------
    # 内部：后台执行，逐行推送进度
    # ------------------------------------------------------------------
    async def _execute_async(
        self,
        session: SessionState,
        prompt: str,
        task_id: str,
        event_queue: asyncio.Queue,
        auto_commit: bool = False,
    ) -> None:
        async def push(event: str, **kwargs) -> None:
            await event_queue.put({"type": "event", "event": event, "task_id": task_id, **kwargs})

        # 同一会话串行：等待锁（可能排队等前一条 prompt 完成）
        async with session.lock:
            await push("started")
            try:
                await self._run_claude(session, prompt, task_id, event_queue, push, auto_commit)
            except Exception as e:
                logger.exception("任务 %s 异常", task_id)
                await push("error", status="error", data={"message": str(e)})

    async def _run_claude(self, session, prompt, task_id, event_queue, push, auto_commit: bool = False) -> None:
        claude_bin = _resolve_claude_cmd()
        cmd = [claude_bin, "--dangerously-skip-permissions"]
        if session.has_prior_turn:
            cmd.append("--continue")
        cmd.extend(["--print", prompt])

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)

        logger.info("任务 %s | 目录: %s | cmd: %s ...", task_id[:8], session.project_dir, " ".join(cmd[:3]))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=session.project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        output_lines: list[str] = []

        async def read_stdout() -> None:
            while True:
                line = await proc.stdout.readline()
                if line == b"":
                    break
                text = line.decode().rstrip("\r\n")
                output_lines.append(text)
                await push("progress", data={"line": text})

        try:
            await asyncio.wait_for(read_stdout(), timeout=Config.CLAUDE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await push(
                "error",
                status="error",
                data={"message": f"执行超时（>{Config.CLAUDE_TIMEOUT}s）"},
            )
            return

        await proc.wait()

        if proc.returncode == 0:
            session.has_prior_turn = True
            full_output = "\n".join(output_lines)
            logger.info("任务 %s 完成，输出 %d 字符", task_id[:8], len(full_output))
            await push("done", status="ok", data={"output": full_output})

            if auto_commit:
                try:
                    commit_msg = f"auto: {prompt[:72].strip()} [{task_id[:8]}]"
                    commit_hash = await git_ops.commit_and_push(
                        session.project_dir, session.branch, commit_msg
                    )
                    if commit_hash:
                        await push("committed", status="ok",
                                   data={"commit": commit_hash, "pushed": True})
                    else:
                        await push("committed", status="skipped",
                                   data={"message": "无变更，跳过 commit"})
                except Exception as e:
                    logger.error("任务 %s commit/push 失败: %s", task_id[:8], e)
                    await push("committed", status="error", data={"message": str(e)})
        else:
            err = (await proc.stderr.read()).decode().strip()
            logger.error("任务 %s 失败，退出码 %d: %s", task_id[:8], proc.returncode, err)
            await push(
                "error",
                status="error",
                data={"message": f"Claude 退出码 {proc.returncode}: {err}"},
            )


def _resolve_claude_cmd() -> str:
    for name in (Config.CLAUDE_CMD, Config.CLAUDE_CMD + ".cmd"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError(
        f"找不到 Claude 命令 '{Config.CLAUDE_CMD}'，"
        "请安装: npm install -g @anthropic-ai/claude-code"
    )
