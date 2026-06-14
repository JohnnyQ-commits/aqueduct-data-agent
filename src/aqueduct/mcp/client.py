"""MCP Client SDK 封装。

封装标准 MCP Client 的连接、工具调用、生命周期管理。
用户通过 .mcp.json 配置连接自己的 MCP Server。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

from .config import MCPConfig
from .tools import (
    ColumnInfo,
    QueryResult,
    SQLError,
    TableNotFoundError,
    TableSchema,
)


class MCPClient:
    """MCP Client 实现。

    通过 stdio 协议连接到用户配置的 MCP Server，
    调用远程工具获取表结构、执行 SQL 等。
    """

    def __init__(self, config: MCPConfig | None = None, server_name: str | None = None) -> None:
        """初始化 MCP Client。

        Args:
            config: MCP 配置。未指定时自动加载 .mcp.json。
            server_name: 使用的 Server 名称。未指定时使用第一个可用的 Server。
        """
        self.config = config or MCPConfig()
        self.server_name = server_name or (
            self.config.list_servers()[0] if self.config.list_servers() else None
        )

        if self.server_name is None:
            raise RuntimeError("未配置 MCP Server。请创建 .mcp.json 配置文件。")

        self.server_config = self.config.get_server(self.server_name)
        if self.server_config is None:
            raise RuntimeError(f"Server '{self.server_name}' 配置不存在。")

    def _run_mcp_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP Server 的工具（同步版本）。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数。

        Returns:
            工具返回结果。
        """
        command = self.server_config.get("command", "")
        args = self.server_config.get("args", [])
        env = self.server_config.get("env", {})

        # 构建 MCP 工具调用请求（JSON-RPC 格式）
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        # 通过 subprocess 调用 MCP Server
        # 注意：这是简化实现，实际应使用 mcp SDK 的 Client 类
        process = subprocess.run(
            [command] + args,
            input=json.dumps(request),
            capture_output=True,
            text=True,
            env={**self._get_env(), **env},
            timeout=60,
        )

        if process.returncode != 0:
            raise RuntimeError(f"MCP 工具调用失败: {process.stderr}")

        response = json.loads(process.stdout)
        if "error" in response:
            raise RuntimeError(f"MCP 工具返回错误: {response['error']}")

        return response.get("result", {})

    def _get_env(self) -> dict[str, str]:
        """获取当前环境变量。"""
        import os

        return dict(os.environ)

    async def get_table_schema(self, database: str, table: str) -> TableSchema:
        """查询表结构。"""
        result = self._run_mcp_tool(
            "get_table_schema",
            {
                "database": database,
                "table": table,
            },
        )

        if result.get("error"):
            raise TableNotFoundError(database, table)

        columns = []
        for col in result.get("columns", []):
            columns.append(
                ColumnInfo(
                    name=col.get("name", ""),
                    type=col.get("type", "string"),
                    comment=col.get("comment", ""),
                    is_partition=col.get("is_partition", False),
                )
            )

        return TableSchema(
            database=database,
            table=table,
            columns=columns,
            partition_columns=result.get("partition_columns", []),
            comment=result.get("comment", ""),
        )

    async def execute_sql(self, sql: str, limit: int = 100) -> QueryResult:
        """执行 SQL 查询。"""
        result = self._run_mcp_tool(
            "execute_sql",
            {
                "sql": sql,
                "limit": limit,
            },
        )

        if result.get("error"):
            raise SQLError(sql, result["error"])

        return QueryResult(
            columns=result.get("columns", []),
            rows=result.get("rows", []),
            row_count=result.get("row_count", 0),
            success=True,
        )

    async def list_tables(self, database: str | None = None, keyword: str = "") -> list[str]:
        """列出可用的数据表。"""
        result = self._run_mcp_tool(
            "list_tables",
            {
                "database": database,
                "keyword": keyword,
            },
        )

        return result.get("tables", [])

    async def get_table_data(
        self, database: str, table: str, partition: str | None = None, limit: int = 10
    ) -> QueryResult:
        """查询表数据样本。"""
        result = self._run_mcp_tool(
            "get_table_data",
            {
                "database": database,
                "table": table,
                "partition": partition,
                "limit": limit,
            },
        )

        if result.get("error"):
            raise SQLError(f"SELECT * FROM {database}.{table}", result["error"])

        return QueryResult(
            columns=result.get("columns", []),
            rows=result.get("rows", []),
            row_count=result.get("row_count", 0),
            success=True,
        )


# ============================================================
# 同步适配器（用于非异步场景）
# ============================================================


class SyncMCPClient:
    """MCP Client 同步适配器。

    封装 asyncio 事件循环，使 MCP Client 可在同步代码中使用。
    """

    def __init__(self, config: MCPConfig | None = None, server_name: str | None = None) -> None:
        self._client = MCPClient(config, server_name)
        self._loop = asyncio.new_event_loop()

    def get_table_schema(self, database: str, table: str) -> TableSchema:
        """查询表结构（同步）。"""
        return self._loop.run_until_complete(self._client.get_table_schema(database, table))

    def execute_sql(self, sql: str, limit: int = 100) -> QueryResult:
        """执行 SQL 查询（同步）。"""
        return self._loop.run_until_complete(self._client.execute_sql(sql, limit))

    def list_tables(self, database: str | None = None, keyword: str = "") -> list[str]:
        """列出可用数据表（同步）。"""
        return self._loop.run_until_complete(self._client.list_tables(database, keyword))

    def get_table_data(
        self, database: str, table: str, partition: str | None = None, limit: int = 10
    ) -> QueryResult:
        """查询表数据样本（同步）。"""
        return self._loop.run_until_complete(
            self._client.get_table_data(database, table, partition, limit)
        )

    def close(self) -> None:
        """关闭事件循环。"""
        self._loop.close()
