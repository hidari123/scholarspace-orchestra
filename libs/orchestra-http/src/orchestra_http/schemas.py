"""请求/响应模型 —— Pydantic v2。

⚠️ **响应模型绝不含敏感字段**。

⚠️ 用 `extra="forbid"` —— 多余字段直接拒绝。
默认的「忽略」会让客户端以为字段生效了。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CreateFlowRequest",
    "CreateSessionRequest",
    "ErrorResponse",
    "FlowResponse",
    "NodeSpecResponse",
    "RunResponse",
    "SessionResponse",
    "UpdateFlowRequest",
]


class _StrictModel(BaseModel):
    """严格模式的基类。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorResponse(BaseModel):
    """统一错误响应。"""

    model_config = ConfigDict(extra="forbid")

    #: 错误码
    code: str = Field(description="错误码")
    #: 人类可读的消息
    message: str = Field(description="错误描述")


class PortSpecResponse(_StrictModel):
    """端口声明响应。"""

    name: str
    type: str
    required: bool = False
    label_key: str = ""


class NodeSpecResponse(_StrictModel):
    """节点规格响应 —— 节点市场的展示数据。"""

    type: str = Field(description="节点类型")
    label_key: str = Field(description="标签的 i18n key")
    category: str = Field(description="分类")
    description_key: str = Field(description="描述的 i18n key")
    input_ports: list[PortSpecResponse] = Field(default_factory=list)
    output_ports: list[PortSpecResponse] = Field(default_factory=list)
    icon: str = ""
    category_order: int = 0
    config_schema: dict[str, Any] = Field(default_factory=dict)


class FlowResponse(_StrictModel):
    """流程响应。

    ⚠️ **节点与边直接内联** —— 前端画布要一次拿全，
    分开请求会多几次往返。
    """

    flow_id: UUID
    name: str
    version: int
    description: str = ""
    access: str = "private"
    #: 节点（用原始 dict —— 节点结构由前端画布定义）
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    #: 边
    edges: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateFlowRequest(_StrictModel):
    """创建流程请求。"""

    name: str = Field(min_length=1, max_length=200, description="流程名")
    description: str = Field(default="", max_length=2000, description="描述")
    nodes: list[dict[str, Any]] = Field(default_factory=list, description="节点")
    edges: list[dict[str, Any]] = Field(default_factory=list, description="边")


class UpdateFlowRequest(_StrictModel):
    """更新流程请求。

    ⚠️ **不传 = 不改** —— 与「改成空」区分。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None


class RunResponse(_StrictModel):
    """运行响应。"""

    run_id: UUID
    flow_id: UUID
    flow_version: int
    status: str
    error: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CreateSessionRequest(_StrictModel):
    """创建会话请求。"""

    flow_id: UUID = Field(description="绑定的流程")
    title: str = Field(default="", max_length=200, description="标题")
    variables: dict[str, Any] = Field(default_factory=dict, description="初始变量")


class SessionResponse(_StrictModel):
    """会话响应。"""

    session_id: UUID
    flow_id: UUID
    title: str = ""
    variables: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
