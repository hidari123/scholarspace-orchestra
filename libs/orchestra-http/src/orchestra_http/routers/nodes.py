"""节点市场路由 —— 列出可用的节点类型。

⚠️ **只返回元数据，不返回实现** ——
   前端要的是「有哪些节点、参数是什么」，不是执行器代码。
"""

from __future__ import annotations

from fastapi import APIRouter

from orchestra_http.schemas import NodeSpecResponse, PortSpecResponse

__all__ = ["router", "set_registry"]

router = APIRouter(prefix="/api/v1/orchestra/nodes", tags=["nodes"])

#: 节点注册表 —— 由 app 工厂注入。
#:
#: ⚠️ **模块级变量而不是依赖注入** ——
#:    注册表在应用启动时构建一次，之后不变。
#:    用依赖注入反而绕（每次请求都从容器里取同一个东西）。
_registry: object | None = None


def set_registry(registry: object) -> None:
    """设置节点注册表。

    ⚠️ 在 `create_app()` 里调用一次。
    """
    global _registry  # noqa: PLW0603
    _registry = registry


@router.get("", response_model=list[NodeSpecResponse])
async def list_nodes() -> list[NodeSpecResponse]:
    """列出全部可用节点。

    ⚠️ **按分类 + 排序输出** —— 前端节点市场的展示顺序。
    """
    if _registry is None:
        return []

    specs = _registry.all_specs()  # type: ignore[attr-defined]

    return [
        NodeSpecResponse(
            type=spec.type,
            label_key=spec.label_key,
            category=spec.category,
            description_key=spec.description_key,
            input_ports=[
                PortSpecResponse(
                    name=port.name,
                    type=port.type,
                    required=port.required,
                    label_key=port.label_key,
                )
                for port in spec.input_ports
            ],
            output_ports=[
                PortSpecResponse(
                    name=port.name,
                    type=port.type,
                    required=port.required,
                    label_key=port.label_key,
                )
                for port in spec.output_ports
            ],
            icon=spec.icon,
            category_order=spec.category_order,
            config_schema=spec.config_schema,
        )
        for spec in specs
    ]


@router.get("/categories", response_model=list[str])
async def list_categories() -> list[str]:
    """列出节点分类。"""
    if _registry is None:
        return []

    categories = {spec.category for spec in _registry.all_specs()}  # type: ignore[attr-defined]
    return sorted(categories)
