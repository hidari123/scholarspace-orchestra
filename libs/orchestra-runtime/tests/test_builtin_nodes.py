"""节点注册表与内置节点测试。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from orchestra_core.domain.entities import Node
from orchestra_core.domain.value_objects import Position

from orchestra_runtime.builtin_nodes import (
    BranchExecutor,
    MergeExecutor,
    ToolExecutor,
    register_builtin_nodes,
)
from orchestra_runtime.executor import ExecutionContext
from orchestra_runtime.registry import NodeCategory, NodeRegistry, NodeSpec


def make_node(node_id: str, node_type: str, config: dict[str, Any] | None = None) -> Node:
    """构造节点。"""
    return Node(
        node_id=node_id,  # type: ignore[arg-type]
        node_type=node_type,
        position=Position(x=0.0, y=0.0),
        config=config or {},
    )


def make_ctx() -> ExecutionContext:
    """构造执行上下文。"""
    return ExecutionContext(run_id=None, flow_id=None)


class TestNodeRegistry:
    """节点注册表。"""

    def test_register_adds_spec_and_executor(self) -> None:
        """注册节点。"""
        registry = NodeRegistry()
        executor = MergeExecutor()

        registry.register(
            NodeSpec(
                type="merge",
                label_key="node.merge.label",
                category=NodeCategory.CONTROL,
                description_key="node.merge.description",
            ),
            executor,
        )

        assert len(registry) == 1
        assert registry.get_spec("merge") is not None
        assert registry.get_executor("merge") is executor

    def test_register_duplicate_raises(self) -> None:
        """⚠️ 重复注册要报错 —— 静默覆盖会让「哪个插件赢」变成随机。"""
        registry = NodeRegistry()
        spec = NodeSpec(
            type="merge",
            label_key="a",
            category=NodeCategory.CONTROL,
            description_key="b",
        )

        registry.register(spec, MergeExecutor())

        with pytest.raises(ValueError, match="已注册"):
            registry.register(spec, MergeExecutor())

    def test_get_executor_returns_none_for_unknown(self) -> None:
        """未注册 → None。"""
        assert NodeRegistry().get_executor("ghost") is None

    def test_all_specs_sorted(self) -> None:
        """⚠️ 规格按分类 + 排序输出 —— 节点市场的展示顺序要稳定。"""
        registry = NodeRegistry()

        for node_type, category, order in (
            ("z", NodeCategory.TOOL, 0),
            ("a", NodeCategory.CONTROL, 1),
            ("b", NodeCategory.CONTROL, 0),
        ):
            registry.register(
                NodeSpec(
                    type=node_type,
                    label_key="x",
                    category=category,
                    description_key="y",
                    category_order=order,
                ),
                MergeExecutor(),
            )

        specs = registry.all_specs()
        assert [spec.type for spec in specs] == ["b", "a", "z"]

    def test_by_category(self) -> None:
        """按分类筛选。"""
        registry = register_builtin_nodes(NodeRegistry())

        control = registry.by_category(NodeCategory.CONTROL)
        assert {spec.type for spec in control} == {"branch", "merge"}

    def test_executor_map(self) -> None:
        """执行器映射。"""
        registry = register_builtin_nodes(NodeRegistry())
        executors = registry.executor_map()

        assert "branch" in executors
        assert "merge" in executors


class TestBuiltinNodes:
    """内置节点。"""

    def test_builtin_nodes_registered(self) -> None:
        """⚠️ P0 节点都注册了。"""
        registry = register_builtin_nodes(NodeRegistry())

        assert "branch" in registry.executor_map()
        assert "merge" in registry.executor_map()
        assert "tool" in registry.executor_map()

    def test_llm_node_calls_injected(self) -> None:
        """⚠️ LLM 调用由外部注入 —— 本包不绑后端。"""
        calls: list[str] = []

        async def llm_call(prompt: str, config: dict[str, Any], ctx: ExecutionContext) -> str:
            """假 LLM。"""
            calls.append(prompt)
            return "生成的内容"

        registry = register_builtin_nodes(NodeRegistry(), llm_call=llm_call)
        executor = registry.get_executor("llm")
        assert executor is not None

        result = asyncio_run(
            executor.execute(make_node("a", "llm", {"prompt": "你好"}), {}, make_ctx())
        )

        assert result.outputs["out"] == "生成的内容"
        assert calls == ["你好"]

    def test_tool_node_unknown_tool_fails(self) -> None:
        """⚠️ 工具没注册 → 明确失败。"""
        executor = ToolExecutor({})
        result = asyncio_run(
            executor.execute(make_node("a", "tool", {"tool": "ghost"}), {}, make_ctx())
        )

        assert result.error is not None
        assert result.error.code == "tool_not_found"

    def test_tool_node_calls_registered_tool(self) -> None:
        """调用已注册的工具。"""

        async def my_tool(config: dict[str, Any], inputs: dict[str, Any]) -> str:
            """假工具。"""
            return "工具结果"

        executor = ToolExecutor({"my_tool": my_tool})
        result = asyncio_run(
            executor.execute(make_node("a", "tool", {"tool": "my_tool"}), {}, make_ctx())
        )

        assert result.outputs["out"] == "工具结果"

    def test_branch_skips_when_no_match(self) -> None:
        """⚠️ 没命中条件 → 跳过。"""
        executor = BranchExecutor()
        result = asyncio_run(
            executor.execute(
                make_node("a", "branch", {"cases": [], "source_port": "in"}),
                {"in": 5},
                make_ctx(),
            )
        )

        assert result.skipped

    def test_branch_matches_condition(self) -> None:
        """命中条件。"""
        executor = BranchExecutor()
        result = asyncio_run(
            executor.execute(
                make_node(
                    "a",
                    "branch",
                    {
                        "source_port": "in",
                        "cases": [{"condition": "> 10", "port": "big"}],
                        "default": "small",
                    },
                ),
                {"in": 20},
                make_ctx(),
            )
        )

        assert not result.skipped
        assert result.outputs["port"] == "big"

    def test_branch_uses_default(self) -> None:
        """用默认分支。"""
        executor = BranchExecutor()
        result = asyncio_run(
            executor.execute(
                make_node(
                    "a",
                    "branch",
                    {
                        "source_port": "in",
                        "cases": [{"condition": "> 10", "port": "big"}],
                        "default": "small",
                    },
                ),
                {"in": 5},
                make_ctx(),
            )
        )

        assert result.outputs["port"] == "small"

    def test_merge_concat_strategy(self) -> None:
        """拼接策略。"""
        executor = MergeExecutor()
        result = asyncio_run(
            executor.execute(
                make_node("a", "merge", {"strategy": "concat"}), {"x": 1, "y": 2}, make_ctx()
            )
        )

        assert result.outputs["out"] == [1, 2]

    def test_merge_dict_strategy(self) -> None:
        """字典合并。"""
        executor = MergeExecutor()
        result = asyncio_run(
            executor.execute(
                make_node("a", "merge", {"strategy": "dict"}),
                {"x": {"a": 1}, "y": {"b": 2}},
                make_ctx(),
            )
        )

        assert result.outputs["out"] == {"a": 1, "b": 2}

    def test_merge_first_strategy(self) -> None:
        """取第一个。"""
        executor = MergeExecutor()
        result = asyncio_run(
            executor.execute(
                make_node("a", "merge", {"strategy": "first"}), {"x": 1, "y": 2}, make_ctx()
            )
        )

        assert result.outputs["out"] == 1


def asyncio_run(coro: Any) -> Any:
    """跑协程。

    ⚠️ 简单的同步包装 —— 测试里不想写一堆 async def。
    """
    return asyncio.run(coro)
