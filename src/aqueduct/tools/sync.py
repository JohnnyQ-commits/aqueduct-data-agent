"""设计同步工具 — SyncTool。

从 Design.md 提取表结构，同步至 DDL 文件。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool


@register_tool
class SyncTool(BaseTool):
    """设计同步工具 — 注册到全局工具注册中心。"""

    name = "sync"
    description = "设计文档同步 — 将 Design.md 中的表结构提取并同步至 DDL 文件"

    def execute(self, **kwargs: Any) -> ToolResult:
        design_file = kwargs.get("design_file")
        if not design_file:
            return ToolResult(
                success=False,
                error="缺少必填参数 design_file",
            )

        design_path = Path(design_file)
        if not design_path.exists():
            return ToolResult(
                success=False,
                error=f"设计文档不存在: {design_file}",
            )

        content = design_path.read_text(encoding="utf-8")

        # 提取 CREATE TABLE 语句
        create_match = re.search(
            r"(CREATE\s+TABLE\s+.*?)(?:;|$)",
            content,
            re.IGNORECASE | re.DOTALL,
        )

        if not create_match:
            return ToolResult(
                success=False,
                error="设计文档中未找到 CREATE TABLE 语句",
            )

        ddl_content = create_match.group(1).strip()
        if not ddl_content.endswith(";"):
            ddl_content += ";"

        # 输出到 DDL 文件（如果指定）
        ddl_file = kwargs.get("ddl_file")
        if ddl_file:
            ddl_path = Path(ddl_file)
            ddl_path.write_text(ddl_content + "\n", encoding="utf-8")

        return ToolResult(
            success=True,
            data={
                "ddl": ddl_content,
                "table_name": re.search(
                    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)", ddl_content, re.IGNORECASE
                ).group(1)
                if create_match
                else "unknown",
            },
            metadata={"status": "synced"},
        )
