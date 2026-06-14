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

    def __repr__(self) -> str:
        return f"<Tool name={self.name!r} desc={self.description!r}>"
