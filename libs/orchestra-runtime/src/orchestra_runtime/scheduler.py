"""DAG 调度器 —— 按拓扑序执行流程图。

⚠️ **调度器的三个核心职责**：

    ① 决定「下一个执行谁」—— 就绪节点里挑
    ② 把上游输出接到下游输入 —— 数据绑定
    ③ 控制并发与重试 —— 别把下游打爆

---

## 并发策略

⚠️ **同一批就绪的节点并发执行** ——
   菱形图里 B 和 C 互不依赖，串行执行浪费一半时间。

⚠️ 但**并发度有上限** —— 无限制并发会把下游 API 打爆。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from orchestra_core.domain.entities import (
    ErrorInfo,
    FlowGraph,
    Node,
    NodeRun,
    Run,
    Usage,
)
from orchestra_core.domain.run_status import RunStatus
from orchestra_core.domain.value_objects import NodeId

from orchestra_runtime.executor import (
    ExecutionContext,
    NodeExecutionResult,
    NodeExecutor,
)

__all__ = [
    "NodeExecutorNotFoundError",
    "RetryPolicy",
    "Scheduler",
]


class NodeExecutorNotFoundError(LookupError):
    """节点类型没有注册执行器。"""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """重试策略。

    ⚠️ **只重试「可重试」的错误** ——
       参数错、权限不足重试一万次也没用。
    """

    #: 最大尝试次数
    max_attempts: int = 1
    #: 基础延迟（秒）
    base_delay: float = 0.5
    #: 指数退避倍数
    backoff: float = 2.0

    def delay_for(self, attempt: int) -> float:
        """第 N 次重试前等多久。

        :param attempt: 从 0 开始的尝试序号
        """
        return self.base_delay * (self.backoff**attempt)


@dataclass(slots=True)
class SchedulerState:
    """调度器状态。

    ⚠️ **可变** —— 这是执行过程中的累积状态。
    """

    #: 节点输出（节点 ID → 端口 → 值）
    outputs: dict[NodeId, dict[str, Any]] = field(default_factory=dict)
    #: 已完成的节点
    completed: set[NodeId] = field(default_factory=set)
    #: 失败的节点
    failed: set[NodeId] = field(default_factory=set)
    #: 跳过的节点
    skipped: set[NodeId] = field(default_factory=set)
    #: 节点执行记录
    node_runs: list[NodeRun] = field(default_factory=list)
    #: 累计用量
    usage: Usage = field(default_factory=Usage)
    #: 是否已取消
    cancelled: bool = False


class Scheduler:
    """DAG 调度器。"""

    def __init__(
        self,
        executors: dict[str, NodeExecutor],
        retry_policy: RetryPolicy | None = None,
        max_concurrency: int = 4,
        on_event: Callable[[str, NodeId | None, dict[str, Any]], None] | None = None,
    ) -> None:
        """初始化。

        :param executors: 节点类型 → 执行器
        :param retry_policy: 重试策略
        :param max_concurrency: 最大并发节点数
        :param on_event: 事件回调（用于发 SSE 事件）
        """
        self._executors = executors
        self._retry_policy = retry_policy or RetryPolicy()
        self._max_concurrency = max_concurrency
        self._on_event = on_event

    async def run(
        self,
        graph: FlowGraph,
        run: Run,
        variables: dict[str, Any] | None = None,
    ) -> Run:
        """执行整个流程。

        :param graph: 流程图
        :param run: 运行记录
        :param variables: 初始变量
        :return: 最终的运行记录
        """
        state = SchedulerState()
        context_vars = dict(variables or {})

        self._emit("run.started", None, {"run_id": str(run.run_id)})

        while True:
            # ⚠️ 取消检查 —— 每轮都要看
            if state.cancelled:
                return self._finish_cancelled(run, state)

            # ⚠️ **失败检查必须在「无就绪节点」检查之前** ——
            #    最后一个节点失败时，ready 会变空，
            #    先 break 就永远走不到失败分支 → run 被误判成成功。
            if state.failed:
                return self._finish_failed(run, state)

            ready = self._ready_nodes(graph, state)
            if not ready:
                break

            await self._run_batch(graph, ready, state, context_vars, run)

        # ⚠️ 还有节点没跑完 → 说明有依赖不可达（比如上游被跳过）
        #:
        # ⚠️ **必须明确报错，不能静默结束** ——
        #    用户以为流程跑完了，其实少了一半。
        #    「分支未命中」本身不是错误（正常业务逻辑），
        #    但「因为分支未命中导致下游跑不到」是**流程设计问题**，
        #    用户需要知道。
        unreachable = self._unreachable_nodes(graph, state)
        if unreachable:
            return self._finish_failed(
                run,
                state,
                error=ErrorInfo(
                    code="unreachable_nodes",
                    message=f"以下节点无法到达：{', '.join(unreachable)}",
                    failed_node_id=unreachable[0],
                ),
            )

        return self._finish_succeeded(run, state)

    def cancel(self, state: SchedulerState) -> None:
        """取消执行。

        ⚠️ **幂等** —— 重复取消不会报错。
        """
        state.cancelled = True

    # ── 内部方法 ──────────────────────────────────────

    def _ready_nodes(self, graph: FlowGraph, state: SchedulerState) -> tuple[NodeId, ...]:
        """取就绪节点。

        ⚠️ **被跳过的节点不算「完成」** ——
           它的下游永远等不到输入，是**不可达**而不是「就绪」。

           ⚠️ 之前把 `skipped` 也算进 done 了，导致：
               分支跳过 → 下游「就绪」→ 下游拿到 None 输入照样跑
           结果是下游拿着空数据产出了看似正常的结果 ——
           **静默产出垃圾**，比报错危险得多。
        """
        done = state.completed
        ready = graph.ready_nodes(frozenset(done))

        # ⚠️ 已失败的节点不再执行
        # ⚠️ **已跳过的节点也不再执行** ——
        #    它不算「完成」，所以 `ready_nodes` 会一直把它返回，
        #    循环就停不下来了（死循环）。
        return tuple(
            node_id
            for node_id in ready
            if node_id not in state.failed and node_id not in state.skipped
        )

    def _unreachable_nodes(self, graph: FlowGraph, state: SchedulerState) -> tuple[NodeId, ...]:
        """找出永远跑不到的节点。

        ⚠️ 上游被跳过 → 下游永远等不到输入。
           这种要**明确报错**，不能静默结束 ——
           用户以为流程跑完了，其实少了一半。

        ⚠️ **skipped 不算完成** —— 与 `_ready_nodes` 保持一致。
           被跳过的节点的下游就是不可达的。
        """
        done = state.completed
        return tuple(
            node_id
            for node_id in graph.topological_order
            if node_id not in done
            and node_id not in state.failed
            and node_id not in state.skipped
            and not graph.dependencies.get(node_id, frozenset()) <= done
        )

    async def _run_batch(
        self,
        graph: FlowGraph,
        node_ids: tuple[NodeId, ...],
        state: SchedulerState,
        variables: dict[str, Any],
        run: Run,
    ) -> None:
        """执行一批节点（并发）。"""
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run_one(node_id: NodeId) -> None:
            async with semaphore:
                await self._run_node(graph, node_id, state, variables, run)

        await asyncio.gather(*(run_one(node_id) for node_id in node_ids))

    async def _run_node(
        self,
        graph: FlowGraph,
        node_id: NodeId,
        state: SchedulerState,
        variables: dict[str, Any],
        run: Run,
    ) -> None:
        """执行单个节点（含重试）。"""
        node = graph.node(node_id)
        executor = self._executors.get(node.node_type)

        if executor is None:
            # ⚠️ 节点类型没注册 → 明确失败，不是跳过
            state.failed.add(node_id)
            state.node_runs.append(
                NodeRun(
                    node_run_id=uuid4(),
                    run_id=run.run_id,
                    node_id=node_id,
                    status=RunStatus.FAILED,
                    error=ErrorInfo(
                        code="executor_not_found",
                        message=f"节点类型未注册执行器：{node.node_type}",
                        failed_node_id=node_id,
                    ),
                )
            )
            return

        inputs = self._bind_inputs(node, state, variables)

        context = ExecutionContext(
            run_id=run.run_id,
            flow_id=run.flow_id,
            variables=variables,
            budget_limit=None,
            spent=state.usage.cost,
        )

        self._emit("node.started", node_id, {"node_id": node_id})

        result = await self._execute_with_retry(executor, node, inputs, context)

        state.usage = state.usage.add(result.usage)

        # ⚠️ **补上 failed_node_id** ——
        #    执行器返回的错误通常不知道自己在哪个节点上，
        #    调度器知道。不补的话用户只看到「失败了」，
        #    不知道是哪个节点 —— 得自己一个个节点查。
        error = result.error
        if error is not None and error.failed_node_id is None:
            error = replace(error, failed_node_id=node_id)

        state.node_runs.append(
            NodeRun(
                node_run_id=uuid4(),
                run_id=run.run_id,
                node_id=node_id,
                status=(
                    RunStatus.SKIPPED
                    if result.skipped
                    else RunStatus.SUCCEEDED
                    if error is None
                    else RunStatus.FAILED
                ),
                inputs=inputs,
                outputs=result.outputs,
                error=error,
                usage=result.usage,
            )
        )

        if error is not None:
            state.failed.add(node_id)
            self._emit(
                "node.failed",
                node_id,
                {"node_id": node_id, "error": error.message},
            )
            return

        if result.skipped:
            state.skipped.add(node_id)
            self._emit("node.skipped", node_id, {"node_id": node_id})
            return

        # ⚠️ 输出要存下来 —— 下游要取
        state.outputs[node_id] = result.outputs
        state.completed.add(node_id)
        self._emit("node.succeeded", node_id, {"node_id": node_id})

    async def _execute_with_retry(
        self,
        executor: NodeExecutor,
        node: Node,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeExecutionResult:
        """执行节点，失败时按策略重试。"""
        last_result: NodeExecutionResult | None = None

        for attempt in range(self._retry_policy.max_attempts):
            last_result = await executor.execute(node, inputs, context)

            if last_result.error is None:
                return last_result

            # ⚠️ 不可重试的错误 → 立刻返回
            if not self._is_retryable(last_result.error):
                return last_result

            if attempt < self._retry_policy.max_attempts - 1:
                await asyncio.sleep(self._retry_policy.delay_for(attempt))

        # ⚠️ mypy 需要明确的返回
        return last_result or NodeExecutionResult(
            error=ErrorInfo(code="unknown", message="节点执行失败")
        )

    def _is_retryable(self, error: ErrorInfo) -> bool:
        """错误是否可重试。

        ⚠️ **默认不可重试** ——
           盲目重试会烧钱（LLM 节点尤其）。
           节点执行器要自己声明可重试。
        """
        return error.code.startswith("retryable:")

    def _bind_inputs(
        self,
        node: Node,
        state: SchedulerState,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """绑定上游输出到节点输入。

        ⚠️ **三种来源**：
            ① 上游节点的输出（通过 PortBinding）
            ② 流程的初始变量（config 里引用 `${var}`）
            ③ 常量（config 里直接写的值）
        """
        inputs: dict[str, Any] = {}

        for binding in node.inputs:
            upstream = state.outputs.get(binding.source_node, {})
            value = upstream.get(binding.source_port)

            # ⚠️ 支持路径取值：`outputs.text` → 取 text 字段
            if binding.path and isinstance(value, dict):
                value = value.get(binding.path)

            inputs[binding.target_port] = value

        # ⚠️ config 里的 `${var}` 引用初始变量
        for key, value in node.config.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                var_name = value[2:-1]
                inputs[key] = variables.get(var_name)

        return inputs

    def _emit(self, event_type: str, node_id: NodeId | None, payload: dict[str, Any]) -> None:
        """发事件。"""
        if self._on_event is None:
            return
        self._on_event(event_type, node_id, payload)

    # ── 结束状态 ──────────────────────────────────────

    def _finish_succeeded(self, run: Run, state: SchedulerState) -> Run:
        """成功结束。"""
        self._emit("run.succeeded", None, {"run_id": str(run.run_id)})
        return _with_run_state(run, RunStatus.SUCCEEDED, state)

    def _finish_failed(
        self,
        run: Run,
        state: SchedulerState,
        error: ErrorInfo | None = None,
    ) -> Run:
        """失败结束。"""
        # ⚠️ 必须能定位到**具体节点** ——
        # 只说「流程失败」的话，用户得一个个节点查。
        final_error = error
        if final_error is None and state.failed:
            failed_node = next(iter(state.failed))
            for node_run in state.node_runs:
                if node_run.node_id == failed_node and node_run.error is not None:
                    final_error = node_run.error
                    break
            if final_error is None:
                final_error = ErrorInfo(
                    code="node_failed",
                    message=f"节点执行失败：{failed_node}",
                    failed_node_id=failed_node,
                )

        self._emit("run.failed", None, {"run_id": str(run.run_id)})
        return _with_run_state(run, RunStatus.FAILED, state, final_error)

    def _finish_cancelled(self, run: Run, state: SchedulerState) -> Run:
        """取消结束。"""
        self._emit("run.cancelled", None, {"run_id": str(run.run_id)})
        return _with_run_state(run, RunStatus.CANCELLED, state)


def _with_run_state(
    run: Run,
    status: RunStatus,
    state: SchedulerState,
    error: ErrorInfo | None = None,
) -> Run:
    """生成带最终状态的运行记录。

    ⚠️ 用 `dataclasses.replace` —— Run 是 frozen dataclass。
    """
    return replace(
        run,
        status=status,
        error=error,
        usage=state.usage,
        finished_at=datetime.now(UTC),
    )
