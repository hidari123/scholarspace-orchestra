"""DAG 调度器测试。

⚠️ 重点验证：
    ① 执行顺序符合拓扑序
    ② 数据绑定正确（上游输出接到下游输入）
    ③ 失败传播（一个节点失败 → run 失败，能定位到节点）
    ④ 跳过传播（分支跳过 → 下游不可达 → 明确报错）
    ⑤ 重试只对可重试错误
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from orchestra_core.domain.entities import (
    Edge,
    ErrorInfo,
    Flow,
    Node,
    PortBinding,
    Run,
    Usage,
)
from orchestra_core.domain.graph import build_graph
from orchestra_core.domain.run_status import RunStatus
from orchestra_core.domain.value_objects import EdgeId, FlowId, Position, RunId

from orchestra_runtime.executor import (
    ExecutionContext,
    NodeExecutionResult,
    NodeExecutor,
)
from orchestra_runtime.scheduler import RetryPolicy, Scheduler, SchedulerState

# ══════════════════════════════════════════════════════════
# 测试用执行器
# ══════════════════════════════════════════════════════════


class FakeExecutor(NodeExecutor):
    """假执行器 —— 按配置返回结果。"""

    def __init__(
        self,
        node_type: str,
        outputs: dict[str, object] | None = None,
        error: ErrorInfo | None = None,
        skipped: bool = False,
        delay: float = 0.0,
    ) -> None:
        self._node_type = node_type
        self._outputs = outputs or {}
        self._error = error
        self._skipped = skipped
        self._delay = delay
        #: 执行记录（输入 + 顺序）
        self.calls: list[dict[str, object]] = []

    @property
    def node_type(self) -> str:
        """节点类型。"""
        return self._node_type

    async def execute(
        self,
        node: Node,
        inputs: dict[str, object],
        ctx: ExecutionContext,
    ) -> NodeExecutionResult:
        """记录并返回预设结果。"""
        if self._delay:
            await asyncio.sleep(self._delay)

        del ctx  # ⚠️ 显式标记未使用 —— 避免 ARG002
        self.calls.append({"node_id": node.node_id, "inputs": inputs})

        return NodeExecutionResult(
            outputs=dict(self._outputs),
            usage=Usage(prompt_tokens=10, completion_tokens=5, cost=0.01, duration_ms=1),
            error=self._error,
            skipped=self._skipped,
        )


# ══════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════


def make_node(
    node_id: str,
    node_type: str = "llm",
    bindings: tuple[PortBinding, ...] = (),
    config: dict[str, object] | None = None,
) -> Node:
    """构造节点。"""
    return Node(
        node_id=node_id,  # type: ignore[arg-type]
        node_type=node_type,
        position=Position(x=0.0, y=0.0),
        config=config or {},
        inputs=bindings,
    )


def make_edge(source: str, target: str) -> Edge:
    """构造边。"""
    return Edge(
        edge_id=EdgeId(uuid4()),
        source_node=source,  # type: ignore[arg-type]
        source_port="out",
        target_node=target,  # type: ignore[arg-type]
        target_port="in",
    )


def make_flow(nodes: tuple[Node, ...], edges: tuple[Edge, ...]) -> Flow:
    """构造流程。"""
    return Flow(flow_id=FlowId(uuid4()), name="test", nodes=nodes, edges=edges)


def make_run() -> Run:
    """构造运行记录。"""
    return Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))


def binding(source: str, target_port: str = "in") -> PortBinding:
    """构造端口绑定。"""
    return PortBinding(
        target_port=target_port,
        source_node=source,  # type: ignore[arg-type]
        source_port="out",
    )


class TestExecutionOrder:
    """执行顺序。"""

    @pytest.mark.asyncio
    async def test_linear_order(self) -> None:
        """线性图按顺序执行。"""
        flow = make_flow(
            (
                make_node("a", node_type="up"),
                make_node("b", node_type="down", bindings=(binding("a"),)),
            ),
            (make_edge("a", "b"),),
        )

        up = FakeExecutor("up", outputs={"out": "x"})
        down = FakeExecutor("down")
        scheduler = Scheduler({"up": up, "down": down})
        result = await scheduler.run(build_graph(flow), make_run())

        assert result.status is RunStatus.SUCCEEDED
        # ⚠️ 两个节点都执行了，且按拓扑序
        assert [call["node_id"] for call in up.calls + down.calls] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_diamond_both_branches_run(self) -> None:
        """⚠️ 菱形图的两条分支都要跑。"""
        # ⚠️ 每个节点用不同的 node_type ——
        #    调度器按类型找执行器，同类型只能有一个实例。
        flow = make_flow(
            (
                make_node("a", node_type="t_a"),
                make_node("b", node_type="t_b", bindings=(binding("a"),)),
                make_node("c", node_type="t_c", bindings=(binding("a"),)),
                make_node("d", node_type="t_d", bindings=(binding("b"), binding("c"))),
            ),
            (
                make_edge("a", "b"),
                make_edge("a", "c"),
                make_edge("b", "d"),
                make_edge("c", "d"),
            ),
        )

        executors = {f"t_{name}": FakeExecutor(f"t_{name}") for name in ("a", "b", "c", "d")}
        scheduler = Scheduler(executors)
        result = await scheduler.run(build_graph(flow), make_run())

        assert result.status is RunStatus.SUCCEEDED
        # ⚠️ 四个节点都执行了
        assert sum(len(e.calls) for e in executors.values()) == 4

    @pytest.mark.asyncio
    async def test_independent_nodes_both_run(self) -> None:
        """无依赖的节点都跑。"""
        executor = FakeExecutor("llm")
        flow = make_flow((make_node("a"), make_node("b")), ())

        scheduler = Scheduler({"llm": executor})
        await scheduler.run(build_graph(flow), make_run())

        assert len(executor.calls) == 2


class TestDataBinding:
    """数据绑定。"""

    @pytest.mark.asyncio
    async def test_upstream_output_binds_to_downstream(self) -> None:
        """⚠️ 上游输出接到下游输入。

        ⚠️ **两个节点要用不同的 node_type** ——
        调度器按 node_type 找执行器，
        同一个类型只会拿到同一个执行器实例。
        """
        flow = make_flow(
            (
                make_node("a", node_type="up"),
                make_node("b", node_type="down", bindings=(binding("a"),)),
            ),
            (make_edge("a", "b"),),
        )

        up = FakeExecutor("up", outputs={"out": "来自A"})
        down = FakeExecutor("down")
        scheduler = Scheduler({"up": up, "down": down})
        await scheduler.run(build_graph(flow), make_run())

        assert down.calls[0]["inputs"] == {"in": "来自A"}

    @pytest.mark.asyncio
    async def test_variable_reference_resolved(self) -> None:
        """⚠️ config 里的 `${var}` 引用初始变量。"""
        flow = make_flow((make_node("a", config={"prompt": "${question}"}),), ())

        executor = FakeExecutor("llm")
        scheduler = Scheduler({"llm": executor})
        await scheduler.run(build_graph(flow), make_run(), variables={"question": "什么是DAG"})

        assert executor.calls[0]["inputs"] == {"prompt": "什么是DAG"}

    @pytest.mark.asyncio
    async def test_path_binding_extracts_field(self) -> None:
        """⚠️ 带 path 的绑定取特定字段。"""
        flow = make_flow(
            (
                make_node("a", node_type="up"),
                make_node(
                    "b",
                    node_type="down",
                    bindings=(
                        PortBinding(
                            target_port="in",
                            source_node="a",  # type: ignore[arg-type]
                            source_port="out",
                            path="text",
                        ),
                    ),
                ),
            ),
            (make_edge("a", "b"),),
        )

        up = FakeExecutor("up", outputs={"out": {"text": "内容", "meta": 1}})
        down = FakeExecutor("down")
        scheduler = Scheduler({"up": up, "down": down})
        await scheduler.run(build_graph(flow), make_run())

        assert down.calls[0]["inputs"] == {"in": "内容"}


class TestFailurePropagation:
    """失败传播。"""

    @pytest.mark.asyncio
    async def test_node_failure_fails_run(self) -> None:
        """⚠️ 节点失败 → run 失败。"""
        flow = make_flow(
            (
                make_node("a", node_type="ok"),
                make_node("b", node_type="bad"),
            ),
            (make_edge("a", "b"),),
        )

        ok = FakeExecutor("ok")
        bad = FakeExecutor("bad", error=ErrorInfo(code="boom", message="失败了"))
        scheduler = Scheduler({"ok": ok, "bad": bad})

        result = await scheduler.run(build_graph(flow), make_run())

        assert result.status is RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_failure_records_failed_node(self) -> None:
        """⚠️ 失败要能定位到具体节点。"""
        flow = make_flow(
            (
                make_node("a", node_type="ok"),
                make_node("b", node_type="bad"),
            ),
            (make_edge("a", "b"),),
        )

        ok = FakeExecutor("ok")
        bad = FakeExecutor("bad", error=ErrorInfo(code="boom", message="失败了"))
        scheduler = Scheduler({"ok": ok, "bad": bad})

        result = await scheduler.run(build_graph(flow), make_run())

        assert result.status is RunStatus.FAILED
        assert result.error is not None
        # ⚠️ 必须能定位到具体节点
        assert result.error.failed_node_id == "b"

    @pytest.mark.asyncio
    async def test_missing_executor_fails_run(self) -> None:
        """⚠️ 节点类型没注册执行器 → 明确失败，不是跳过。"""
        flow = make_flow((make_node("a", node_type="unregistered"),), ())

        scheduler = Scheduler({})
        result = await scheduler.run(build_graph(flow), make_run())

        assert result.status is RunStatus.FAILED
        assert result.error is not None
        assert result.error.code == "executor_not_found"

    @pytest.mark.asyncio
    async def test_downstream_not_executed_after_failure(self) -> None:
        """⚠️ 上游失败 → 下游不执行。"""
        flow = make_flow(
            (
                make_node("a", node_type="bad"),
                make_node("b", node_type="ok"),
            ),
            (make_edge("a", "b"),),
        )

        bad = FakeExecutor("bad", error=ErrorInfo(code="boom", message="x"))
        ok = FakeExecutor("ok")
        scheduler = Scheduler({"bad": bad, "ok": ok})

        await scheduler.run(build_graph(flow), make_run())

        assert len(ok.calls) == 0, "上游失败了，下游不该执行"


class TestSkipPropagation:
    """跳过传播。"""

    @pytest.mark.asyncio
    async def test_skipped_node_makes_downstream_unreachable(self) -> None:
        """⚠️ 分支跳过 → 下游不可达 → **明确报错**。

        不能静默结束 —— 用户以为流程跑完了，其实少了一半。
        """
        flow = make_flow(
            (
                make_node("a", node_type="branch"),
                make_node("b", node_type="ok", bindings=(binding("a"),)),
            ),
            (make_edge("a", "b"),),
        )

        branch = FakeExecutor("branch", skipped=True)
        ok = FakeExecutor("ok")
        scheduler = Scheduler({"branch": branch, "ok": ok})

        result = await scheduler.run(build_graph(flow), make_run())

        assert result.status is RunStatus.FAILED
        assert result.error is not None
        assert result.error.code == "unreachable_nodes"

    @pytest.mark.asyncio
    async def test_skipped_node_not_executed_downstream(self) -> None:
        """被跳过的节点，其下游不执行。"""
        flow = make_flow(
            (
                make_node("a", node_type="branch"),
                make_node("b", node_type="ok", bindings=(binding("a"),)),
            ),
            (make_edge("a", "b"),),
        )

        branch = FakeExecutor("branch", skipped=True)
        ok = FakeExecutor("ok")
        scheduler = Scheduler({"branch": branch, "ok": ok})

        await scheduler.run(build_graph(flow), make_run())

        assert len(ok.calls) == 0


class TestRetry:
    """重试。"""

    @pytest.mark.asyncio
    async def test_retries_retryable_error(self) -> None:
        """可重试的错误 → 重试。"""
        flow = make_flow((make_node("a", node_type="flaky"),), ())

        calls = {"count": 0}

        class FlakyExecutor(NodeExecutor):
            """前两次失败，第三次成功。"""

            @property
            def node_type(self) -> str:
                """节点类型。"""
                return "flaky"

            async def execute(
                self,
                node: Node,
                inputs: dict[str, object],
                ctx: ExecutionContext,
            ) -> NodeExecutionResult:
                """前两次可重试失败。"""
                del node, inputs, ctx  # ⚠️ 显式标记未使用 —— 避免 ARG002
                calls["count"] += 1
                if calls["count"] < 3:
                    return NodeExecutionResult(
                        error=ErrorInfo(code="retryable:timeout", message="超时")
                    )
                return NodeExecutionResult(outputs={"out": "成功"})

        scheduler = Scheduler(
            {"flaky": FlakyExecutor()},
            retry_policy=RetryPolicy(max_attempts=3, base_delay=0.0),
        )
        result = await scheduler.run(build_graph(flow), make_run())

        assert result.status is RunStatus.SUCCEEDED
        assert calls["count"] == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_error(self) -> None:
        """⚠️ 不可重试 → 不重试。"""
        flow = make_flow((make_node("a", node_type="bad"),), ())

        calls = {"count": 0}

        class BadExecutor(NodeExecutor):
            """一直失败，且不可重试。"""

            @property
            def node_type(self) -> str:
                """节点类型。"""
                return "bad"

            async def execute(
                self,
                node: Node,
                inputs: dict[str, object],
                ctx: ExecutionContext,
            ) -> NodeExecutionResult:
                """统计调用次数。"""
                del node, inputs, ctx  # ⚠️ 显式标记未使用 —— 避免 ARG002
                calls["count"] += 1
                return NodeExecutionResult(error=ErrorInfo(code="invalid_param", message="参数错"))

        scheduler = Scheduler(
            {"bad": BadExecutor()},
            retry_policy=RetryPolicy(max_attempts=3, base_delay=0.0),
        )
        await scheduler.run(build_graph(flow), make_run())

        assert calls["count"] == 1, "不可重试的错误被重试了 —— 会白白烧钱"


class TestEvents:
    """事件回调。"""

    @pytest.mark.asyncio
    async def test_emits_lifecycle_events(self) -> None:
        """⚠️ 发生命周期事件（SSE 用）。"""
        events: list[tuple[str, str | None]] = []

        flow = make_flow((make_node("a", node_type="llm"),), ())
        executor = FakeExecutor("llm")

        scheduler = Scheduler(
            {"llm": executor},
            on_event=lambda event_type, node_id, _payload: events.append((event_type, node_id)),
        )
        await scheduler.run(build_graph(flow), make_run())

        event_types = [event[0] for event in events]
        assert "run.started" in event_types
        assert "node.started" in event_types
        assert "node.succeeded" in event_types
        assert "run.succeeded" in event_types

    @pytest.mark.asyncio
    async def test_emits_failure_event(self) -> None:
        """失败也发事件。"""
        events: list[str] = []

        flow = make_flow((make_node("a", node_type="bad"),), ())
        bad = FakeExecutor("bad", error=ErrorInfo(code="boom", message="x"))

        scheduler = Scheduler(
            {"bad": bad},
            on_event=lambda event_type, _node_id, _payload: events.append(event_type),
        )
        await scheduler.run(build_graph(flow), make_run())

        assert "node.failed" in events
        assert "run.failed" in events


class TestUsage:
    """用量累计。"""

    @pytest.mark.asyncio
    async def test_usage_accumulates(self) -> None:
        """⚠️ 用量按节点累加。"""
        flow = make_flow(
            (
                make_node("a", node_type="llm"),
                make_node("b", node_type="llm", bindings=(binding("a"),)),
            ),
            (make_edge("a", "b"),),
        )

        executor = FakeExecutor("llm")  # 每个节点 10+5 token，0.01 美元
        scheduler = Scheduler({"llm": executor})
        result = await scheduler.run(build_graph(flow), make_run())

        assert result.usage.prompt_tokens == 20
        assert result.usage.completion_tokens == 10
        assert result.usage.cost == pytest.approx(0.02)


class TestCancel:
    """取消。"""

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self) -> None:
        """⚠️ 取消是幂等的。"""
        scheduler = Scheduler({})
        state = SchedulerState()

        scheduler.cancel(state)
        scheduler.cancel(state)

        assert state.cancelled
