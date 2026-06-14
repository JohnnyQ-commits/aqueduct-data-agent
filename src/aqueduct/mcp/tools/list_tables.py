"""MCP 工具接口 — 列出可用数据表。

定义 list_tables 工具的抽象接口和具体实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ListTablesTool(ABC):
    """列出可用数据表工具抽象基类。

    用户的数据平台 MCP Server 需要实现此接口。
    """

    @abstractmethod
    async def execute(self, database: str | None = None, keyword: str = "") -> list[str]:
        """列出数据平台中可用的表。

        Args:
            database: 库名过滤。None 表示列出所有库。
            keyword: 表名关键字过滤（模糊匹配）。

        Returns:
            表名列表，格式为 "database.table"。
        """
