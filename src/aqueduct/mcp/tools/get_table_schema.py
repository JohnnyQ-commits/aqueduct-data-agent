"""MCP 工具接口 — 查询表结构。

定义 get_table_schema 工具的抽象接口和具体实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from . import TableSchema


class GetTableSchemaTool(ABC):
    """查询表结构工具抽象基类。

    用户的数据平台 MCP Server 需要实现此接口。
    """

    @abstractmethod
    async def execute(self, database: str, table: str) -> TableSchema:
        """查询指定表的完整结构。

        Args:
            database: 库名。
            table: 表名。

        Returns:
            表结构信息（字段名、类型、注释、分区字段等）。

        Raises:
            TableNotFoundError: 表不存在时抛出。
        """
