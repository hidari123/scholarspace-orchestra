"""内存实现 —— 开发与测试用。

⚠️ **生产环境用 PostgreSQL**，但接口一致。
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from orchestra_core.domain.entities import Flow, Run
from orchestra_core.domain.graph import build_graph

__all__ = [
    "InMemoryFlowRepository",
    "InMemoryRunRepository",
]


class InMemoryFlowRepository:
    """内存流程存储。"""

    def __init__(self) -> None:
        self._flows: dict[UUID, Flow] = {}
        #: ⚠️ 锁 —— 并发保存时要保证「校验 + 写入」是原子的
        self._lock = asyncio.Lock()

    async def save(self, flow: Flow) -> Flow:
        """保存流程（**先校验**）。

        ⚠️ **保存前必须校验** —— 有环的流程存进去，
        运行时才发现就晚了。
        """
        async with self._lock:
            build_graph(flow)  # ⚠️ 校验失败会抛异常
            self._flows[flow.flow_id] = flow

        return flow

    async def get(self, flow_id: UUID) -> Flow | None:
        """取流程。"""
        return self._flows.get(flow_id)

    async def list_all(self) -> tuple[Flow, ...]:
        """全部流程。"""
        return tuple(self._flows.values())

    async def delete(self, flow_id: UUID) -> bool:
        """删除流程。"""
        async with self._lock:
            return self._flows.pop(flow_id, None) is not None


class InMemoryRunRepository:
    """内存运行记录存储。"""

    def __init__(self) -> None:
        self._runs: dict[UUID, Run] = {}

    async def save(self, run: Run) -> Run:
        """保存运行记录。"""
        self._runs[run.run_id] = run
        return run

    async def get(self, run_id: UUID) -> Run | None:
        """取运行记录。"""
        return self._runs.get(run_id)

    async def list_by_flow(self, flow_id: UUID) -> tuple[Run, ...]:
        """取某流程的所有运行。"""
        return tuple(run for run in self._runs.values() if run.flow_id == flow_id)
