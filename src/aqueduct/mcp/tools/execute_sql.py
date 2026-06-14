"""MCP 工具接口 — 执行 SQL 查询。

实现：通过 数据平台 HTTP 接口执行 Hive SQL。
"""

from __future__ import annotations

from ..adapters.dp_client import DataPlatformAdapter
from ..tools import QueryResult


class HiveExecuteTool:
    """数据平台 Hive SQL 执行工具实现。"""

    def __init__(self) -> None:
        try:
            self.adapter = DataPlatformAdapter()
        except RuntimeError as e:
            self.adapter = None
            self.init_error = str(e)

    def execute(self, sql: str, limit: int = 100) -> QueryResult:
        """执行 SQL 查询并返回结果。

        Args:
            sql: SQL 语句。
            limit: 返回行数限制。

        Returns:
            QueryResult 对象。
        """
        if self.adapter is None:
            return QueryResult(
                success=False,
                error=f"初始化失败: {self.init_error}",
            )

        try:
            result = self.adapter.execute_hive_query(sql)
            # 提取列名
            if result["data"]:
                columns = list(result["data"][0].keys())
                rows = [list(row.values()) for row in result["data"]]
            else:
                columns = []
                rows = []

            return QueryResult(
                columns=columns,
                rows=rows[:limit],
                row_count=result["row_count"],
                success=True,
            )
        except Exception as e:
            return QueryResult(
                success=False,
                error=str(e),
            )
