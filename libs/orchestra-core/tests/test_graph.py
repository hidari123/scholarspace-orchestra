"""DAG 校验测试 —— 环检测 / 拓扑排序 / 依赖计算。

⚠️ 这是编排引擎最核心的测试 ——
   图错了，调度器就会让 run 永远卡住。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from orchestra_core.domain.entities import Edge, Flow, Node
from orchestra_core.domain.errors import (
    CycleDetectedError,
    FlowValidationError,
    NodeNotFoundError,
)
from orchestra_core.domain.graph import build_graph, find_cycle, topological_sort
from orchestra_core.domain.value_objects import EdgeId, FlowId, Position


#: 便捷构造节点。
def make_node(node_id: str) -> Node:
    """构造一个节点。"""
    return Node(
        node_id=node_id,  # type: ignore[arg-type]
        node_type="llm",
        position=Position(x=0.0, y=0.0),
    )


#: 便捷构造边。
def make_edge(source: str, target: str) -> Edge:
    """构造一条边。"""
    return Edge(
        edge_id=EdgeId(uuid4()),
        source_node=source,  # type: ignore[arg-type]
        source_port="out",
        target_node=target,  # type: ignore[arg-type]
        target_port="in",
    )


#: 便捷构造流程。
def make_flow(
    nodes: tuple[Node, ...],
    edges: tuple[Edge, ...],
) -> Flow:
    """构造一个流程。"""
    return Flow(flow_id=FlowId(uuid4()), name="test", nodes=nodes, edges=edges)


class TestTopologicalSort:
    """拓扑排序。"""

    def test_linear_graph(self) -> None:
        """线性图 A → B → C。"""
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c")),
            (make_edge("a", "b"), make_edge("b", "c")),
        )

        assert topological_sort(flow) == ("a", "b", "c")

    def test_diamond_graph(self) -> None:
        """菱形图 A → B, A → C, B → D, C → D。"""
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c"), make_node("d")),
            (
                make_edge("a", "b"),
                make_edge("a", "c"),
                make_edge("b", "d"),
                make_edge("c", "d"),
            ),
        )

        order = topological_sort(flow)

        assert order[0] == "a"
        assert order[-1] == "d"
        assert set(order[1:3]) == {"b", "c"}

    def test_independent_nodes(self) -> None:
        """独立节点（无边）。"""
        flow = make_flow((make_node("a"), make_node("b")), ())
        assert topological_sort(flow) == ("a", "b")

    def test_empty_graph(self) -> None:
        """空图。"""
        assert topological_sort(make_flow((), ())) == ()

    def test_order_is_deterministic(self) -> None:
        """⚠️ 拓扑序必须**确定**。

        同样的图每次排序结果不同的话，
        「同样的流程跑出不一样的顺序」——没法复现问题。
        """
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c")),
            (make_edge("a", "b"), make_edge("a", "c")),
        )

        first = topological_sort(flow)
        for _ in range(5):
            assert topological_sort(flow) == first

    def test_detects_cycle(self) -> None:
        """⚠️ 环必须被检测到。"""
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c")),
            (
                make_edge("a", "b"),
                make_edge("b", "c"),
                make_edge("c", "a"),
            ),
        )

        with pytest.raises(CycleDetectedError):
            topological_sort(flow)

    def test_detects_self_loop(self) -> None:
        """⚠️ 自环也是环。"""
        flow = make_flow((make_node("a"),), (make_edge("a", "a"),))

        with pytest.raises(CycleDetectedError):
            topological_sort(flow)


class TestFindCycle:
    """找环（不抛异常）。"""

    def test_no_cycle_returns_empty(self) -> None:
        """无环 → 空元组。"""
        flow = make_flow(
            (make_node("a"), make_node("b")),
            (make_edge("a", "b"),),
        )
        assert find_cycle(flow) == ()

    def test_finds_cycle_nodes(self) -> None:
        """找到环上的节点。"""
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c")),
            (
                make_edge("a", "b"),
                make_edge("b", "c"),
                make_edge("c", "a"),
            ),
        )

        cycle = find_cycle(flow)
        assert set(cycle) == {"a", "b", "c"}

    def test_excludes_nodes_only_pointing_into_cycle(self) -> None:
        """⚠️ 指向环的节点**不在环里**。

        X → A, A → B, B → A
        真正的环是 A,B；X 只是被卡住。
        """
        flow = make_flow(
            (make_node("x"), make_node("a"), make_node("b")),
            (
                make_edge("x", "a"),
                make_edge("a", "b"),
                make_edge("b", "a"),
            ),
        )

        cycle = find_cycle(flow)
        assert set(cycle) == {"a", "b"}
        assert "x" not in cycle


class TestBuildGraph:
    """构图。"""

    def test_builds_dependencies(self) -> None:
        """依赖关系。"""
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c")),
            (make_edge("a", "b"), make_edge("b", "c")),
        )

        graph = build_graph(flow)

        assert graph.dependencies["a"] == frozenset()
        assert graph.dependencies["b"] == frozenset({"a"})
        assert graph.dependencies["c"] == frozenset({"b"})

    def test_builds_dependents(self) -> None:
        """下游关系。"""
        flow = make_flow(
            (make_node("a"), make_node("b")),
            (make_edge("a", "b"),),
        )

        graph = build_graph(flow)

        assert graph.dependents["a"] == ("b",)
        assert graph.dependents.get("b", ()) == ()

    def test_rejects_duplicate_node_ids(self) -> None:
        """⚠️ 节点 ID 重复要报错。

        重复 ID 会让依赖算错 —— 两个同名节点，
        一个完成了另一个还在等。
        """
        flow = make_flow((make_node("a"), make_node("a")), ())

        with pytest.raises(FlowValidationError, match="节点 ID 重复"):
            build_graph(flow)

    def test_rejects_edge_to_missing_node(self) -> None:
        """⚠️ 边指向不存在的节点要报错。"""
        flow = make_flow((make_node("a"),), (make_edge("a", "ghost"),))

        with pytest.raises(NodeNotFoundError, match="ghost"):
            build_graph(flow)

    def test_node_lookup(self) -> None:
        """按 ID 取节点。"""
        flow = make_flow((make_node("a"),), ())
        graph = build_graph(flow)

        assert graph.node("a").node_id == "a"

    def test_node_lookup_raises_for_missing(self) -> None:
        """取不存在的节点报错。"""
        graph = build_graph(make_flow((make_node("a"),), ()))

        with pytest.raises(KeyError):
            graph.node("ghost")

    def test_ready_nodes_requires_dependencies_done(self) -> None:
        """⚠️ 就绪节点 = 依赖全部完成。"""
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c")),
            (make_edge("a", "b"), make_edge("b", "c")),
        )
        graph = build_graph(flow)

        assert graph.ready_nodes(frozenset()) == ("a",)
        assert graph.ready_nodes(frozenset({"a"})) == ("b",)
        assert graph.ready_nodes(frozenset({"a", "b"})) == ("c",)
        assert graph.ready_nodes(frozenset({"a", "b", "c"})) == ()

    def test_ready_nodes_returns_independent_in_topo_order(self) -> None:
        """多个就绪节点按拓扑序返回。"""
        flow = make_flow(
            (make_node("a"), make_node("b"), make_node("c")),
            (make_edge("a", "b"),),
        )
        graph = build_graph(flow)

        # a 和 c 都无依赖 —— 按拓扑序（a 先）返回
        assert graph.ready_nodes(frozenset()) == ("a", "c")
