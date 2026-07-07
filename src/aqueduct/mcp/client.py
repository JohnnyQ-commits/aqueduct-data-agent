"""MCP Client SDK 封装。

封装标准 MCP Client 的连接、工具调用、生命周期管理。
用户通过 .mcp.json 配置连接自己的 MCP Server。

工具名映射：
  通过 .mcp.json 中的 toolMapping 配置，将 Aqueduct 标准工具名
  映射到 MCP Server 实际提供的工具名。未配置时直接使用标准名。

响应格式映射：
  通过 .mcp.json 中的 responseMapping 配置，将 MCP Server 的响应
  字段路径映射到 Aqueduct 标准格式。未配置时使用标准字段名。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
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

# Windows 检测
_IS_WINDOWS = sys.platform == "win32"


class MCPClient:
    """MCP Client 实现。

    通过 stdio 协议连接到用户配置的 MCP Server，
    调用远程工具获取表结构、执行 SQL 等。

    实现细节:
    - 进程缓存：同一 Server 共享子进程，避免每次调用新建
    - JSON-RPC 2.0 握手：首次调用前发送 initialize + initialized 通知
    - 工具名映射：通过 toolMapping 配置适配不同 MCP Server
    - 响应映射：通过 responseMapping 配置适配不同响应格式
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

        # 工具名映射和响应映射
        self._tool_mapping: dict[str, Any] = self.config.get_tool_mapping(self.server_name)
        self._response_mapping: dict[str, Any] = self.config.get_response_mapping(self.server_name)

        # 进程缓存和初始化状态
        self._process: subprocess.Popen | None = None
        self._initialized: bool = False
        self._next_request_id: int = 0

    def _get_env(self) -> dict[str, str]:
        """获取当前环境变量。"""
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

        # Windows 兼容性：解析完整路径
        resolved_command = command
        if not os.path.isabs(command):
            resolved = shutil.which(command)
            if resolved:
                resolved_command = resolved
            else:
                logger.warning("无法解析命令路径: %s，尝试直接使用", command)

        self._process = subprocess.Popen(
            [resolved_command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
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

        # Windows 兼容性：pipe 不支持 selectors，使用 readline + threading 超时
        if _IS_WINDOWS:
            response_line = self._readline_with_timeout(process.stdout, timeout)
        else:
            import selectors

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

    def _readline_with_timeout(self, stdout: Any, timeout: float) -> str:
        """Windows 兼容的行读取（带超时）。

        Windows pipe 不支持 selectors，使用 threading 实现超时。
        """
        import queue
        import threading

        result_queue: queue.Queue[str] = queue.Queue()

        def _read() -> None:
            line = stdout.readline()
            result_queue.put(line)

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()

        try:
            return result_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"MCP Server '{self.server_name}' 响应超时 ({timeout}s)") from None

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

    # ----------------------------------------------------------------
    # 工具名映射 & 响应解析
    # ----------------------------------------------------------------

    def _resolve_tool(self, standard_name: str) -> tuple[str, dict[str, Any]]:
        """解析标准工具名到实际工具名和映射配置。

        Args:
            standard_name: Aqueduct 标准工具名（如 get_table_schema）。

        Returns:
            (actual_tool_name, mapping_config) 元组。
            无映射时返回 (standard_name, {})。
        """
        mapping = self._tool_mapping.get(standard_name)
        if mapping is None:
            return standard_name, {}
        return mapping["name"], mapping

    def _transform_args(
        self,
        template: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """将参数模板中的变量替换为实际值。

        支持 $table, $database, $keyword 等变量。

        Args:
            template: 参数模板，如 {"keywords": "$table", "size": 50}。
            **kwargs: 变量值，如 table="users", database="default"。

        Returns:
            替换后的参数字典。
        """
        result = {}
        for key, value in template.items():
            if isinstance(value, str) and value.startswith("$"):
                var_name = value[1:]
                result[key] = kwargs.get(var_name, value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _extract_by_path(data: Any, path: str, default: Any = None) -> Any:
        """从嵌套数据结构中按路径提取值。

        支持点号分隔的字段名和方括号数组索引。
        示例: "data.columnList", "data.records[0].tblId"

        Args:
            data: 嵌套字典/列表。
            path: 提取路径。
            default: 路径不存在时的默认值。

        Returns:
            提取到的值，或 default。
        """
        if not path:
            return data if data is not None else default

        # 解析路径：data.records[0].tblId → ["data", "records", "0", "tblId"]
        parts: list[str] = []
        for segment in path.split("."):
            # 处理数组索引: records[0] → ["records", "0"]
            match = re.match(r"^(\w+)\[(\d+)]$", segment)
            if match:
                parts.append(match.group(1))
                parts.append(match.group(2))
            else:
                parts.append(segment)

        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (IndexError, ValueError):
                    return default
            else:
                return default
            if current is None:
                return default

        return current

    def _parse_table_schema(self, result: dict[str, Any], database: str, table: str) -> TableSchema:
        """使用 responseMapping 解析表结构响应为 TableSchema。

        支持两种模式：
        1. 配置了 responseMapping → 按路径提取字段
        2. 未配置 → 使用标准格式（columns/name/type/comment）

        Args:
            result: MCP Server 返回的原始结果。
            database: 数据库名。
            table: 表名。

        Returns:
            TableSchema 实例。
        """
        mapping = self._response_mapping.get("get_table_schema", {})

        # 路径配置（带默认值）
        columns_path = mapping.get("columns_path", "columns")
        name_path = mapping.get("column_name_path", "name")
        type_path = mapping.get("column_type_path", "type")
        comment_path = mapping.get("column_comment_path", "comment")
        partition_path = mapping.get("column_is_partition_path", "is_partition")

        # 提取 MCP 工具返回内容（兼容 content 包装和直接返回）
        raw_data = self._unwrap_mcp_content(result)

        # 提取字段列表
        raw_columns = self._extract_by_path(raw_data, columns_path, [])
        if not isinstance(raw_columns, list):
            raw_columns = []

        columns = []
        for col in raw_columns:
            columns.append(
                ColumnInfo(
                    name=str(self._extract_by_path(col, name_path, "")),
                    type=str(self._extract_by_path(col, type_path, "string")),
                    comment=str(self._extract_by_path(col, comment_path, "")),
                    is_partition=bool(self._extract_by_path(col, partition_path, False)),
                )
            )

        # 提取表级信息
        comment = str(self._extract_by_path(raw_data, "comment", ""))
        partition_columns_raw = self._extract_by_path(raw_data, "partition_columns", [])
        partition_columns = partition_columns_raw if isinstance(partition_columns_raw, list) else []

        return TableSchema(
            database=database,
            table=table,
            columns=columns,
            partition_columns=partition_columns,
            comment=comment,
        )

    @staticmethod
    def _unwrap_mcp_content(result: dict[str, Any]) -> dict[str, Any]:
        """解包 MCP 工具的 content 包装。

        MCP 工具返回格式可能是：
        1. {"content": [{"type": "text", "text": "{json}"}]}  — 标准 MCP 格式
        2. 直接返回数据字典

        Args:
            result: MCP 工具原始返回。

        Returns:
            解包后的数据字典。
        """
        content = result.get("content")
        if isinstance(content, list) and len(content) > 0:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = first.get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("MCP content text 不是有效 JSON: %s", text[:200])
                    return result
        return result

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

    # ----------------------------------------------------------------
    # 标准接口实现
    # ----------------------------------------------------------------

    async def get_table_schema(self, database: str, table: str) -> TableSchema:
        """查询表结构。

        通过 toolMapping 配置适配不同 MCP Server：
        - 无映射：直接调用 get_table_schema(database, table)
        - 有映射 + search_first：先搜索获取 ID，再查详情
        - 有映射无 search_first：直接调用映射后的工具
        """
        actual_tool, mapping = self._resolve_tool("get_table_schema")
        search_cfg = mapping.get("search_first")

        if search_cfg:
            # 两步调用：先搜索获取 ID，再查详情
            search_args = self._transform_args(
                search_cfg.get("arguments", {"keywords": "$table"}),
                table=table,
                database=database,
            )
            search_result = self._run_mcp_tool(search_cfg["name"], search_args)
            search_data = self._unwrap_mcp_content(search_result)

            # 从搜索结果中提取 ID
            extract_path = search_cfg.get("extract_id_path", "id")
            tbl_id = self._extract_by_path(search_data, extract_path)
            if tbl_id is None:
                raise TableNotFoundError(database, table)

            # 用 ID 调用详情工具
            id_param = search_cfg.get("id_param_name", "id")
            arguments = {id_param: str(tbl_id)}
        else:
            arguments = {"database": database, "table": table}

        result = self._run_mcp_tool(actual_tool, arguments)
        return self._parse_table_schema(result, database, table)

    async def execute_sql(self, sql: str, limit: int = 100) -> QueryResult:
        """执行 SQL 查询。"""
        actual_tool, _ = self._resolve_tool("execute_sql")
        result = self._run_mcp_tool(
            actual_tool,
            {
                "sql": sql,
                "limit": limit,
            },
        )

        data = self._unwrap_mcp_content(result)

        if data.get("error"):
            raise SQLError(sql, data["error"])

        return QueryResult(
            columns=data.get("columns", []),
            rows=data.get("rows", []),
            row_count=data.get("row_count", 0),
            success=True,
        )

    async def list_tables(self, database: str | None = None, keyword: str = "") -> list[str]:
        """列出可用的数据表。"""
        actual_tool, mapping = self._resolve_tool("list_tables")

        if mapping:
            # 使用映射的参数模板
            arguments = self._transform_args(
                mapping.get("arguments", {"keyword": "$keyword"}),
                database=database or "",
                keyword=keyword,
            )
        else:
            arguments = {
                "database": database,
                "keyword": keyword,
            }

        result = self._run_mcp_tool(actual_tool, arguments)
        data = self._unwrap_mcp_content(result)

        # 支持多种返回格式
        tables = data.get("tables", [])
        if isinstance(tables, list):
            return tables
        return []

    async def get_table_data(
        self, database: str, table: str, partition: str | None = None, limit: int = 10
    ) -> QueryResult:
        """查询表数据样本。"""
        actual_tool, _ = self._resolve_tool("get_table_data")
        result = self._run_mcp_tool(
            actual_tool,
            {
                "database": database,
                "table": table,
                "partition": partition,
                "limit": limit,
            },
        )

        data = self._unwrap_mcp_content(result)

        if data.get("error"):
            raise SQLError(f"SELECT * FROM {database}.{table}", data["error"])

        return QueryResult(
            columns=data.get("columns", []),
            rows=data.get("rows", []),
            row_count=data.get("row_count", 0),
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
