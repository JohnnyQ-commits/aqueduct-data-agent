"""MCP 配置加载器。

读取 .mcp.json 配置文件，管理 MCP Server 连接信息。

配置示例 (.mcp.json):
{
  "mcpServers": {
    "数据资产平台": {
      "command": "npx",
      "args": ["-y", "@your-company/mcp-server"],
      "env": {
        "DATA_PLATFORM_URL": "https://数据平台地址",
        "API_TOKEN": "用户Token"
      },
      "toolMapping": {
        "get_table_schema": {
          "name": "actual_tool_name",
          "search_first": {
            "name": "search_tool",
            "arguments": {"keywords": "$table"},
            "extract_id_path": "data.records[0].id",
            "id_param_name": "id"
          }
        }
      },
      "responseMapping": {
        "get_table_schema": {
          "columns_path": "data.columnList",
          "column_name_path": "columnName",
          "column_type_path": "columnType",
          "column_comment_path": "comment"
        }
      }
    }
  }
}

toolMapping: 将 Aqueduct 标准工具名映射到 MCP Server 实际工具名。
  - name: 实际工具名
  - search_first: 可选，某些工具需要先搜索获取 ID 再查详情
    - arguments: 搜索参数，$table/$database/$keyword 会被替换为实际值
    - extract_id_path: 从搜索结果中提取 ID 的路径
    - id_param_name: 传给详情工具的 ID 参数名

responseMapping: 将 MCP Server 响应格式映射到 Aqueduct 标准格式。
  - columns_path: 字段列表在响应中的路径（如 "data.columnList"）
  - column_name_path: 字段名在每条记录中的路径
  - column_type_path: 字段类型在每条记录中的路径
  - column_comment_path: 字段注释在每条记录中的路径
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MCPConfig:
    """MCP 配置管理器。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """初始化配置加载器。

        Args:
            config_path: .mcp.json 文件路径。
                         默认查找项目根目录下的 .mcp.json。
        """
        if config_path is None:
            # 自动查找项目根目录
            config_path = Path(__file__).resolve().parent.parent.parent.parent / ".mcp.json"

        self.config_path = Path(config_path)
        self.servers: dict[str, dict[str, Any]] = {}

        self.load()

    def load(self) -> None:
        """加载 .mcp.json 配置文件。

        如果文件不存在，使用空配置。
        """
        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as f:
                config = json.load(f)
            self.servers = config.get("mcpServers", {})
        else:
            self.servers = {}

    def get_server(self, name: str) -> dict[str, Any] | None:
        """获取指定名称的 MCP Server 配置。

        Args:
            name: Server 名称。

        Returns:
            Server 配置字典，不存在时返回 None。
        """
        return self.servers.get(name)

    def list_servers(self) -> list[str]:
        """列出所有已配置的 MCP Server 名称。

        Returns:
            Server 名称列表。
        """
        return list(self.servers.keys())

    def is_configured(self) -> bool:
        """检查是否配置了至少一个 MCP Server。

        Returns:
            有配置返回 True，否则 False。
        """
        return len(self.servers) > 0

    def get_tool_mapping(self, server_name: str) -> dict[str, Any]:
        """获取指定 Server 的工具名映射配置。

        Args:
            server_name: Server 名称。

        Returns:
            工具映射字典，未配置时返回空字典。
        """
        server = self.get_server(server_name)
        if server is None:
            return {}
        return server.get("toolMapping", {})

    def get_response_mapping(self, server_name: str) -> dict[str, Any]:
        """获取指定 Server 的响应格式映射配置。

        Args:
            server_name: Server 名称。

        Returns:
            响应映射字典，未配置时返回空字典。
        """
        server = self.get_server(server_name)
        if server is None:
            return {}
        return server.get("responseMapping", {})

    def validate_server(self, name: str) -> list[str]:
        """验证 MCP Server 配置是否完整。

        Args:
            name: Server 名称。

        Returns:
            错误消息列表，空列表表示配置有效。
        """
        server = self.get_server(name)
        if server is None:
            return [f"Server '{name}' 不存在"]

        errors = []
        if "command" not in server:
            errors.append(f"Server '{name}' 缺少 command 字段")
        if "args" not in server:
            errors.append(f"Server '{name}' 缺少 args 字段")

        return errors
