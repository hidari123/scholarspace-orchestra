"""FastAPI 依赖 —— 仓储与用例上下文。

⚠️ **默认是内存实现** —— 生产环境用 `dependency_overrides` 换成 PostgreSQL。
路由代码一行都不用改。
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import Annotated

from fastapi import Depends
from orchestra_core.application.ports import FlowRepositoryPort, RunRepositoryPort
from orchestra_core.application.use_cases import FlowContext, SessionContext
from orchestra_core.infrastructure.memory import (
    InMemoryFlowRepository,
    InMemoryRunRepository,
)

__all__ = [
    "get_flow_context",
    "get_flow_repository",
    "get_run_repository",
    "get_session_context",
]


@cache
def get_flow_repository() -> FlowRepositoryPort:
    """流程仓储依赖。

    ⚠️ 用 `functools.cache` 做单例 —— 比 global 干净，
    不会有并发初始化的竞态。测试用 `dependency_overrides` 覆盖。
    """
    return InMemoryFlowRepository()


@cache
def get_run_repository() -> RunRepositoryPort:
    """运行仓储依赖。"""
    return InMemoryRunRepository()


def get_flow_context(
    flows: Annotated[FlowRepositoryPort, Depends(get_flow_repository)],
) -> FlowContext:
    """流程用例上下文。"""
    return FlowContext(flows=flows)


def get_session_context(
    flows: Annotated[FlowRepositoryPort, Depends(get_flow_repository)],
    runs: Annotated[RunRepositoryPort, Depends(get_run_repository)],
) -> SessionContext:
    """会话用例上下文。"""
    return SessionContext(flows=flows, runs=runs)


#: 类型别名 —— 路由里用着清楚。
FlowContextDep = Annotated[FlowContext, Depends(get_flow_context)]
SessionContextDep = Annotated[SessionContext, Depends(get_session_context)]
FlowRepositoryDep = Annotated[FlowRepositoryPort, Depends(get_flow_repository)]
RunRepositoryDep = Annotated[RunRepositoryPort, Depends(get_run_repository)]


def dependency_overrides() -> dict[Callable[..., object], Callable[..., object]]:
    """默认的依赖覆盖表（空的）。

    ⚠️ 保留这个函数是为了让「覆盖点」有明确的位置 ——
    生产环境在这里填 PostgreSQL 实现。
    """
    return {}
