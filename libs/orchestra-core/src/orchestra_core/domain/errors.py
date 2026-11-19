"""领域错误。

⚠️ **每个错误都要能定位到具体节点/边** ——
   用户改流程图时，「校验失败」但不知道哪里失败是最恼人的。
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestra_core.domain.value_objects import NodeId

__all__ = [
    "CycleDetectedError",
    "FlowValidationError",
    "NodeNotFoundError",
    "PortMismatch",
    "PortTypeMismatchError",
]


class FlowValidationError(ValueError):
    """流程校验失败。

    ⚠️ 带 `node_id` —— 让前端能高亮出问题的节点。
    """

    def __init__(self, message: str, node_id: NodeId | None = None) -> None:
        self.node_id = node_id
        super().__init__(message)


class NodeNotFoundError(FlowValidationError):
    """节点不存在。"""


class CycleDetectedError(FlowValidationError):
    """检测到环。

    ⚠️ **环是致命的** —— 拓扑排序不存在，调度器会永远等某些节点。
    """

    def __init__(self, cycle: tuple[NodeId, ...]) -> None:
        self.cycle = cycle
        # ⚠️ 打印成可读的环：A → B → C → A
        rendered = " → ".join(cycle)
        super().__init__(f"流程中存在环：{rendered}", node_id=cycle[0] if cycle else None)


class PortTypeMismatchError(FlowValidationError):
    """端口类型不匹配。

    ⚠️ **参数打包成 dataclass** ——
       6 个参数超过 ruff 的上限（5 个），
       而且这种「两端口对比」的信息天然成对出现。
    """

    def __init__(self, mismatch: PortMismatch) -> None:
        self.mismatch = mismatch

        super().__init__(
            f"端口类型不匹配：{mismatch.source_node}.{mismatch.source_port}"
            f"（{mismatch.source_type}）"
            f" → {mismatch.target_node}.{mismatch.target_port}"
            f"（{mismatch.target_type}）",
            node_id=mismatch.target_node,
        )


@dataclass(frozen=True, slots=True)
class PortMismatch:
    """端口类型不匹配的详情。"""

    #: 源节点
    source_node: NodeId
    #: 源端口
    source_port: str
    #: 源端口类型
    source_type: str
    #: 目标节点
    target_node: NodeId
    #: 目标端口
    target_port: str
    #: 目标端口类型
    target_type: str
