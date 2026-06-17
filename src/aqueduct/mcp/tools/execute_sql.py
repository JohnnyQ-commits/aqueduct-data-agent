"""MCP 工具接口 — 执行 SQL 查询。

实现：通过 数据平台 HTTP 接口执行 Hive SQL。
"""

from __future__ import annotations

import time
from typing import Any

from ..adapters.dp_client import DataPlatformAdapter
from ..tools import QueryResult


class HiveExecuteTool:
    """数据平台 Hive SQL 执行工具实现。"""

    def __init__(self) -> None:
        try:
            self.adapter = DataPlatformAdapter()
            self._init_error: str | None = None
        except RuntimeError as e:
            self.adapter = None
            self._init_error = str(e)

    def health_check(self) -> dict[str, Any]:
        """检查数据平台连接是否可用。

        Returns:
            dict: {"status": "ok"/"unavailable", "message": str}
        """
        if self.adapter is None:
            return {
                "status": "unavailable",
                "message": f"数据平台未配置: {self._init_error}",
            }
        try:
            self.adapter.execute_hive_query("SELECT 1")
            return {"status": "ok", "message": "连接正常"}
        except Exception as e:
            return {"status": "unavailable", "message": f"连接失败: {e}"}

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
                columns=[],
                rows=[],
                row_count=0,
                success=False,
                error=f"数据平台未配置: {self._init_error}",
            )

        try:
            start_time = time.time()
            result = self.adapter.execute_hive_query(sql)
            execution_time_ms = int((time.time() - start_time) * 1000)

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
                columns=[],
                rows=[],
                row_count=0,
                success=False,
                error=str(e),
            )
