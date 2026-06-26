"""工具层抽象基类。

所有原子工具（SQL 校验器、血缘解析器、成本预估器等）
必须实现 BaseTool。工具通过 `@register_tool` 装饰器注册到
全局注册中心，可被 DAG 节点动态调用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """任意工具执行的标准结果。"""

    success: bool  # 是否成功
    data: Any = None  # 输出数据
    error: str = ""  # 错误信息
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    """工具执行上下文 — 提供类型安全的参数访问。

    替代 ``**kwargs`` 的类型安全方案。工具可通过此 dataclass
    以属性方式访问参数，IDE 自动补全和类型检查均可生效。

    用法::

        ctx = ToolContext.from_kwargs(**kwargs)
        sql_file = ctx.get_str("sql_file")
        limit = ctx.get_int("limit", default=100)

    注意: 现有工具仍可使用 ``**kwargs``，ToolContext 为可选增强。
    """

    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_kwargs(cls, **kwargs: Any) -> ToolContext:
        """从关键字参构造上下文。"""
        return cls(params=kwargs)

    def get(self, key: str, default: Any = None) -> Any:
        """获取参数值。"""
        return self.params.get(key, default)

    def get_str(self, key: str, default: str = "") -> str:
        """获取字符串参数。"""
        val = self.params.get(key)
        return str(val) if val is not None else default

    def get_int(self, key: str, default: int = 0) -> int:
        """获取整数参数。"""
        val = self.params.get(key)
        try:
            return int(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """获取布尔参数。"""
        val = self.params.get(key)
        return bool(val) if val is not None else default

    def require(self, key: str) -> Any:
        """获取必填参数，缺失时抛出 KeyError。"""
        if key not in self.params:
            raise KeyError(f"工具缺少必填参数: {key}")
        return self.params[key]


class BaseTool(ABC):
    """所有原子工具的抽象基类。

    每个工具代表一个聚焦的能力：SQL 校验、血缘解析、成本预估、DQC 等。

    子类必须定义 `name`、`description` 和 `execute()`。
    可选覆盖 `validate()` 进行入参校验。
    """

    name: str = ""  # 工具唯一名称（注册键）
    description: str = ""  # 工具描述

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """以给定参数执行工具。

        Returns:
            ToolResult，包含成功标志、输出数据和元数据。
        """

    def validate(self, **kwargs: Any) -> list[str]:
        """执行前校验输入参数。

        Returns:
            错误消息列表。空列表表示校验通过。
        """
        return []

    def run_validated(self, **kwargs: Any) -> ToolResult:
        """先校验再执行。校验失败时抛出 ToolValidationError。

        Returns:
            ToolResult，包含成功标志、输出数据和元数据。

        Raises:
            ToolValidationError: 参数校验失败时抛出。
        """
        errors = self.validate(**kwargs)
        if errors:
            from ..exceptions import ToolValidationError

            raise ToolValidationError(f"工具 '{self.name}' 参数校验失败: {'; '.join(errors)}")
        return self.execute(**kwargs)

    def __repr__(self) -> str:
        return f"<Tool name={self.name!r} desc={self.description!r}>"
