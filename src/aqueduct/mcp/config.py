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
      }
    }
  }
}
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
