"""领域实体 —— Flow / Node / Edge / FlowGraph。

⚠️ **DAG 校验是这个模块的核心价值** —— 编排图里最容易出的错是**环**：

    A → B → C → A

    调度器按拓扑序执行，环会让某些节点永远等不到输入，
    run 永远卡住。**在保存时就拦住**，比运行时超时好得多。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from orchestra_core.domain.run_status import RunStatus
from orchestra_core.domain.value_objects import (
    AccessLevel,
    EdgeId,
    FlowId,
    FlowVersion,
    NodeId,
    Position,
    RunId,
)

__all__ = [
    "Edge",
    "ErrorInfo",
    "Flow",
    "FlowGraph",
    "Node",
    "NodeRun",
    "PortBinding",
    "PortSpec",
    "Run",
    "Usage",
]


@dataclass(frozen=True, slots=True)
class PortSpec:
    """端口声明。

    ⚠️ `type` 用字符串而不是枚举 —— 因为自定义节点可能定义自己的类型。
    """

    #: 端口名
    name: str
    #: 端口类型（text / json / file / image / table / any）
    type: str
    #: 是否必填
    required: bool = False
    #: i18n key —— 不是硬编码文案
    label_key: str = ""


@dataclass(frozen=True, slots=True)
class PortBinding:
    """端口绑定 —— 上游输出接到下游输入。"""

    #: 目标端口名
    target_port: str
    #: 上游节点 ID
    source_node: NodeId
    #: 上游端口名
    source_port: str
    #: 变量路径（可选）—— 从上游输出里取特定字段
    path: str | None = None


@dataclass(frozen=True, slots=True)
class Node:
    """流程节点。

    ⚠️ `node_id` 是**图内唯一**的 —— 不是全局唯一。
    同一个节点类型可以在一张图里出现多次（`llm_1` / `llm_2`）。
    """

    #: 图内唯一 ID
    node_id: NodeId
    #: 节点类型（llm / tool / branch / merge …）
    node_type: str
    #: 画布坐标
    position: Position
    #: 类型化配置
    config: dict[str, Any] = field(default_factory=dict)
    #: 上游数据绑定
    inputs: tuple[PortBinding, ...] = ()
    #: 输出端口声明
    outputs: tuple[PortSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class Edge:
    """边 —— 两个节点之间的连线。"""

    edge_id: EdgeId
    source_node: NodeId
    source_port: str
    target_node: NodeId
    target_port: str


@dataclass(frozen=True, slots=True)
class Flow:
    """流程定义。

    ⚠️ **不可变** —— 改了就是新版本（`version` +1）。
    正在运行的 run 引用的是特定版本，不会被改坏。
    """

    flow_id: FlowId
    #: 流程名
    name: str
    #: 版本号 —— 每次修改 +1
    #:
    #: ⚠️ 用 `default_factory` 而不是直接写 `FlowVersion(1)` ——
    #:    直接写在默认值位置的话，**类定义时只求值一次**，
    #:    所有 Flow 实例会共享同一个版本号对象。
    version: FlowVersion = field(default_factory=lambda: FlowVersion(1))
    #: 访问级别
    access: AccessLevel = AccessLevel.PRIVATE
    #: 节点
    nodes: tuple[Node, ...] = ()
    #: 边
    edges: tuple[Edge, ...] = ()
    #: 描述
    description: str = ""
    #: 创建时间
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: 更新时间
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """错误信息。

    ⚠️ `failed_node_id` —— **定位到具体节点**。
    只说「流程失败」的话，用户得自己一个个节点查。
    """

    #: 错误码
    code: str
    #: 消息
    message: str
    #: 失败节点
    failed_node_id: NodeId | None = None
    #: 附加数据
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Usage:
    """用量统计。

    ⚠️ **成本必须有** —— 成本闸门靠它判断要不要中止。
    """

    #: 输入 token
    prompt_tokens: int = 0
    #: 输出 token
    completion_tokens: int = 0
    #: 成本（美元）
    cost: float = 0.0
    #: 耗时（毫秒）
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        """总 token 数。"""
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: Usage) -> Usage:
        """累加用量。"""
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost=self.cost + other.cost,
            duration_ms=self.duration_ms + other.duration_ms,
        )


@dataclass(frozen=True, slots=True)
class Run:
    """一次流程运行。"""

    run_id: RunId
    flow_id: FlowId
    #: 引用的流程版本 —— 流程改了也不影响这次运行
    #:
    #: ⚠️ 同样用 `default_factory` —— 见 Flow.version 的说明
    flow_version: FlowVersion = field(default_factory=lambda: FlowVersion(1))
    #: 状态
    status: RunStatus = RunStatus.PENDING
    #: 输入变量
    input_vars: dict[str, Any] = field(default_factory=dict)
    #: 开始时间
    started_at: datetime | None = None
    #: 结束时间
    finished_at: datetime | None = None
    #: 错误
    error: ErrorInfo | None = None
    #: 用量
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class NodeRun:
    """单节点执行记录。"""

    node_run_id: Any
    run_id: RunId
    node_id: NodeId
    status: RunStatus = RunStatus.PENDING
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: ErrorInfo | None = None
    usage: Usage = field(default_factory=Usage)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FlowGraph:
    """流程图 —— 带拓扑序与依赖关系的节点集合。

    ⚠️ **不可变** —— 校验通过才构造出来。
    想要一个合法的图，不能先构造再校验（中间状态是坏的）。
    """

    flow: Flow
    #: 拓扑序（节点 ID 列表）—— 执行顺序
    topological_order: tuple[NodeId, ...]
    #: 节点 ID → 其依赖的上游节点 ID 集合
    dependencies: dict[NodeId, frozenset[NodeId]]
    #: 节点 ID → 直接下游
    dependents: dict[NodeId, tuple[NodeId, ...]]

    @property
    def nodes(self) -> tuple[Node, ...]:
        """所有节点。"""
        return self.flow.nodes

    def node(self, node_id: NodeId) -> Node:
        """按 ID 取节点。

        :raises KeyError: 节点不存在
        """
        for node in self.flow.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"节点不存在：{node_id}")

    def ready_nodes(self, completed: frozenset[NodeId]) -> tuple[NodeId, ...]:
        """取「依赖已满足」的节点。

        ⚠️ 按拓扑序返回 —— 保证同批就绪的节点按正确顺序执行。

        :param completed: 已完成的节点集合
        """
        return tuple(
            node_id
            for node_id in self.topological_order
            if node_id not in completed and self.dependencies.get(node_id, frozenset()) <= completed
        )
