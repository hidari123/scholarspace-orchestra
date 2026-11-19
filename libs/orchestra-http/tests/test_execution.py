"""执行服务测试 —— 「触发运行」真的跑起来。

⚠️ 重点验证：
    ① 触发运行不阻塞 HTTP 响应
    ② 运行状态能被查到（pending → running → succeeded）
    ③ 图不合法 → 直接 failed（不是永远 pending）
    ④ 调度器抛异常 → failed（不是永远 running）
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from orchestra_core.domain.entities import Edge, Flow, Node, Run
from orchestra_core.domain.run_status import RunStatus
from orchestra_core.domain.value_objects import EdgeId, FlowId, Position, RunId
from orchestra_core.infrastructure.memory import (
    InMemoryFlowRepository,
    InMemoryRunRepository,
)
from orchestra_runtime.executor import NodeExecutionResult, NodeExecutor
from orchestra_runtime.scheduler import Scheduler

from orchestra_http.app import create_app
from orchestra_http.dependencies import (
    get_execution_service,
    get_flow_repository,
    get_run_repository,
)
from orchestra_http.execution import ExecutionService

#: 节点字典。
NODE_A = {
    "node_id": "a",
    "node_type": "llm",
    "position": {"x": 0, "y": 0},
    "config": {},
    "inputs": [],
}


class FakeExecutor(NodeExecutor):
    """假执行器 —— 记录调用。"""

    def __init__(self, node_type: str = "llm") -> None:
        self._node_type = node_type
        self.calls: list[str] = []

    @property
    def node_type(self) -> str:
        """节点类型。"""
        return self._node_type

    async def execute(
        self,
        node: Node,
        inputs: dict[str, Any],
        ctx: Any,
    ) -> NodeExecutionResult:
        """记录并成功。"""
        del inputs, ctx
        self.calls.append(node.node_id)
        return NodeExecutionResult(outputs={"out": "ok"})


class ExplodingExecutor(NodeExecutor):
    """会抛异常的��行器。"""

    @property
    def node_type(self) -> str:
        """节点类型。"""
        return "llm"

    async def execute(
        self,
        node: Node,
        inputs: dict[str, Any],
        ctx: Any,
    ) -> NodeExecutionResult:
        """直接抛异常。"""
        del node, inputs, ctx
        raise RuntimeError("执行器炸了")


@pytest.fixture
def flow_repo() -> InMemoryFlowRepository:
    """流程仓储。"""
    return InMemoryFlowRepository()


@pytest.fixture
def run_repo() -> InMemoryRunRepository:
    """运行仓储。"""
    return InMemoryRunRepository()


def make_service(run_repo: InMemoryRunRepository, scheduler: Scheduler) -> ExecutionService:
    """构造执行服务。"""
    return ExecutionService(
        scheduler=scheduler,
        run_repository=run_repo,
    )


class TestExecutionService:
    """执行服务。"""

    @pytest.mark.asyncio
    async def test_start_executes_flow(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 触发后流程真的执行了。"""
        executor = FakeExecutor()
        service = make_service(run_repo, Scheduler({"llm": executor}))

        flow = _make_flow()
        run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))

        await service.start(run, flow)
        result = await service.wait(run.run_id)

        assert result is not None
        assert result.status is RunStatus.SUCCEEDED
        # ⚠️ 两个节点都执行了（b 没有依赖，两个可以并行）`n        assert sorted(executor.calls) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_start_returns_immediately(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ `start` 立即返回 —— 不等执行完。

        否则 HTTP 请求会阻塞到整个流程跑完。
        """
        executor = FakeExecutor()
        service = make_service(run_repo, Scheduler({"llm": executor}))

        flow = _make_flow()
        run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))

        # ⚠️ 用一个慢执行器 —— 如果 start 等待，测试会超时
        await asyncio.wait_for(service.start(run, flow), timeout=1.0)

        await service.wait(run.run_id)

    @pytest.mark.asyncio
    async def test_invalid_graph_fails_run(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 图不合法 → 直接 failed，不是永远 pending。"""
        service = make_service(run_repo, Scheduler({}))

        # ⚠️ 造一个有环的 flow
        flow = _make_flow(with_cycle=True)
        run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))

        await service.start(run, flow)
        result = await service.wait(run.run_id)

        assert result is not None
        assert result.status is RunStatus.FAILED
        assert result.error is not None
        assert result.error.code == "invalid_graph"

    @pytest.mark.asyncio
    async def test_scheduler_exception_fails_run(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 调度器抛异常 → failed，不是永远 running。

        这是最容易漏的一类 bug —— 未捕获的异常会让
        task 静默死掉，run 永远停在 running。
        """
        service = make_service(run_repo, Scheduler({"llm": ExplodingExecutor()}))

        flow = _make_flow()
        run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))

        await service.start(run, flow)
        result = await service.wait(run.run_id)

        assert result is not None
        assert result.status is RunStatus.FAILED
        assert result.error is not None
        assert result.error.code == "scheduler_error"

    @pytest.mark.asyncio
    async def test_run_saved_to_repository(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 执行结果写回仓储 —— 路由才查得到。"""
        service = make_service(run_repo, Scheduler({"llm": FakeExecutor()}))

        flow = _make_flow()
        run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))

        await service.start(run, flow)
        await service.wait(run.run_id)

        saved = await run_repo.get(run.run_id)
        assert saved is not None
        assert saved.status is RunStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_cancel_running_execution(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 取消正在执行的运行。"""

        class SlowExecutor(NodeExecutor):
            """很慢的执行器。"""

            @property
            def node_type(self) -> str:
                """节点类型。"""
                return "llm"

            async def execute(
                self,
                node: Node,
                inputs: dict[str, Any],
                ctx: Any,
            ) -> NodeExecutionResult:
                """睡很久。"""
                del node, inputs, ctx
                await asyncio.sleep(30)
                return NodeExecutionResult(outputs={"out": "ok"})

        service = make_service(run_repo, Scheduler({"llm": SlowExecutor()}))

        flow = _make_flow()
        run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))

        await service.start(run, flow)
        cancelled = await service.cancel(run.run_id)

        assert cancelled

    @pytest.mark.asyncio
    async def test_cancel_finished_execution_returns_false(
        self, run_repo: InMemoryRunRepository
    ) -> None:
        """⚠️ 取消已结束的运行 → False。"""
        service = make_service(run_repo, Scheduler({"llm": FakeExecutor()}))

        flow = _make_flow()
        run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))

        await service.start(run, flow)
        await service.wait(run.run_id)

        assert not await service.cancel(run.run_id)


class TestRunEndpointIntegration:
    """端点与执行的集成。"""

    def test_run_endpoint_uses_shared_repository(
        self,
        flow_repo: InMemoryFlowRepository,
        run_repo: InMemoryRunRepository,
    ) -> None:
        """执行服务与路由必须用同一个仓储。

        ⚠️ 自己 new 一个的话，路由永远查到 pending。
        """
        app = create_app()
        app.dependency_overrides[get_flow_repository] = lambda: flow_repo
        app.dependency_overrides[get_run_repository] = lambda: run_repo
        app.dependency_overrides[get_execution_service] = lambda: make_service(
            run_repo, Scheduler({"llm": FakeExecutor()})
        )

        with TestClient(app) as client:
            created = client.post(
                "/api/v1/orchestra/flows",
                json={"name": "A", "nodes": [NODE_A], "edges": []},
            ).json()

            response = client.post(
                "/api/v1/orchestra/runs",
                params={"flow_id": created["flow_id"]},
            )

            assert response.status_code == 202
            run_id = response.json()["run_id"]

            # ⚠️ 轮询直到结束
            for _attempt in range(50):
                run = client.get(f"/api/v1/orchestra/runs/{run_id}").json()
                if run["status"] not in ("pending", "running"):
                    break
                time.sleep(0.02)

            assert run["status"] == "succeeded"

    def test_cancel_unknown_run_returns_404(
        self,
        flow_repo: InMemoryFlowRepository,
        run_repo: InMemoryRunRepository,
    ) -> None:
        """⚠️ 取消不存在的运行 → 404。"""
        app = create_app()
        app.dependency_overrides[get_flow_repository] = lambda: flow_repo
        app.dependency_overrides[get_run_repository] = lambda: run_repo

        with TestClient(app) as client:
            response = client.post(f"/api/v1/orchestra/runs/{uuid4()}/cancel")

        assert response.status_code == 404


def _make_flow(with_cycle: bool = False) -> Flow:
    """构造流程。

    :param with_cycle: 是否造一个有环的
    """
    node_a = Node(
        node_id="a",
        node_type="llm",
        position=Position(x=0.0, y=0.0),
    )
    node_b = Node(
        node_id="b",
        node_type="llm",
        position=Position(x=0.0, y=100.0),
    )

    edges = []
    if with_cycle:
        edges.append(
            Edge(
                edge_id=EdgeId(uuid4()),
                source_node="a",
                source_port="out",
                target_node="b",
                target_port="in",
            )
        )
        edges.append(
            Edge(
                edge_id=EdgeId(uuid4()),
                source_node="b",
                source_port="out",
                target_node="a",
                target_port="in",
            )
        )

    return Flow(
        flow_id=FlowId(uuid4()),
        name="test",
        nodes=(node_a, node_b),
        edges=tuple(edges),
    )
