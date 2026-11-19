"""SSE 端点 —— 实时推送运行进度。

⚠️ **SSE 而不是 WebSocket** ——
   数据是单向的（服务端 → 客户端），
   SSE 自带断线重连（`Last-Event-ID`），WebSocket 要自己实现。

⚠️ **断线重连靠 `seq`** ——
   客户端带 `Last-Event-ID: 42`，服务端从 43 开始补发。
   这就是 events 服务的 `seq` 存在的意义。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from orchestra_http.dependencies import RunRepositoryDep

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/orchestra/runs", tags=["events"])


#: 事件源 —— 由 app 工厂注入。
#:
#: ⚠️ **模块级变量** —— 事件源在应用启动时创建，
#: 之后不变。
_event_source: Any = None


def set_event_source(source: Any) -> None:
    """设置事件源。

    ⚠️ 在 `create_app()` 里调用一次。
    """
    global _event_source  # noqa: PLW0603
    _event_source = source


def _format_sse(event_type: str, data: dict[str, Any], event_id: str | None = None) -> str:
    r"""格式化成 SSE 帧。

    ⚠️ **格式规范**（每行以换行结束，帧以空行结束）：

        event: <类型>
        id: <事件 ID>
        data: <JSON>

    ⚠️ `data` 用 JSON 而不是裸文本 ——
    断线重连时客户端要靠 `id` 定位，数据本身是结构化的。

    ⚠️ **用 r 前缀是因为文档里有反斜杠** ——
    普通字符串里反斜杠会被当成转义符。
    """
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


@router.get(
    "/{run_id}/events",
    summary="订阅运行事件（SSE）",
    response_class=StreamingResponse,
)
async def stream_events(
    run_id: UUID,
    request: Request,
    runs: RunRepositoryDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after_seq: int = Query(default=0, ge=0, description="从该序号之后开始"),
) -> StreamingResponse:
    """订阅运行事件。

    ⚠️ **两种续传方式**：
        · `Last-Event-ID` 头 —— 浏览器原生重连时自动带
        · `?after_seq=` —— 客户端显式指定

    Raises:
        HTTPException: 运行不存在（404）
    """
    run = await runs.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"运行记录不存在：{run_id}",
        )

    # ⚠️ `Last-Event-ID` 优先 —— 浏览器自动带的
    start_seq = after_seq
    if last_event_id:
        try:
            start_seq = int(last_event_id)
        except ValueError:
            # ⚠️ 非法的事件 ID → 从头开始，不报错
            #    客户端可能带了脏数据，不该让它整个失败
            start_seq = after_seq

    async def event_generator() -> AsyncIterator[str]:
        """生成 SSE 帧。"""
        if _event_source is None:
            yield _format_sse(
                "error",
                {"code": "no_event_source", "message": "事件源未配置"},
            )
            return

        async for event in _event_source.subscribe(run_id, after_seq=start_seq):
            # ⚠️ **客户端断开时要停** ——
            #    不检查的话会一直生成，用户关了页面也在跑
            if await request.is_disconnected():
                return

            yield _format_sse(
                event.type,
                {
                    "run_id": str(event.stream_id),
                    "seq": event.seq,
                    "occurred_at": event.occurred_at.isoformat(),
                    **event.payload,
                },
                event_id=str(event.seq),
            )

            # ⚠️ 终止事件后结束流 ——
            #    客户端不用一直挂着等结束
            if event.is_terminal:
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # ⚠️ 关掉 nginx 的缓冲 —— 否则 SSE 会被攒着不发
            "X-Accel-Buffering": "no",
        },
    )
