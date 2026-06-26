"""MCP Client SDK 封装。

封装标准 MCP Client 的连接、工具调用、生命周期管理。
用户通过 .mcp.json 配置连接自己的 MCP Server。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import selectors
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

logger = logging.getLogger(__name__)

# MCP Server 响应超时（秒）
_DEFAULT_TIMEOUT = 30.0

# MCP 协议版本（可通过环境变量 AQUEDUCT_MCP_PROTOCOL_VERSION 覆盖）
_MCP_PROTOCOL_VERSION = "2025-03-26"


class MCPClient:
    """MCP Client 实现。

    通过 stdio 协议连接到用户配置的 MCP Server，
    调用远程工具获取表结构、执行 SQL 等。

    实现细节:
    - 进程缓存：同一 Server 共享子进程，避免每次调用新建
    - JSON-RPC 2.0 握手：首次调用前发送 initialize + initialized 通知
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

        # 进程缓存和初始化状态
        self._process: subprocess.Popen | None = None
        self._initialized: bool = False
        self._next_request_id: int = 0

    def _get_env(self) -> dict[str, str]:
        """获取当前环境变量。"""
        import os

        return dict(os.environ)

    def _ensure_process(self) -> subprocess.Popen:
        """确保子进程已启动，未启动则创建。"""
        if self._process is not None and self._process.poll() is None:
            return self._process

        command = self.server_config.get("command", "")
        args = self.server_config.get("args", [])
        env = {**self._get_env(), **self.server_config.get("env", {})}

        # 警告：配置中的 env 覆盖了系统环境变量
        config_env = self.server_config.get("env", {})
        if config_env:
            system_env = self._get_env()
            overridden = set(config_env.keys()) & set(system_env.keys())
            if overridden:
                logger.warning(
                    "Server '%s' 配置 env 覆盖了系统环境变量: %s",
                    self.server_name,
                    ", ".join(sorted(overridden)),
                )

        self._process = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        self._initialized = False
        return self._process

    def _send_request(
        self, request: dict[str, Any], timeout: float = _DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """发送 JSON-RPC 请求并解析响应。

        Args:
            request: JSON-RPC 请求字典。
            timeout: 等待 Server 响应的超时秒数，默认 30 秒。

        Raises:
            TimeoutError: Server 在超时时间内未响应。
            RuntimeError: Server 进程已终止或返回非法 JSON。
        """
        process = self._ensure_process()
        assert process.stdin and process.stdout

        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()

        # 使用 selectors 实现超时读取，避免 readline() 永久阻塞
        sel = selectors.DefaultSelector()
        try:
            sel.register(process.stdout, selectors.EVENT_READ)
            if not sel.select(timeout):
                raise TimeoutError(f"MCP Server '{self.server_name}' 响应超时 ({timeout}s)")
        finally:
            sel.unregister(process.stdout)
            sel.close()

        response_line = process.stdout.readline()
        if not response_line:
            raise RuntimeError("MCP Server 进程已终止")

        try:
            return json.loads(response_line)
        except json.JSONDecodeError as e:
            logger.error(
                "MCP Server 返回非法 JSON (server=%s, response=%r): %s",
                self.server_name,
                response_line[:200],
                e,
            )
            raise RuntimeError(f"MCP Server '{self.server_name}' 返回非法 JSON: {e}") from e

    def initialize(self) -> None:
        """执行 MCP 初始化握手。

        发送 initialize 请求，收到响应后发送 initialized 通知。
        """
        init_request = {
            "jsonrpc": "2.0",
            "id": self._next_request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aqueduct", "version": "0.4.0"},
            },
        }
        self._next_request_id += 1
        self._send_request(init_request)

        # 发送 initialized 通知（无 id，无返回值）
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        process = self._ensure_process()
        assert process.stdin
        process.stdin.write(json.dumps(notification) + "\n")
        process.stdin.flush()

        self._initialized = True

    def _run_mcp_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP Server 的工具（同步版本）。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数。

        Returns:
            工具返回结果。
        """
        # 首次调用前自动初始化
        if not self._initialized:
            self.initialize()

        # 构建 MCP 工具调用请求（JSON-RPC 格式）
        request = {
            "jsonrpc": "2.0",
            "id": self._next_request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        response = self._send_request(request)
        self._next_request_id += 1
        if "error" in response:
            raise RuntimeError(f"MCP 工具返回错误: {response['error']}")

        return response.get("result", {})

    def close(self) -> None:
        """关闭子进程，释放资源。"""
        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.stdin.close()
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    self._process.kill()
            self._process = None
            self._initialized = False

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
    每次方法调用使用独立的 asyncio.run()，避免事件循环生命周期问题。
    """

    def __init__(self, config: MCPConfig | None = None, server_name: str | None = None) -> None:
        self._client = MCPClient(config, server_name)

    def _run(self, coro: Any) -> Any:
        """在独立事件循环中运行协程。"""
        return asyncio.run(coro)

    def get_table_schema(self, database: str, table: str) -> TableSchema:
        """查询表结构（同步）。"""
        return self._run(self._client.get_table_schema(database, table))

    def execute_sql(self, sql: str, limit: int = 100) -> QueryResult:
        """执行 SQL 查询（同步）。"""
        return self._run(self._client.execute_sql(sql, limit))

    def list_tables(self, database: str | None = None, keyword: str = "") -> list[str]:
        """列出可用数据表（同步）。"""
        return self._run(self._client.list_tables(database, keyword))

    def get_table_data(
        self, database: str, table: str, partition: str | None = None, limit: int = 10
    ) -> QueryResult:
        """查询表数据样本（同步）。"""
        return self._run(self._client.get_table_data(database, table, partition, limit))

    def close(self) -> None:
        """关闭 MCP 子进程。"""
        self._client.close()
