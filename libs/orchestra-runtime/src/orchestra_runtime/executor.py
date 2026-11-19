"""节点执行器协议 —— 节点怎么被执行的抽象。

⚠️ **执行器是插件的核心** ——
   每个节点类型对应一个执行器实现。
   新增节点 = 新增一个类，不用改调度器。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from orchestra_core.domain.entities import ErrorInfo, Node, Usage

__all__ = [
    "ExecutionContext",
    "NodeExecutionResult",
    "NodeExecutor",
]


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """执行上下文 —— 节点执行时能看到的外部世界。

    ⚠️ **用 dataclass 打包** —— 节点执行器需要的依赖不少，
    一堆参数会让签名没法看。
    """

    #: 运行 ID
    run_id: Any
    #: 流程 ID
    flow_id: Any
    #: 变量上下文（流程的输入变量 + 中间结果）
    variables: dict[str, Any] = field(default_factory=dict)
    #: 成本上限（美元）—— 超过就该停
    budget_limit: float | None = None
    #: 已消耗成本
    spent: float = 0.0
    #: 取消信号 —— 节点执行中要能响应取消
    is_cancelled: bool = False

    def remaining_budget(self) -> float | None:
        """剩余预算。

        :return: None 表示没有上限
        """
        if self.budget_limit is None:
            return None
        return max(0.0, self.budget_limit - self.spent)


@dataclass(frozen=True, slots=True)
class NodeExecutionResult:
    """节点执行结果。"""

    #: 输出值（按端口名）
    outputs: dict[str, Any] = field(default_factory=dict)
    #: 用量
    usage: Usage = field(default_factory=Usage)
    #: 错误 —— 非 None 表示失败
    error: ErrorInfo | None = None
    #: 是否被跳过（条件分支未命中）
    skipped: bool = False

    @property
    def succeeded(self) -> bool:
        """是否成功。"""
        return self.error is None and not self.skipped


@runtime_checkable
class NodeExecutor(Protocol):
    """节点执行器协议。

    ⚠️ **一个实现对应一个节点类型** ——
       调度器按 `node_type` 找执行器，找不到就报「节点类型未注册」。
    """

    @property
    def node_type(self) -> str:
        """处理的节点类型。"""
        ...

    async def execute(
        self,
        node: Node,
        inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> NodeExecutionResult:
        """执行节点。

        :param node: 节点定义
        :param inputs: 输入值（按端口名）
        :param ctx: 执行上下文
        """
        ...

    async def stream(
        self,
        node: Node,
        inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> AsyncIterator[Any]:
        """流式执行（可选）。

        ⚠️ **默认实现是不流式** ——
           多数节点（merge / branch / tool）没有流式概念。
           LLM 节点才需要。
        """
        result = await self.execute(node, inputs, ctx)
        yield result
