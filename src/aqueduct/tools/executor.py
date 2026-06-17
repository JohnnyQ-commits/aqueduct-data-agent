"""SQL 执行工具 — SQLExecutorTool。

统一 SQL 执行入口，封装 HiveExecuteTool，提供 execute/health_check/execute_batch。
通过 @register_tool 注册到全局工具注册中心。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config.settings import get_settings
from ..mcp.tools.execute_sql import HiveExecuteTool
from .base import BaseTool, ToolResult
from .registry import register_tool

logger = logging.getLogger(__name__)


@register_tool
class SQLExecutorTool(BaseTool):
    """统一 SQL 执行入口 — 注册到全局工具注册中心。

    封装 HiveExecuteTool，提供：
    - execute(sql): 执行单条 SQL
    - health_check(): 检查数据平台连接
    - execute_batch(sqls): 批量执行，单条失败不中断

    支持参数:
        sql: SQL 语句（execute 必填）
        sqls: SQL 语句列表（execute_batch 必填）
        action: 操作类型 — "execute" / "health_check" / "execute_batch"
    """

    name = "executor"
    description = (
        "SQL 执行工具 — 执行单条 SQL、健康检查、批量执行。用于 DQC 验证和未来的 ETL 执行。"
    )

    def __init__(self) -> None:
        self._hive_tool = HiveExecuteTool()

    def execute(self, **kwargs: Any) -> ToolResult:
        """根据 action 参数分发到具体方法。"""
        action = kwargs.get("action", "execute")

        if action == "health_check":
            return self.health_check()
        elif action == "execute_batch":
            sqls = kwargs.get("sqls", [])
            return self.execute_batch(sqls=sqls)
        else:
            sql = kwargs.get("sql", "")
            if not sql:
                return ToolResult(success=False, error="缺少必填参数 sql")
            return self._execute_sql(sql)

    def health_check(self) -> ToolResult:
        """检查数据平台连接是否可用。"""
        settings = get_settings()
        if not settings.execution_enabled:
            return ToolResult(
                success=False,
                data={
                    "status": "unavailable",
                    "message": "执行能力已禁用 (execution_enabled=false)",
                },
            )

        result = self._hive_tool.health_check()
        is_ok = result["status"] == "ok"
        return ToolResult(
            success=is_ok,
            data=result,
        )

    def execute_batch(self, *, sqls: list[str]) -> ToolResult:
        """批量执行多条 SQL，单条失败不中断。"""
        if not sqls:
            return ToolResult(
                success=True,
                data={
                    "results": [],
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "total_time_ms": 0,
                },
            )

        results: list[dict[str, Any]] = []
        passed = 0
        failed = 0
        batch_start = time.time()

        for idx, sql in enumerate(sqls):
            start = time.time()
            qr = self._hive_tool.execute(sql)
            elapsed_ms = int((time.time() - start) * 1000)

            if qr.success:
                passed += 1
                results.append(
                    {
                        "index": idx,
                        "success": True,
                        "rows": qr.rows,
                        "row_count": qr.row_count,
                        "time_ms": elapsed_ms,
                    }
                )
            else:
                failed += 1
                results.append(
                    {
                        "index": idx,
                        "success": False,
                        "error": qr.error,
                        "time_ms": elapsed_ms,
                    }
                )

        total_time_ms = int((time.time() - batch_start) * 1000)

        return ToolResult(
            success=True,
            data={
                "results": results,
                "total": len(sqls),
                "passed": passed,
                "failed": failed,
                "total_time_ms": total_time_ms,
            },
        )

    def _execute_sql(self, sql: str) -> ToolResult:
        """执行单条 SQL。"""
        settings = get_settings()
        if not settings.execution_enabled:
            return ToolResult(
                success=False,
                error="执行能力已禁用 (execution_enabled=false)",
            )

        start = time.time()
        qr = self._hive_tool.execute(sql, limit=settings.execution_max_rows)
        elapsed_ms = int((time.time() - start) * 1000)

        if not qr.success:
            return ToolResult(success=False, error=qr.error)

        return ToolResult(
            success=True,
            data={
                "rows": qr.rows,
                "row_count": qr.row_count,
                "execution_time_ms": elapsed_ms,
                "sql_preview": sql[:200],
            },
        )

    def validate(self, **kwargs: Any) -> list[str]:
        """校验输入参数。"""
        errors: list[str] = []
        action = kwargs.get("action", "execute")
        if action == "execute" and not kwargs.get("sql"):
            errors.append("action=execute 时缺少必填参数: sql")
        if action == "execute_batch" and not kwargs.get("sqls"):
            errors.append("action=execute_batch 时缺少必填参数: sqls")
        return errors
