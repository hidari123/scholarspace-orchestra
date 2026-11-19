"""会话路由 —— 多轮对话的状态。

⚠️ **会话是内存态** —— 进程重启就没了。
生产环境要存 Redis/PostgreSQL。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from orchestra_core.application.use_cases import (
    append_message,
    create_session,
)
from orchestra_core.domain.errors import FlowValidationError
from orchestra_core.domain.session import MessageRole

from orchestra_http.dependencies import SessionContextDep
from orchestra_http.schemas import CreateSessionRequest, SessionResponse

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/orchestra/sessions", tags=["sessions"])

#: 会话存储 —— 内存实现。
#:
#: ⚠️ **模块级变量** —— 进程内共享。
_sessions: dict[UUID, object] = {}


def _to_response(session: object) -> SessionResponse:
    """领域对象 → 响应模型。"""
    return SessionResponse(
        session_id=session.session_id,  # type: ignore[attr-defined]
        flow_id=session.flow_id,  # type: ignore[attr-defined]
        title=session.title,  # type: ignore[attr-defined]
        variables=session.variables,  # type: ignore[attr-defined]
        created_at=session.created_at,  # type: ignore[attr-defined]
        updated_at=session.updated_at,  # type: ignore[attr-defined]
    )


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session_endpoint(
    payload: CreateSessionRequest,
    context: SessionContextDep,
) -> SessionResponse:
    """创建会话。

    Raises:
        HTTPException: 流程不存在（404）
    """
    try:
        session = await create_session(
            context,
            flow_id=payload.flow_id,
            title=payload.title,
            variables=payload.variables,
        )
    except FlowValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    _sessions[session.session_id] = session

    return _to_response(session)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session_endpoint(
    session_id: UUID,
    context: SessionContextDep,
) -> SessionResponse:
    """取会话。

    Raises:
        HTTPException: 不存在（404）
    """
    del context  # ⚠️ 显式标记未使用 —— 避免 ARG001
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话不存在：{session_id}",
        )

    return _to_response(session)


@router.post("/{session_id}/messages", response_model=SessionResponse)
async def append_message_endpoint(
    session_id: UUID,
    content: str,
    context: SessionContextDep,
) -> SessionResponse:
    """追加一条用户消息。

    ⚠️ **助手输出会写回会话变量** (`last_response`) ——
       下一轮可以引用。

    Raises:
        HTTPException: 会话不存在（404）
    """
    del context  # ⚠️ 显式标记未使用 —— 避免 ARG001
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话不存在：{session_id}",
        )

    # ⚠️ `append_message` 是**协程** —— 必须 await
    updated, _message = await append_message(
        session,  # type: ignore[arg-type]
        MessageRole.USER,
        content,
    )
    _sessions[session_id] = updated

    return _to_response(updated)
