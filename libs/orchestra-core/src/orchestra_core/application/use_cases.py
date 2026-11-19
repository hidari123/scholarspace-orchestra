"""应用用例 —— 流程与会话的编排。

⚠️ 这一层做三件事：
    ① 校验（保存前拦住环）
    ② 版本管理（每次修改 +1）
    ③ 会话变量累积（多轮对话）
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid4

from orchestra_core.application.ports import FlowRepositoryPort, RunRepositoryPort
from orchestra_core.domain.entities import Edge, Flow, Node
from orchestra_core.domain.errors import FlowValidationError
from orchestra_core.domain.graph import build_graph
from orchestra_core.domain.session import Message, MessageRole, Session
from orchestra_core.domain.value_objects import FlowId, FlowVersion, SessionId

__all__ = [
    "FlowChanges",
    "FlowContext",
    "SessionContext",
    "append_message",
    "create_flow",
    "create_session",
    "delete_flow",
    "get_flow",
    "list_flows",
    "update_flow",
]


@dataclass(frozen=True, slots=True)
class FlowContext:
    """流程用例上下文。"""

    #: 流程仓储
    flows: FlowRepositoryPort


@dataclass(frozen=True, slots=True)
class FlowChanges:
    """流程改动。

    ⚠️ **None 表示「不改」** —— 与「改成空」区分开。
    """

    #: 新名字
    name: str | None = None
    #: 新节点
    nodes: tuple[Node, ...] | None = None
    #: 新边
    edges: tuple[Edge, ...] | None = None
    #: 新描述
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SessionContext:
    """会话用例上下文。"""

    #: 流程仓储
    flows: FlowRepositoryPort
    #: 运行仓储
    runs: RunRepositoryPort


# ══════════════════════════════════════════════════════════
# 流程
# ══════════════════════════════════════════════════════════


async def create_flow(
    context: FlowContext,
    name: str,
    nodes: tuple[Node, ...] = (),
    edges: tuple[Edge, ...] = (),
    description: str = "",
) -> Flow:
    """创建流程。

    :raises FlowValidationError: 图有环 / 节点 ID 重复
    """
    flow = Flow(
        flow_id=FlowId(uuid4()),
        name=name,
        nodes=nodes,
        edges=edges,
        description=description,
        version=FlowVersion(1),
    )

    # ⚠️ 仓储的 save 会校验 —— 有环直接抛
    return await context.flows.save(flow)


async def update_flow(
    context: FlowContext,
    flow_id: UUID,
    changes: FlowChanges,
) -> Flow:
    """更新流程。

    ⚠️ **版本号 +1** —— 正在运行的 run 引用的是特定版本，
    改了流程不影响它。

    ⚠️ **改动打包成 dataclass** ——
       6 个参数超过 ruff 上限（5 个），
       而且「哪些字段要改」本身就是一个整体概念。

    :raises FlowValidationError: 流程不存在 / 图有环
    """
    existing = await context.flows.get(flow_id)
    if existing is None:
        raise FlowValidationError(f"流程不存在：{flow_id}")

    updated = replace(
        existing,
        name=changes.name if changes.name is not None else existing.name,
        nodes=changes.nodes if changes.nodes is not None else existing.nodes,
        edges=changes.edges if changes.edges is not None else existing.edges,
        description=(
            changes.description if changes.description is not None else existing.description
        ),
        version=FlowVersion(existing.version + 1),
    )

    return await context.flows.save(updated)


async def get_flow(context: FlowContext, flow_id: UUID) -> Flow | None:
    """取流程。"""
    return await context.flows.get(flow_id)


async def list_flows(context: FlowContext) -> tuple[Flow, ...]:
    """列出全部流程。"""
    return await context.flows.list_all()


async def delete_flow(context: FlowContext, flow_id: UUID) -> bool:
    """删除流程。"""
    return await context.flows.delete(flow_id)


async def validate_flow(context: FlowContext, flow_id: UUID) -> bool:
    """校验流程是否有环。

    :return: 是否合法
    """
    flow = await context.flows.get(flow_id)
    if flow is None:
        return False

    try:
        build_graph(flow)
    except FlowValidationError:
        return False

    return True


# ══════════════════════════════════════════════════════════
# 会话
# ══════════════════════════════════════════════════════════


async def create_session(
    context: SessionContext,
    flow_id: UUID,
    title: str = "",
    variables: dict[str, Any] | None = None,
) -> Session:
    """创建会话。

    :raises FlowValidationError: 流程不存在
    """
    flow = await context.flows.get(flow_id)
    if flow is None:
        raise FlowValidationError(f"流程不存在：{flow_id}")

    return Session(
        session_id=SessionId(uuid4()),
        flow_id=FlowId(flow_id),
        title=title or flow.name,
        variables=dict(variables or {}),
    )


async def append_message(
    session: Session,
    role: MessageRole,
    content: str,
    node_run_ids: tuple[str, ...] = (),
) -> tuple[Session, Message]:
    """追加消息并更新会话变量。

    ⚠️ **助手的消息会写回会话变量** ——
       多轮对话时，上一轮的输出要能被下一轮引用。

    :param session: 当前会话
    :param role: 消息角色
    :param content: 消息内容
    :param node_run_ids: 关联的节点运行
    :return: (新会话, 新消息)
    """
    message = Message(
        message_id=uuid4(),
        session_id=session.session_id,
        role=role,
        content=content,
        node_run_ids=node_run_ids,
    )

    # ⚠️ 助手输出写回 `last_response` —— 下一轮可以引用
    variables = dict(session.variables)
    if role is MessageRole.ASSISTANT:
        variables["last_response"] = content

    updated = replace(session, variables=variables)

    return updated, message
