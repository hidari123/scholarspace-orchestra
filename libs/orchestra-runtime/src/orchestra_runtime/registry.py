"""节点注册表 —— 插件机制的核心。

⚠️ **节点市场 = 注册表 + 声明式元数据**。

    节点类型            标签（i18n key）      图标
    ─────────────────────────────────────────────
    llm                 node.llm.label        🤖
    paper_insight       node.paper.label      📄

⚠️ **标签是 i18n key 不是硬编码文案** ——
   这样界面语言切换时节点名也跟着变。

⚠️ **注册表在导入时构建** ——
   插件通过 `register_all()` 注册自己的节点，
   调度器启动时调用一次。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestra_runtime.executor import NodeExecutor

__all__ = [
    "NodeCategory",
    "NodeRegistry",
    "NodeSpec",
    "PortSpec",
]


class NodeCategory:
    """节点分类。

    ⚠️ **用字符串常量而不是枚举** ——
       插件可以定义自己的分类。
    """

    MODEL = "model"
    TOOL = "tool"
    DATA = "data"
    CONTROL = "control"
    COLLABORATION = "collaboration"
    HUMAN = "human"
    ACADEMIC = "academic"


@dataclass(frozen=True, slots=True)
class PortSpec:
    """端口声明。"""

    #: 端口名
    name: str
    #: 端口类型（text / json / file / image / table / any）
    type: str
    #: 是否必填
    required: bool = False
    #: i18n key
    label_key: str = ""


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """节点规格 —— 节点市场的展示元数据。

    ⚠️ **不含实现** —— 实现是 `NodeExecutor`，
       元数据是给人看的（画布、节点市场）。
    """

    #: 节点类型（唯一）
    type: str
    #: i18n key —— 不是硬编码文案
    label_key: str
    #: 分类
    category: str
    #: 描述的 i18n key
    description_key: str
    #: 输入端口
    input_ports: tuple[PortSpec, ...] = ()
    #: 输出端口
    output_ports: tuple[PortSpec, ...] = ()
    #: 图标
    icon: str = ""
    #: 分类内的排序
    category_order: int = 0
    #: 配置的 JSON Schema —— 画布用它渲染表单
    config_schema: dict[str, Any] = field(default_factory=dict)


class NodeRegistry:
    """节点注册表。"""

    def __init__(self) -> None:
        self._specs: dict[str, NodeSpec] = {}
        self._executors: dict[str, NodeExecutor] = {}

    def register(self, spec: NodeSpec, executor: NodeExecutor) -> None:
        """注册一个节点。

        :param spec: 节点规格
        :param executor: 执行器实现
        :raises ValueError: 类型重复注册
        """
        if spec.type in self._specs:
            raise ValueError(f"节点类型已注册：{spec.type}")

        self._specs[spec.type] = spec
        self._executors[spec.type] = executor

    def get_executor(self, node_type: str) -> NodeExecutor | None:
        """取执行器。

        :return: None 表示没注册
        """
        return self._executors.get(node_type)

    def get_spec(self, node_type: str) -> NodeSpec | None:
        """取规格。"""
        return self._specs.get(node_type)

    def all_specs(self) -> tuple[NodeSpec, ...]:
        """全部规格（按分类 + 排序）。"""
        return tuple(
            sorted(
                self._specs.values(),
                key=lambda spec: (spec.category, spec.category_order, spec.type),
            )
        )

    def by_category(self, category: str) -> tuple[NodeSpec, ...]:
        """按分类取规格。"""
        return tuple(spec for spec in self.all_specs() if spec.category == category)

    def executor_map(self) -> dict[str, NodeExecutor]:
        """执行器映射 —— 传给调度器。"""
        return dict(self._executors)

    def __len__(self) -> int:
        """已注册的节点数。"""
        return len(self._specs)
