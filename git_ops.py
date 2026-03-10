"""
Git 操作模块
目录结构：
  {BASE}/{project_code}/_main/          ← 主克隆，用于 worktree 管理，不直接编码
  {BASE}/{project_code}/{branch_dir}/   ← 每个需求分支独立 worktree，有独立 .claude/
"""
import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def branch_to_dirname(branch: str) -> str:
    """将分支名转换为安全的目录名，feature/foo-bar → feature__foo-bar"""
    return re.sub(r'[/\\:*?"<>|]', "__", branch)


async def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """执行 shell 命令，返回 (returncode, stdout, stderr)"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


async def clone_repo(remote_url: str, main_dir: str) -> None:
    """克隆远程仓库到 main_dir（作为主克隆，仅用于 worktree 管理）"""
    logger.info("克隆仓库 %s -> %s", remote_url, main_dir)
    code, out, err = await _run(["git", "clone", remote_url, main_dir])
    if code != 0:
        raise RuntimeError(f"git clone 失败: {err}")
    logger.info("克隆完成")


async def fetch_all(main_dir: str, retries: int = 3) -> None:
    """拉取所有远程引用，网络错误时自动重试"""
    last_err = ""
    for attempt in range(retries):
        code, _, err = await _run(["git", "fetch", "--all"], cwd=main_dir)
        if code == 0:
            return
        last_err = err
        if attempt < retries - 1:
            wait = 2 ** attempt
            logger.warning("git fetch 失败（第 %d/%d 次），%d 秒后重试: %s", attempt + 1, retries, wait, err)
            await asyncio.sleep(wait)
    raise RuntimeError(f"git fetch 失败: {last_err}")


async def remote_branch_exists(main_dir: str, branch: str) -> bool:
    """检查远程是否存在指定分支"""
    code, out, _ = await _run(
        ["git", "ls-remote", "--heads", "origin", branch], cwd=main_dir
    )
    return code == 0 and bool(out)


async def local_branch_exists(main_dir: str, branch: str) -> bool:
    """检查本地是否存在指定分支"""
    code, out, _ = await _run(
        ["git", "branch", "--list", branch], cwd=main_dir
    )
    return code == 0 and bool(out)


async def setup_worktree(
    main_dir: str, branch: str, worktree_dir: str, base_branch: str
) -> None:
    """
    为需求分支准备独立 worktree 目录，幂等操作：
    - 若 worktree 目录已存在 → 拉取最新代码
    - 若远程可达且分支存在 → checkout 到 worktree
    - 若远程可达但分支不存在 → 从 base_branch 新建分支到 worktree
    - 若远程不可达（网络/仓库不存在）→ 离线模式，从本地 base_branch 新建分支
    """
    wt_path = Path(worktree_dir)

    if wt_path.exists():
        # worktree 已存在，尝试拉取（新建分支可能无远程，忽略失败）
        logger.info("worktree 已存在，拉取最新: %s", worktree_dir)
        code, _, err = await _run(["git", "pull", "origin", branch], cwd=worktree_dir)
        if code != 0:
            logger.warning("pull 跳过（可能是纯本地分支）: %s", err)
        return

    # 尝试 fetch，失败时进入离线模式（本地建分支）
    fetch_ok = False
    try:
        await fetch_all(main_dir)
        fetch_ok = True
    except RuntimeError as e:
        logger.warning("无法连接远程仓库，切换为离线模式: %s", e)

    if fetch_ok and await remote_branch_exists(main_dir, branch):
        logger.info("远程分支 %s 存在，创建跟踪 worktree", branch)
        if await local_branch_exists(main_dir, branch):
            # 本地已有该分支，直接挂载到 worktree
            code, _, err = await _run(
                ["git", "worktree", "add", worktree_dir, branch],
                cwd=main_dir,
            )
        else:
            # 本地没有，创建本地分支并跟踪远程
            code, _, err = await _run(
                ["git", "worktree", "add", "--track", "-b", branch,
                 worktree_dir, f"origin/{branch}"],
                cwd=main_dir,
            )
    else:
        # 远程不可达 或 远程分支不存在：从本地 base_branch 新建分支
        if fetch_ok:
            logger.info("远程分支 %s 不存在，从 %s 新建 worktree", branch, base_branch)
        else:
            logger.info("离线模式：从本地 %s 新建分支 %s（首次推送时将自动创建远程分支）", base_branch, branch)

        # 确定 base_branch 的实际引用：
        # - 联网时用 origin/<base_branch>（刚 fetch，保证最新）
        # - 离线时用本地分支（退而求其次）
        if fetch_ok:
            start_point = f"origin/{base_branch}"
        elif await local_branch_exists(main_dir, base_branch):
            start_point = base_branch
        else:
            raise RuntimeError(
                f"离线模式下本地不存在 {base_branch} 分支，无法创建新分支"
            )

        code, _, err = await _run(
            ["git", "worktree", "add", "-b", branch, worktree_dir, start_point],
            cwd=main_dir,
        )

    if code != 0:
        raise RuntimeError(f"git worktree add 失败: {err}")

    logger.info("worktree 就绪: %s @ %s", worktree_dir, branch)


async def commit_and_push(worktree_dir: str, branch: str, message: str) -> str | None:
    """
    git add -A → commit → push。
    若无变更（nothing to commit）返回 None；
    成功返回 commit hash（短 8 位）；失败抛 RuntimeError。
    """
    code, _, err = await _run(["git", "add", "-A"], cwd=worktree_dir)
    if code != 0:
        raise RuntimeError(f"git add 失败: {err}")

    code, out, err = await _run(
        ["git", "commit", "-m", message], cwd=worktree_dir
    )
    if code != 0:
        if "nothing to commit" in err or "nothing to commit" in out:
            return None
        raise RuntimeError(f"git commit 失败: {err}")

    _, commit_hash, _ = await _run(["git", "rev-parse", "--short=8", "HEAD"], cwd=worktree_dir)

    code, _, err = await _run(["git", "push", "origin", branch], cwd=worktree_dir)
    if code != 0:
        # 新建的本地分支尚无上游，尝试 --set-upstream
        logger.warning("git push 失败，尝试设置上游分支: %s", err)
        code, _, err = await _run(
            ["git", "push", "--set-upstream", "origin", branch], cwd=worktree_dir
        )
        if code != 0:
            raise RuntimeError(f"git push 失败: {err}")

    return commit_hash.strip()


async def init_base_branch(main_dir: str, branch: str, project_name: str = "") -> dict:
    """
    确保 main_dir 中存在 branch 分支：
    - 远程已有该分支 → 本地切换并 pull
    - 远程没有该分支 → 新建分支，创建 .gitignore / README.md，commit，push
    返回 {"branch": branch, "created": bool, "message": str}
    """
    # fetch 已在 clone 之后，远程可达，直接检查
    remote_has = await remote_branch_exists(main_dir, branch)

    if remote_has:
        logger.info("远程 %s 分支已存在，切换并拉取", branch)
        if await local_branch_exists(main_dir, branch):
            code, _, err = await _run(["git", "checkout", branch], cwd=main_dir)
        else:
            code, _, err = await _run(
                ["git", "checkout", "-b", branch, f"origin/{branch}"], cwd=main_dir
            )
        if code != 0:
            raise RuntimeError(f"切换到 {branch} 分支失败: {err}")
        await _run(["git", "pull", "origin", branch], cwd=main_dir)
        return {"branch": branch, "created": False, "message": f"已切换到远程 {branch} 分支"}

    # 远程没有该分支：新建
    logger.info("远程不存在 %s 分支，本地新建并初始化", branch)
    code, _, err = await _run(["git", "checkout", "-b", branch], cwd=main_dir)
    if code != 0:
        raise RuntimeError(f"创建 {branch} 分支失败: {err}")

    # 写入初始文件
    import os
    title = project_name or os.path.basename(main_dir)

    gitignore = Path(main_dir) / ".gitignore"
    gitignore.write_text(
        "# Python\n__pycache__/\n*.py[cod]\n*.egg-info/\ndist/\nbuild/\n.venv/\n\n"
        "# Node\nnode_modules/\n.npm/\ndist/\n\n"
        "# IDE\n.idea/\n.vscode/\n*.swp\n\n"
        "# OS\n.DS_Store\nThumbs.db\n\n"
        "# Env\n.env\n.env.*\n",
        encoding="utf-8",
    )

    readme = Path(main_dir) / "README.md"
    readme.write_text(
        f"# {title}\n\n> 项目初始化自动生成\n",
        encoding="utf-8",
    )

    code, _, err = await _run(["git", "add", ".gitignore", "README.md"], cwd=main_dir)
    if code != 0:
        raise RuntimeError(f"git add 失败: {err}")

    code, out, err = await _run(
        ["git", "commit", "-m", f"chore: init {branch} branch"], cwd=main_dir
    )
    if code != 0:
        if "nothing to commit" in err or "nothing to commit" in out:
            pass  # 文件已存在且无变更，不阻断流程
        else:
            raise RuntimeError(f"git commit 失败: {err}")

    code, _, err = await _run(
        ["git", "push", "--set-upstream", "origin", branch], cwd=main_dir
    )
    if code != 0:
        raise RuntimeError(f"git push 失败: {err}")

    logger.info("已创建并推送 %s 分支（含 .gitignore / README.md）", branch)
    return {"branch": branch, "created": True, "message": f"已创建并推送 {branch} 分支"}


async def remove_worktree(main_dir: str, worktree_dir: str) -> None:
    """移除 worktree（保留分支，不删除远程）"""
    code, _, err = await _run(
        ["git", "worktree", "remove", "--force", worktree_dir],
        cwd=main_dir,
    )
    if code != 0:
        logger.warning("worktree remove 失败（可能已不存在）: %s", err)
    else:
        logger.info("worktree 已移除: %s", worktree_dir)
