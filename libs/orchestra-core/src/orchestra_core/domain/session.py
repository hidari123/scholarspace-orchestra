"""会话与消息 —— 多轮对话的状态。

⚠️ **会话是「有状态的」** —— 流程本身无状态，
   多轮对话靠会话保存变量与消息历史。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from orchestra_core.domain.value_objects import FlowId, SessionId

__all__ = [
    "ContentBlock",
    "DelegationRecord",
    "Message",
    "MessageRole",
    "Session",
]


class MessageRole(StrEnum):
    """消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ContentBlock:
    """富内容块。

    ⚠️ **不只是纯文本** —— 引用、图表、文件都要能表达。
    """

    #: 块类型（text / citation / chart / file / image）
    type: str
    #: 内容
    content: Any
    #: 附加元数据
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Message:
    """一条消息。"""

    message_id: UUID
    session_id: SessionId
    role: MessageRole
    content: str
    #: 富内容块
    blocks: tuple[ContentBlock, ...] = ()
    #: 关联的节点运行记录
    node_run_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class DelegationRecord:
    """多 Agent 委派记录。

    ⚠️ **追踪「谁委派了谁」** ——
       多 Agent 场景下，出问题时要知道是哪一层委派的。
    """

    #: 父节点
    parent_node: str
    #: 子 Agent
    child_agent: str
    #: 委派的任务
    task: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    #: 状态
    status: str = "running"


@dataclass(frozen=True, slots=True)
class Session:
    """会话。

    ⚠️ **变量是会话的核心** —— 多轮对话时，
    上一轮的输出会成为这一轮的输入。
    """

    session_id: SessionId
    #: 绑定的流程
    flow_id: FlowId
    #: 标题
    title: str = ""
    #: 会话变量（跨轮次累积）
    variables: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
