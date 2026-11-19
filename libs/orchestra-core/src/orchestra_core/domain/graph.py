"""DAG 校验与构图。

⚠️ **用 Kahn 算法（入度法）检测环** —— 不是 DFS。

    Kahn：反复取「入度为 0」的节点。
         如果处理完所有节点还有剩余 → 剩下的就是环里的节点。

    DFS：递归标记 visiting。
          图大时可能爆栈，而且要额外处理「已访问」和「在栈上」两种状态。

Kahn 的好处：**顺便算出拓扑序**（执行顺序），不用算两遍。
"""

from __future__ import annotations

from collections import defaultdict

from orchestra_core.domain.entities import Flow, FlowGraph
from orchestra_core.domain.errors import CycleDetectedError, FlowValidationError, NodeNotFoundError
from orchestra_core.domain.value_objects import NodeId

__all__ = [
    "build_graph",
    "find_cycle",
    "topological_sort",
]


def build_graph(flow: Flow) -> FlowGraph:
    """校验并构建流程图。

    ⚠️ **校验不通过直接抛异常** ——
       不返回一个「可能无效」的图。
       半个有效的图比没有图更危险。

    :param flow: 流程定义
    :raises FlowValidationError: 校验失败
    :raises CycleDetectedError: 有环
    """
    _validate_unique_node_ids(flow)
    _validate_edges_reference_existing_nodes(flow)

    order = topological_sort(flow)
    dependencies = _build_dependencies(flow)
    dependents = _build_dependents(flow)

    return FlowGraph(
        flow=flow,
        topological_order=order,
        dependencies=dependencies,
        dependents=dependents,
    )


def _validate_unique_node_ids(flow: Flow) -> None:
    """节点 ID 必须唯一。

    ⚠️ 重复 ID 会让依赖关系算错 —— 两个同名节点，
       一个完成了另一个还在等。
    """
    seen: set[NodeId] = set()
    for node in flow.nodes:
        if node.node_id in seen:
            raise FlowValidationError(
                f"节点 ID 重复：{node.node_id}",
                node_id=node.node_id,
            )
        seen.add(node.node_id)


def _validate_edges_reference_existing_nodes(flow: Flow) -> None:
    """边引用的节点必须存在。"""
    node_ids = {node.node_id for node in flow.nodes}

    for edge in flow.edges:
        if edge.source_node not in node_ids:
            raise NodeNotFoundError(
                f"边的源节点不存在：{edge.source_node}",
                node_id=edge.source_node,
            )
        if edge.target_node not in node_ids:
            raise NodeNotFoundError(
                f"边的目标节点不存在：{edge.target_node}",
                node_id=edge.target_node,
            )


def _build_dependencies(flow: Flow) -> dict[NodeId, frozenset[NodeId]]:
    """算每个节点的依赖（上游节点集合）。"""
    dependencies: dict[NodeId, set[NodeId]] = {node.node_id: set() for node in flow.nodes}

    for edge in flow.edges:
        # ⚠️ 自环也是环 —— Kahn 算法会自然检测到
        dependencies[edge.target_node].add(edge.source_node)

    return {node_id: frozenset(deps) for node_id, deps in dependencies.items()}


def _build_dependents(flow: Flow) -> dict[NodeId, tuple[NodeId, ...]]:
    """算每个节点的下游。"""
    dependents: dict[NodeId, list[NodeId]] = defaultdict(list)

    for edge in flow.edges:
        dependents[edge.source_node].append(edge.target_node)

    return {node_id: tuple(children) for node_id, children in dependents.items()}


def topological_sort(flow: Flow) -> tuple[NodeId, ...]:
    """拓扑排序（Kahn 算法）。

    :param flow: 流程定义
    :raises CycleDetectedError: 有环
    """
    dependencies = _build_dependencies(flow)
    dependents = _build_dependents(flow)

    # 入度
    in_degree: dict[NodeId, int] = {node_id: len(deps) for node_id, deps in dependencies.items()}

    # ⚠️ 用**节点在 flow 里的顺序**做初始队列 ——
    #    保证拓扑序是**确定的**。
    #    用 set 的话，同一张图每次排序结果可能不同，
    #    那会让「同样的流程跑出不一样的顺序」，没法复现。
    ready = [node.node_id for node in flow.nodes if in_degree.get(node.node_id, 0) == 0]

    order: list[NodeId] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)

        for child in dependents.get(node_id, ()):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)

    if len(order) != len(flow.nodes):
        # ⚠️ 有节点没能处理 —— 剩下的在环里
        remaining = [node.node_id for node in flow.nodes if node.node_id not in set(order)]
        raise CycleDetectedError(tuple(remaining))

    return tuple(order)


def find_cycle(flow: Flow) -> tuple[NodeId, ...]:
    """找出环上的节点。

    ⚠️ 与 `topological_sort` 不同 —— 这个**不抛异常**，返回空元组表示无环。
    给「流程编辑器」用：实时提示用户哪里成环了。

    :param flow: 流程定义
    :return: 环上的节点（无环则空元组）
    """
    dependencies = _build_dependencies(flow)
    dependents = _build_dependents(flow)

    in_degree: dict[NodeId, int] = {node_id: len(deps) for node_id, deps in dependencies.items()}

    ready = [node.node_id for node in flow.nodes if in_degree.get(node.node_id, 0) == 0]

    removed: set[NodeId] = set()
    while ready:
        node_id = ready.pop(0)
        removed.add(node_id)

        for child in dependents.get(node_id, ()):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)

    # ⚠️ 剩下的节点里，真正在环上的是「互相可达」的那些 ——
    #    剩下的可能还包括「指向环」的节点（它们只是被卡住，不在环里）。
    remaining = [node.node_id for node in flow.nodes if node.node_id not in removed]
    if not remaining:
        return ()

    return _nodes_in_cycle(remaining, dependencies)


def _nodes_in_cycle(
    remaining: list[NodeId],
    dependencies: dict[NodeId, frozenset[NodeId]],
) -> tuple[NodeId, ...]:
    """从「被卡住的节点」里找出真正在环上的部分。"""
    remaining_set = set(remaining)

    # ⚠️ 在环上的节点：它依赖的某个节点也在环上
    #    （环外指向环的节点，它的依赖在环上，但它自己不在环里）
    in_cycle = {
        node_id
        for node_id in remaining
        if dependencies.get(node_id, frozenset()) & remaining_set
        and _has_cycle_path(node_id, dependencies, remaining_set)
    }

    if not in_cycle:
        # 兜底 —— 理论上不会到这，但空结果会让调用方困惑
        return tuple(remaining)

    # ⚠️ 按 flow 里的顺序返回 —— 可读且确定
    return tuple(node_id for node_id in remaining if node_id in in_cycle)


def _has_cycle_path(
    start: NodeId,
    dependencies: dict[NodeId, frozenset[NodeId]],
    candidates: set[NodeId],
) -> bool:
    """从 start 出发能不能回到 start。

    ⚠️ 用迭代 + 访问集合 —— 图可能有环，递归会无限循环。
    """
    stack = [start]
    visited: set[NodeId] = set()

    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)

        for dependency in dependencies.get(current, frozenset()):
            if dependency not in candidates:
                continue
            if dependency == start:
                return True
            stack.append(dependency)

    return False
