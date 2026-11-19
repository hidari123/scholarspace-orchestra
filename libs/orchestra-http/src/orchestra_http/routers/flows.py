"""流程路由 —— CRUD + 校验。

⚠️ **保存前必须校验** —— 有环的流程存进去，
运行时才发现就晚了。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from orchestra_core.application.use_cases import (
    FlowChanges,
    create_flow,
    delete_flow,
    get_flow,
    list_flows,
    update_flow,
)
from orchestra_core.domain.entities import Edge, Node, PortBinding
from orchestra_core.domain.errors import FlowValidationError
from orchestra_core.domain.value_objects import EdgeId, Position

from orchestra_http.dependencies import FlowContextDep
from orchestra_http.schemas import (
    CreateFlowRequest,
    FlowResponse,
    UpdateFlowRequest,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/orchestra/flows", tags=["flows"])


def _to_nodes(raw: list[dict[str, Any]]) -> tuple[Node, ...]:
    """把 HTTP 的节点字典转成领域对象。

    ⚠️ **这里做「最小转换」** —— 复杂的字段校验交给 Pydantic，
    领域层只关心结构对不对。
    """
    return tuple(
        Node(
            node_id=item["node_id"],
            node_type=item.get("node_type", "llm"),
            position=Position(
                x=float(item.get("position", {}).get("x", 0.0)),
                y=float(item.get("position", {}).get("y", 0.0)),
            ),
            config=item.get("config") or {},
            inputs=tuple(_to_binding(binding) for binding in item.get("inputs") or []),
        )
        for item in raw
    )


def _to_binding(raw: dict[str, Any]) -> PortBinding:
    """把端口绑定字典转成领域对象。"""
    return PortBinding(
        target_port=raw.get("target_port", "in"),
        source_node=raw.get("source_node", ""),
        source_port=raw.get("source_port", "out"),
        path=raw.get("path"),
    )


def _to_edges(raw: list[dict[str, Any]]) -> tuple[Edge, ...]:
    """把 HTTP 的边字典转成领域对象。"""
    return tuple(
        Edge(
            edge_id=EdgeId(uuid4()),
            source_node=item.get("source_node", ""),
            source_port=item.get("source_port", "out"),
            target_node=item.get("target_node", ""),
            target_port=item.get("target_port", "in"),
        )
        for item in raw
    )


def _to_response(flow: Any) -> FlowResponse:
    """领域对象 → 响应模型。"""
    return FlowResponse(
        flow_id=flow.flow_id,
        name=flow.name,
        version=flow.version,
        description=flow.description,
        access=flow.access.value,
        nodes=[
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "position": {"x": node.position.x, "y": node.position.y},
                "config": node.config,
                "inputs": [
                    {
                        "target_port": binding.target_port,
                        "source_node": binding.source_node,
                        "source_port": binding.source_port,
                        "path": binding.path,
                    }
                    for binding in node.inputs
                ],
            }
            for node in flow.nodes
        ],
        edges=[
            {
                "source_node": edge.source_node,
                "source_port": edge.source_port,
                "target_node": edge.target_node,
                "target_port": edge.target_port,
            }
            for edge in flow.edges
        ],
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


@router.post("", response_model=FlowResponse, status_code=status.HTTP_201_CREATED)
async def create_flow_endpoint(
    payload: CreateFlowRequest,
    context: FlowContextDep,
) -> FlowResponse:
    """创建流程。

    ⚠️ **有环 → 422** —— 保存时就拦住，不留到运行时。

    Raises:
        HTTPException: 校验失败
    """
    try:
        flow = await create_flow(
            context,
            name=payload.name,
            nodes=_to_nodes(payload.nodes),
            edges=_to_edges(payload.edges),
            description=payload.description,
        )
    except FlowValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except (KeyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"节点或边结构不合法：{error}",
        ) from error

    return _to_response(flow)


@router.get("", response_model=list[FlowResponse])
async def list_flows_endpoint(context: FlowContextDep) -> list[FlowResponse]:
    """列出全部流程。"""
    flows = await list_flows(context)
    return [_to_response(flow) for flow in flows]


@router.get("/{flow_id}", response_model=FlowResponse)
async def get_flow_endpoint(
    flow_id: UUID,
    context: FlowContextDep,
) -> FlowResponse:
    """取流程。

    Raises:
        HTTPException: 不存在（404）
    """
    flow = await get_flow(context, flow_id)
    if flow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"流程不存在：{flow_id}",
        )

    return _to_response(flow)


@router.put("/{flow_id}", response_model=FlowResponse)
async def update_flow_endpoint(
    flow_id: UUID,
    payload: UpdateFlowRequest,
    context: FlowContextDep,
) -> FlowResponse:
    """更新流程（版本号 +1）。

    Raises:
        HTTPException: 不存在（404）/ 校验失败（422）
    """
    changes = FlowChanges(
        name=payload.name,
        description=payload.description,
        nodes=_to_nodes(payload.nodes) if payload.nodes is not None else None,
        edges=_to_edges(payload.edges) if payload.edges is not None else None,
    )

    try:
        flow = await update_flow(context, flow_id, changes)
    except FlowValidationError as error:
        # ⚠️ 「不存在」是 404，「有环」是 422 —— 语义要分开
        if "不存在" in str(error):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    return _to_response(flow)


@router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flow_endpoint(
    flow_id: UUID,
    context: FlowContextDep,
) -> None:
    """删除流程。

    ⚠️ **不存在也返回 204** —— 删除是幂等的。
    """
    await delete_flow(context, flow_id)
