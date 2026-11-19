"""执行服务 —— 把 HTTP 的「触发运行」接到调度器。

⚠️ **这一层解决三个问题**：
    ① 触发运行不能同步执行（会 HTTP 超时）
    ② 执行进度要通过 SSE 推出去
    ③ 运行记录要持久化（前端刷新页面还能看到）

⚠️ **用 `asyncio.create_task` 而不是 BackgroundTasks** ——
   BackgroundTasks 是在响应返回**之后**才跑，
   但 FastAPI 的依赖清理会在响应完成后立即发生
   （依赖里的 `yield` 之后代码就执行了）——
   后台任务可能拿到已关闭的连接。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from orchestra_core.domain.entities import ErrorInfo, Flow, Run
from orchestra_core.domain.graph import build_graph
from orchestra_core.domain.run_status import RunStatus
from orchestra_runtime.scheduler import Scheduler

__all__ = [
    "ExecutionContext",
    "ExecutionService",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecutionContext:
    """一次执行的上下文。"""

    #: 运行记录
    run: Run
    #: 流程
    flow: Flow
    #: 初始变量
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionService:
    """执行服务。

    ⚠️ **调度器是共享的** —— 执行器注册表不随运行变。
    """

    #: 调度器
    scheduler: Scheduler
    #: 运行仓储
    run_repository: Any
    #: 事件发布回调（可选）
    publish_event: Callable[..., Any] | None = None
    #: 正在执行的任务 —— 用来等它结束（测试用）
    tasks: dict[UUID, asyncio.Task[Run]] = field(default_factory=dict)

    async def start(
        self,
        run: Run,
        flow: Flow,
        variables: dict[str, Any] | None = None,
    ) -> Run:
        """启动一次执行。

        ⚠️ **立即返回，不等执行完** ——
        HTTP 请求不能等整个流程跑完（可能几分钟）。

        :param run: 运行记录（pending 状态）
        :param flow: 流程
        :param variables: 初始变量
        :return: 传入的 run（状态未变）
        """
        task = asyncio.create_task(self._execute(run, flow, variables or {}))
        self.tasks[run.run_id] = task

        # ⚠️ **不要 await task** ——
        # await 会等执行完，HTTP 请求就阻塞了。
        # 用 done callback 清理引用。
        task.add_done_callback(lambda _t: self.tasks.pop(run.run_id, None))

        return run

    async def _execute(
        self,
        run: Run,
        flow: Flow,
        variables: dict[str, Any],
    ) -> Run:
        """真正执行。

        ⚠️ **异常必须捕获** ——
        未捕获的异常会让 task 静默死掉，
        run 永远停在 running 状态。
        """
        try:
            graph = build_graph(flow)
        except Exception as error:  # noqa: BLE001
            # ⚠️ 图不合法 —— 直接失败
            failed = replace(
                run,
                status=RunStatus.FAILED,
                error=_error_info("invalid_graph", str(error)),
            )
            await self._save(failed)
            return failed

        # ⚠️ 标记为 running
        running = replace(run, status=RunStatus.RUNNING)
        await self._save(running)

        try:
            result = await self.scheduler.run(
                graph,
                running,
                variables=variables,
            )
        except Exception as error:
            # ⚠️ **调度器自己抛异常** —— 不接住的话 run 永远停在 running
            logger.exception("运行失败：%s", run.run_id)
            failed = replace(
                running,
                status=RunStatus.FAILED,
                error=_error_info("scheduler_error", str(error)),
            )
            await self._save(failed)
            return failed

        await self._save(result)
        return result

    async def wait(self, run_id: UUID) -> Run | None:
        """等一次执行结束（测试用）。

        :return: 最终的运行记录
        """
        task = self.tasks.get(run_id)
        if task is None:
            return None

        return await task

    async def _save(self, run: Run) -> None:
        """保存运行记录。"""
        await self.run_repository.save(run)

    async def cancel(self, run_id: UUID) -> bool:
        """取消一次执行。

        :return: 是否真的取消了什么
        """
        task = self.tasks.get(run_id)
        if task is None or task.done():
            return False

        task.cancel()
        return True


def _error_info(code: str, message: str) -> ErrorInfo:
    """构造 ErrorInfo。"""
    return ErrorInfo(code=code, message=message)
