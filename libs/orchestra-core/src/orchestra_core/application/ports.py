"""应用层端口 —— 存储与外部系统的契约。

⚠️ **端口在应用层，实现放基础设施层** ——
   路由换数据库一行都不用改，测试不用起数据库。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from orchestra_core.domain.entities import Flow, Run

__all__ = [
    "FlowRepositoryPort",
    "RunRepositoryPort",
]


@runtime_checkable
class FlowRepositoryPort(Protocol):
    """流程存储端口。"""

    async def save(self, flow: Flow) -> Flow:
        """保存流程。

        ⚠️ **保存前必须校验** —— 有环的流程存进去，
        运行时才发现就晚了。
        """
        ...

    async def get(self, flow_id: UUID) -> Flow | None:
        """取流程。

        :return: None 表示不存在
        """
        ...

    async def list_all(self) -> tuple[Flow, ...]:
        """全部流程。"""
        ...

    async def delete(self, flow_id: UUID) -> bool:
        """删除流程。

        :return: 是否真的删了（不存在返回 False）
        """
        ...


@runtime_checkable
class RunRepositoryPort(Protocol):
    """运行记录存储端口。"""

    async def save(self, run: Run) -> Run:
        """保存运行记录。"""
        ...

    async def get(self, run_id: UUID) -> Run | None:
        """取运行记录。"""
        ...

    async def list_by_flow(self, flow_id: UUID) -> tuple[Run, ...]:
        """取某流程的所有运行。"""
        ...
