"""
命令处理器
write_prompt 立即返回 task_id（ACK），后续通过 event_queue 推送进度/结果事件。
其余命令保持同步应答。
"""
import asyncio
import logging
from pathlib import Path
from typing import Any

import git_ops
from claude_runner import ClaudeRunner
from config import Config

logger = logging.getLogger(__name__)


def _ok(data: Any = None, msg: str = "success") -> dict:
    return {"status": "ok", "message": msg, "data": data}


def _err(msg: str) -> dict:
    return {"status": "error", "message": msg, "data": None}


class CommandHandler:
    def __init__(self):
        self.runner = ClaudeRunner()

    def _project_base(self, project_code: str) -> Path:
        return Path(Config.BASE_PROJECTS_DIR) / project_code

    def _main_dir(self, project_code: str) -> Path:
        return self._project_base(project_code) / "_main"

    def _worktree_dir(self, project_code: str, branch: str) -> Path:
        return self._project_base(project_code) / git_ops.branch_to_dirname(branch)

    # ------------------------------------------------------------------
    # 统一入口：event_queue 由 ws_client 传入，write_prompt 用它推送事件
    # ------------------------------------------------------------------
    async def handle(self, payload: dict, event_queue: asyncio.Queue, task_id: str | None = None) -> dict:
        command = payload.get("command", "")
        logger.info("收到命令: %s", command)

        try:
            if command == "project_init":
                return await self._project_init(payload)
            elif command == "requirement_init":
                return await self._requirement_init(payload)
            elif command == "write_prompt":
                return self._write_prompt(payload, event_queue, task_id)   # 同步，立即返回
            elif command == "list_sessions":
                return _ok(data=self.runner.list_sessions())
            elif command == "remove_session":
                return await self._remove_session(payload)
            else:
                return _err(f"未知命令: {command}")
        except KeyError as e:
            return _err(f"缺少必要字段: {e}")
        except Exception as e:
            logger.exception("命令 %s 执行异常", command)
            return _err(str(e))

    # ------------------------------------------------------------------
    # project_init
    # ------------------------------------------------------------------
    async def _project_init(self, payload: dict) -> dict:
        project_code: str = payload["project_code"]
        remote_url: str = payload["remote_url"]
        base_branch: str = payload.get("base_branch", Config.DEFAULT_BASE_BRANCH)
        main_dir = self._main_dir(project_code)

        if main_dir.exists():
            # 项目目录已存在，仍然要确保 base_branch 就绪并拉取到最新
            try:
                branch_info = await git_ops.init_base_branch(
                    str(main_dir), base_branch, project_name=project_code
                )
            except RuntimeError as e:
                logger.error(
                    "init_base_branch 失败（已存在 main_dir）[%s/%s]: %s",
                    project_code,
                    base_branch,
                    e,
                )
                return _err(f"项目目录已存在，但 {base_branch} 分支初始化/更新失败: {e}")

            return _ok(
                data={
                    "main_dir": str(main_dir),
                    "branch": branch_info["branch"],
                    "branch_created": branch_info["created"],
                },
                msg=f"项目 {project_code} 已存在，{branch_info['message']}",
            )

        main_dir.parent.mkdir(parents=True, exist_ok=True)
        await git_ops.clone_repo(remote_url, str(main_dir))

        try:
            branch_info = await git_ops.init_base_branch(
                str(main_dir), base_branch, project_name=project_code
            )
        except RuntimeError as e:
            logger.error("init_base_branch 失败 [%s/%s]: %s", project_code, base_branch, e)
            return _err(f"项目克隆成功，但 {base_branch} 分支初始化失败: {e}")

        return _ok(
            data={"main_dir": str(main_dir), "branch": branch_info["branch"], "branch_created": branch_info["created"]},
            msg=f"项目 {project_code} 初始化完成，{branch_info['message']}",
        )

    # ------------------------------------------------------------------
    # requirement_init
    # ------------------------------------------------------------------
    async def _requirement_init(self, payload: dict) -> dict:
        project_code: str = payload["project_code"]
        branch: str = payload["branch"]
        base_branch: str = payload.get("base_branch", Config.DEFAULT_BASE_BRANCH)
        main_dir = self._main_dir(project_code)

        if not main_dir.exists():
            return _err(f"项目未初始化: {project_code}，请先执行 project_init")

        worktree_dir = self._worktree_dir(project_code, branch)
        try:
            await git_ops.setup_worktree(str(main_dir), branch, str(worktree_dir), base_branch)
        except RuntimeError as e:
            logger.error("setup_worktree 失败 [%s/%s]: %s", project_code, branch, e)
            return _err(f"分支初始化失败: {e}")

        self.runner.register_session(project_code, branch, str(worktree_dir))

        return _ok(
            data={"worktree_dir": str(worktree_dir), "branch": branch},
            msg=f"需求分支 {branch} 就绪",
        )

    # ------------------------------------------------------------------
    # write_prompt - 非 async，立即返回 task_id
    # ------------------------------------------------------------------
    def _write_prompt(self, payload: dict, event_queue: asyncio.Queue, task_id: str | None) -> dict:
        project_code: str = payload["project_code"]
        branch: str = payload["branch"]
        prompt: str = payload["prompt"]
        auto_commit: bool = bool(payload.get("auto_commit", False))

        if not prompt.strip():
            return _err("prompt 不能为空")

        if not self.runner.session_exists(project_code, branch):
            return _err(f"会话 {project_code}:{branch} 不存在，请先执行 requirement_init")

        task_id = self.runner.submit_prompt(project_code, branch, prompt, event_queue, task_id=task_id, auto_commit=auto_commit)
        logger.info("任务已提交: %s -> %s:%s", task_id, project_code, branch)

        return _ok(data={"task_id": task_id}, msg="任务已提交，通过事件推送进度")

    # ------------------------------------------------------------------
    # remove_session
    # ------------------------------------------------------------------
    async def _remove_session(self, payload: dict) -> dict:
        project_code: str = payload["project_code"]
        branch: str = payload["branch"]
        do_remove_wt: bool = payload.get("remove_worktree", False)

        self.runner.remove_session(project_code, branch)

        if do_remove_wt:
            main_dir = self._main_dir(project_code)
            worktree_dir = self._worktree_dir(project_code, branch)
            if main_dir.exists():
                await git_ops.remove_worktree(str(main_dir), str(worktree_dir))

        return _ok(msg=f"会话 {project_code}:{branch} 已移除")
