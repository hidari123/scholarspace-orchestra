"""运行路由 —— 触发执行 + 查询状态。

⚠️ **执行是异步的** —— HTTP 请求立即返回 run_id，
实际执行在后台跑，前端通过 SSE 订阅进度。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from orchestra_core.application.use_cases import get_flow
from orchestra_core.domain.entities import Run
from orchestra_core.domain.errors import FlowValidationError
from orchestra_core.domain.graph import build_graph
from orchestra_core.domain.run_status import RunStatus
from orchestra_core.domain.value_objects import FlowId, RunId

from orchestra_http.dependencies import FlowContextDep, RunRepositoryDep
from orchestra_http.schemas import RunResponse

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/orchestra/runs", tags=["runs"])


def _to_response(run: Run) -> RunResponse:
    """领域对象 → 响应模型。"""
    return RunResponse(
        run_id=run.run_id,
        flow_id=run.flow_id,
        flow_version=run.flow_version,
        status=run.status.value,
        error=(
            {
                "code": run.error.code,
                "message": run.error.message,
                "failed_node_id": run.error.failed_node_id,
            }
            if run.error is not None
            else None
        ),
        usage={
            "prompt_tokens": run.usage.prompt_tokens,
            "completion_tokens": run.usage.completion_tokens,
            "total_tokens": run.usage.total_tokens,
            "cost": run.usage.cost,
            "duration_ms": run.usage.duration_ms,
        },
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run_endpoint(
    flow_id: UUID,
    context: FlowContextDep,
    runs: RunRepositoryDep,
) -> RunResponse:
    """触发一次运行。

    ⚠️ **202 Accepted 而不是 201** ——
       执行是异步的，请求只是「排队成功」，
       不是「执行完成」。

    Raises:
        HTTPException: 流程不存在（404）/ 图不合法（422）
    """
    flow = await get_flow(context, flow_id)
    if flow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"流程不存在：{flow_id}",
        )

    # ⚠️ 先校验 —— 有环的流程不能跑
    try:
        build_graph(flow)
    except FlowValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    run = Run(
        run_id=RunId(uuid4()),
        flow_id=FlowId(flow_id),
        flow_version=flow.version,
        status=RunStatus.PENDING,
    )
    await runs.save(run)

    return _to_response(run)


@router.get("/{run_id}", response_model=RunResponse)
async def get_run_endpoint(
    run_id: UUID,
    runs: RunRepositoryDep,
) -> RunResponse:
    """取运行状态。

    Raises:
        HTTPException: 不存在（404）
    """
    run = await runs.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"运行记录不存在：{run_id}",
        )

    return _to_response(run)


@router.get("", response_model=list[RunResponse])
async def list_runs_endpoint(
    flow_id: UUID,
    runs: RunRepositoryDep,
) -> list[RunResponse]:
    """列出某流程的所有运行。"""
    items = await runs.list_by_flow(flow_id)
    return [_to_response(run) for run in items]
