"""FastAPI 应用工厂。

⚠️ 用**工厂函数**而不是模块级 app 实例 ——
   测试时可以建多个隔离的实例，依赖覆盖互不干扰。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from orchestra_http.routers import events, flows, nodes, runs, sessions

__all__ = ["create_app"]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """生命周期钩子。

    ⚠️ 生产环境在这里初始化数据库连接、事件源、节点注册表。
    """
    del app  # ⚠️ 显式标记未使用 —— 避免 ARG001
    yield


def create_app(
    registry: Any = None,
    event_source: Any = None,
    **kwargs: Any,
) -> FastAPI:
    """创建 FastAPI 应用。

    :param registry: 节点注册表（注入到节点市场）
    :param event_source: 事件源（注入到 SSE 端点）
    :param kwargs: 传给 FastAPI 构造函数的参数
    :return: 应用实例
    """
    app = FastAPI(
        title="scholarspace-orchestra",
        description="scholarspace 编排引擎",
        version="0.1.0",
        lifespan=_lifespan,
        **kwargs,
    )

    app.include_router(flows.router)
    app.include_router(runs.router)
    app.include_router(sessions.router)
    app.include_router(nodes.router)
    app.include_router(events.router)

    # ⚠️ **总是设置**（包括 None）——
    #    只在传了值时才设的话，上一个 app 留下的事件源会**泄漏到下一个 app**。
    #    测试里「这个 app 没有事件源」的语义就失效了。
    nodes.set_registry(registry)
    events.set_event_source(event_source)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """健康检查。"""
        return {"status": "ok"}

    return app
