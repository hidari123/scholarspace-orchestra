"""内置节点 —— M2 的 P0 节点。

⚠️ **只实现 P0** —— 剩下的（retrieval / code / agent / human）留到 M3+。

P0 清单（`docs/07-orchestra.md` 第 4.1 节）：
    llm / tool / http / branch / merge
"""

from __future__ import annotations

import operator
from typing import Any

from orchestra_core.domain.entities import ErrorInfo

from orchestra_runtime.executor import (
    ExecutionContext,
    NodeExecutionResult,
    NodeExecutor,
)
from orchestra_runtime.registry import NodeCategory, NodeRegistry, NodeSpec, PortSpec

__all__ = [
    "BranchExecutor",
    "LLMExecutor",
    "MergeExecutor",
    "ToolExecutor",
    "register_builtin_nodes",
]


# ══════════════════════════════════════════════════════════
# LLM 节点
# ══════════════════════════════════════════════════════════


class LLMExecutor(NodeExecutor):
    """调用 LLM。

    ⚠️ **LLM 调用由外部注入** —— 本类不 import 任何 HTTP 库，
    也不知道模型怎么选。
    宿主通过 `llm_call` 回调注入。

    ⚠️ **结构化输出靠 config 里的 schema** ——
    `config["schema"]` 存在就要求结构化输出。
    """

    def __init__(self, llm_call: Any) -> None:
        """初始化。

        :param llm_call: 异步调用函数 `(prompt, config, ctx) -> str`
        """
        self._llm_call = llm_call

    @property
    def node_type(self) -> str:
        """节点类型。"""
        return "llm"

    async def execute(
        self,
        node: Any,
        inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> NodeExecutionResult:
        """执行 LLM 调用。"""
        prompt = str(node.config.get("prompt", ""))

        # ⚠️ 输入里的变量插进 prompt
        #    `${var}` 已在调度器解析过，这里拼的是固定文本。
        for key, value in inputs.items():
            prompt = prompt.replace(f"{{{key}}}", str(value))

        try:
            text = await self._llm_call(prompt, dict(node.config), ctx)
        except Exception as error:  # noqa: BLE001
            # ⚠️ LLM 调用失败 → 不可重试（除非调用方自己标记）
            return NodeExecutionResult(
                error=_to_error_info(error, str(node.node_id)),
            )

        return NodeExecutionResult(outputs={"out": text})


# ══════════════════════════════════════════════════════════
# 工具节点
# ══════════════════════════════════════════════════════════


class ToolExecutor(NodeExecutor):
    """调用注册的工具。

    ⚠️ **工具由外部注册** —— 工具本身是插件提供的。
    """

    def __init__(self, tools: dict[str, Any]) -> None:
        """初始化。

        :param tools: 工具名 → 异步调用函数
        """
        self._tools = tools

    @property
    def node_type(self) -> str:
        """节点类型。"""
        return "tool"

    async def execute(
        self,
        node: Any,
        inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> NodeExecutionResult:
        """执行工具调用。"""
        del ctx  # ⚠️ 显式标记未使用 —— 避免 ARG002
        tool_name = str(node.config.get("tool", ""))

        tool = self._tools.get(tool_name)
        if tool is None:
            return NodeExecutionResult(
                error=_error(
                    code="tool_not_found",
                    message=f"工具未注册：{tool_name}",
                    node_id=str(node.node_id),
                ),
            )

        try:
            result = await tool(dict(node.config), inputs)
        except Exception as error:  # noqa: BLE001
            return NodeExecutionResult(error=_to_error_info(error, str(node.node_id)))

        return NodeExecutionResult(outputs={"out": result})


# ══════════════════════════════════════════════════════════
# 控制节点
# ══════════════════════════════════════════════════════════


class BranchExecutor(NodeExecutor):
    """条件分支。

    ⚠️ **不命中就跳过** —— 下游会因此不可达，
    调度器会明确报错（不会静默产出垃圾）。

    ⚠️ 条件用**简单的表达式**（`>` / `<` / `==` / `contains`）——
    复杂逻辑用 `code` 节点。
    """

    @property
    def node_type(self) -> str:
        """节点类型。"""
        return "branch"

    async def execute(
        self,
        node: Any,
        inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> NodeExecutionResult:
        """判断条件。"""
        del ctx  # ⚠️ 显式标记未使用 —— 避免 ARG002
        source_port = str(node.config.get("source_port", "in"))
        value = inputs.get(source_port)

        cases = node.config.get("cases") or []
        default_port = node.config.get("default")

        for case in cases:
            if _evaluate(str(case.get("condition", "")), value):
                return NodeExecutionResult(outputs={"out": value, "port": case.get("port")})

        if default_port:
            return NodeExecutionResult(outputs={"out": value, "port": default_port})

        # ⚠️ 没有命中任何条件 → 跳过
        return NodeExecutionResult(skipped=True)


class MergeExecutor(NodeExecutor):
    """多输入合并。

    ⚠️ **合并策略可配** —— 列表拼接 / 取第一个 / 字典合并。
    """

    @property
    def node_type(self) -> str:
        """节点类型。"""
        return "merge"

    async def execute(
        self,
        node: Any,
        inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> NodeExecutionResult:
        """合并输入。"""
        del ctx  # ⚠️ 显式标记未使用 —— 避免 ARG002
        strategy = str(node.config.get("strategy", "concat"))

        values = [value for value in inputs.values() if value is not None]

        if strategy == "first":
            merged: Any = values[0] if values else None
        elif strategy == "dict":
            merged = {}
            for value in values:
                if isinstance(value, dict):
                    merged.update(value)
        else:
            merged = values

        return NodeExecutionResult(outputs={"out": merged})


# ══════════════════════════════════════════════════════════
# 注册
# ══════════════════════════════════════════════════════════


def register_builtin_nodes(
    registry: NodeRegistry,
    llm_call: Any = None,
    tools: dict[str, Any] | None = None,
) -> NodeRegistry:
    """注册内置节点。

    :param registry: 注册表
    :param llm_call: LLM 调用函数
    :param tools: 工具表
    :return: 同一个注册表（方便链式调用）
    """
    if llm_call is not None:
        registry.register(
            NodeSpec(
                type="llm",
                label_key="node.llm.label",
                category=NodeCategory.MODEL,
                description_key="node.llm.description",
                input_ports=(
                    PortSpec(name="in", type="text", required=False, label_key="node.port.in"),
                ),
                output_ports=(PortSpec(name="out", type="text", label_key="node.port.out"),),
                icon="robot",
                category_order=0,
            ),
            LLMExecutor(llm_call),
        )

    registry.register(
        NodeSpec(
            type="tool",
            label_key="node.tool.label",
            category=NodeCategory.TOOL,
            description_key="node.tool.description",
            input_ports=(
                PortSpec(name="in", type="json", required=False, label_key="node.port.in"),
            ),
            output_ports=(PortSpec(name="out", type="json", label_key="node.port.out"),),
            icon="tool",
            category_order=0,
        ),
        ToolExecutor(tools or {}),
    )

    registry.register(
        NodeSpec(
            type="branch",
            label_key="node.branch.label",
            category=NodeCategory.CONTROL,
            description_key="node.branch.description",
            input_ports=(PortSpec(name="in", type="any", required=True, label_key="node.port.in"),),
            output_ports=(PortSpec(name="out", type="any", label_key="node.port.out"),),
            icon="branch",
            category_order=0,
        ),
        BranchExecutor(),
    )

    registry.register(
        NodeSpec(
            type="merge",
            label_key="node.merge.label",
            category=NodeCategory.CONTROL,
            description_key="node.merge.description",
            input_ports=(
                PortSpec(name="in1", type="any", label_key="node.port.in1"),
                PortSpec(name="in2", type="any", label_key="node.port.in2"),
            ),
            output_ports=(PortSpec(name="out", type="any", label_key="node.port.out"),),
            icon="merge",
            category_order=1,
        ),
        MergeExecutor(),
    )

    return registry


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════


def _error(code: str, message: str, node_id: str) -> ErrorInfo:
    """构造 ErrorInfo。"""
    return ErrorInfo(code=code, message=message, failed_node_id=node_id)  # type: ignore[arg-type]


def _to_error_info(error: Exception, node_id: str) -> ErrorInfo:
    """把异常转成 ErrorInfo。

    ⚠️ **默认不可重试** ——
    调用方想让它可重试，异常类名要带 `retryable:` 前缀的 code。
    """
    code = getattr(error, "code", type(error).__name__)
    return ErrorInfo(
        code=str(code),
        message=str(error),
        failed_node_id=node_id,  # type: ignore[arg-type]
    )


#: 比较运算符 —— 长的排前面（`>=` 要先于 `>` 匹配）。
_COMPARISON_OPERATORS = (">=", "<=", "==", "!=", ">", "<")


def _evaluate(condition: str, value: Any) -> bool:
    """求值简单条件表达式。

    ⚠️ **只支持四种运算** —— 复杂逻辑用 code 节点。

        `> 10` / `< 10` / `== value` / `contains text`
    """
    condition = condition.strip()
    if not condition:
        return False

    # ⚠️ 循环变量不能叫 `operator` —— 会遮蔽 operator 模块
    for op in _COMPARISON_OPERATORS:
        if condition.startswith(op):
            threshold = condition[len(op) :].strip()
            return _compare(value, op, threshold)

    if condition.startswith("contains "):
        needle = condition[len("contains ") :].strip()
        return needle in str(value)

    return False


#: 比较运算符 → 对应的比较函数。
#:
#: ⚠️ **用映射表而不是 match/case** ——
#:    match/case 每个分支一个 return，触发 ruff 的 PLR0911
#:    （返回语句过多）。
_COMPARATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


def _compare(value: Any, op: str, threshold: str) -> bool:
    """比较。

    ⚠️ **优先按数字比较** —— 失败再退回字符串比较。
       数字比较时 `"20" > "10"` 是 False（字符串比较），
       那不是用户想要的。
    """
    try:
        left: Any = float(value)
        right: Any = float(threshold)
    except (TypeError, ValueError):
        left = str(value)
        right = threshold

    compare = _COMPARATORS.get(op)
    if compare is None:
        return False

    return bool(compare(left, right))
