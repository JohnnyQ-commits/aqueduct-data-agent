"""工具注册中心 — 全局查找所有已注册的工具。

工具通过 `@register_tool` 装饰器注册。
使用 `get_tool(name)` 按名称获取工具实例。
"""

from __future__ import annotations

from ..exceptions import ToolNotFoundError
from .base import BaseTool

# 全局工具注册表：名称 → 工具类
_TOOL_REGISTRY: dict[str, type[BaseTool]] = {}


def register_tool(tool_cls: type[BaseTool]) -> type[BaseTool]:
    """装饰器：将工具类注册到全局注册表。

    用法:
        @register_tool
        class ValidatorTool(BaseTool):
            name = "validator"
            ...

    Args:
        tool_cls: BaseTool 的子类，必须定义非空的 `name` 属性。

    Returns:
        原类（不变），兼容装饰器链式使用。

    Raises:
        ValueError: name 为空或已存在同名工具时抛出。
    """
    if not tool_cls.name:
        raise ValueError(f"工具类 {tool_cls.__name__} 必须定义非空的 `name` 属性。")
    if tool_cls.name in _TOOL_REGISTRY:
        raise ValueError(
            f"工具 '{tool_cls.name}' 已注册。现有: {_TOOL_REGISTRY[tool_cls.name].__name__}"
        )
    _TOOL_REGISTRY[tool_cls.name] = tool_cls
    return tool_cls


def get_tool(name: str) -> BaseTool:
    """按名称获取工具实例。

    Args:
        name: 已注册的工具名称。

    Returns:
        工具类的新实例。

    Raises:
        ToolNotFoundError: 未找到该名称的工具时抛出。
    """
    if name not in _TOOL_REGISTRY:
        raise ToolNotFoundError(f"工具 '{name}' 未注册。可用工具: {list(_TOOL_REGISTRY.keys())}")
    return _TOOL_REGISTRY[name]()


def list_tools() -> list[str]:
    """返回所有已注册的工具名称列表。"""
    return list(_TOOL_REGISTRY.keys())


def is_tool_registered(name: str) -> bool:
    """检查工具是否已注册。"""
    return name in _TOOL_REGISTRY
