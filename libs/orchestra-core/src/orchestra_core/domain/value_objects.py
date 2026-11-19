"""值对象 —— 领域层的基础类型。

⚠️ **零外部依赖** —— 这一层可以被任何地方复用。

---

## 为什么用 NewType 而不是裸 str

`node_id: str` 与 `flow_id: str` 在类型层面无法区分 ——
把 flow_id 传给要 node_id 的函数，mypy 不会报错，运行时才发现。

`NewType` 给出**编译期**区分，运行时零开销。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType
from uuid import UUID

__all__ = [
    "AccessLevel",
    "EdgeId",
    "FlowId",
    "FlowVersion",
    "NodeId",
    "NodeRunId",
    "Position",
    "RunId",
    "SessionId",
]


#: 流程 ID
FlowId = NewType("FlowId", UUID)
#: 运行 ID
RunId = NewType("RunId", UUID)
#: 节点运行 ID
NodeRunId = NewType("NodeRunId", UUID)
#: 会话 ID
SessionId = NewType("SessionId", UUID)
#: 边 ID
EdgeId = NewType("EdgeId", UUID)

#: 节点 ID —— 图内唯一，如 'llm_1'
NodeId = NewType("NodeId", str)

#: 流程版本号
FlowVersion = NewType("FlowVersion", int)


@dataclass(frozen=True, slots=True)
class Position:
    """画布坐标。

    ⚠️ 浮点数 —— 前端画布用浮点坐标，服务端要能原样存回去。
    """

    x: float
    y: float


class AccessLevel(StrEnum):
    """访问级别。

    ⚠️ 流程可以是私有的、团队共享的、或公开的 ——
    公开的流程能被别人 fork。

    ⚠️ **用 `StrEnum` 而不是 dataclass + 字符串常量** ——
       dataclass 的类属性在 `__post_init__` 里是**实例属性**，
       `self not in (self.PRIVATE, ...)` 会拿实例去和字符串比，
       永远成立 —— 校验形同虚设。
       用枚举则天然只有三种取值。
    """

    #: 私有 —— 只有创建者能看
    PRIVATE = "private"
    #: 团队内共享
    TEAM = "team"
    #: 公开 —— 任何人都能看和 fork
    PUBLIC = "public"
