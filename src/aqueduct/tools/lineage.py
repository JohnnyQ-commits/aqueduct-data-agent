"""血缘解析工具 — LineageTool。

从 SQL 文件中解析表级和字段级血缘关系。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool
from ..utils.regex import RE_INSERT_OVERWRITE, RE_TABLE_NAME

# 血缘解析专用正则
RE_SELECT_BLOCK = re.compile(r"select\s+(.*?)\s+from", re.IGNORECASE | re.DOTALL)
RE_FIELD_ALIAS = re.compile(r"([\w\(\)\.\s+\-*/]+)\s+as\s+(\w+)", re.IGNORECASE)


class LineageParser:
    """SQL 血缘解析器（核心逻辑，与原脚本一致）。"""

    def __init__(self, sql_file: str | Path) -> None:
        self.sql_file = Path(sql_file)
        self.sql_content = ""
        self.target_table = "unknown_target"
        self.source_tables: list[str] = []
        self.field_lineage: list[dict[str, Any]] = []

    def load_sql(self) -> None:
        from ..utils.regex import strip_comments

        with open(self.sql_file, encoding="utf-8") as f:
            self.sql_content = strip_comments(f.read())

    def parse_table_lineage(self) -> None:
        """解析表级血缘（目标表和来源表）。"""
        m_target = RE_INSERT_OVERWRITE.search(self.sql_content)
        if m_target:
            self.target_table = m_target.group(1)

        all_tables = RE_TABLE_NAME.findall(self.sql_content)
        seen = {self.target_table}
        for db, tbl in all_tables:
            full = f"{db}.{tbl}"
            if full not in seen:
                self.source_tables.append(full)
                seen.add(full)

    def parse_field_lineage(self) -> None:
        """解析字段级血缘。"""
        # 1. 建立表别名映射
        alias_map = {}
        from_alias_pattern = re.compile(
            r"(?:from|join)\s+([a-zA-Z_]\w*\.[a-zA-Z_]\w*)\s+([a-zA-Z_]\w*)\b",
            re.IGNORECASE,
        )
        for m in from_alias_pattern.finditer(self.sql_content):
            tbl, alias = m.group(1), m.group(2)
            cte_prefix_pattern = re.compile(
                r"(?:with|,)\s*" + re.escape(alias) + r"\s+as\s*\(",
                re.IGNORECASE,
            )
            if not cte_prefix_pattern.search(self.sql_content):
                alias_map[alias] = tbl

        # 2. 提取 INSERT 语句后的 SELECT 块
        insert_match = RE_INSERT_OVERWRITE.search(self.sql_content)
        search_start = insert_match.end() if insert_match else 0
        select_match = RE_SELECT_BLOCK.search(self.sql_content[search_start:])
        if select_match:
            fields_str = select_match.group(1)
            field_matches = RE_FIELD_ALIAS.findall(fields_str)
            for raw_col, alias in field_matches:
                raw_col = raw_col.strip()
                source_info = {"table": "unknown", "field": raw_col}

                if "." in raw_col:
                    parts = raw_col.split(".")
                    prefix = parts[0]
                    if prefix in alias_map:
                        source_info["table"] = alias_map[prefix]
                        source_info["field"] = parts[1]

                self.field_lineage.append(
                    {
                        "target_field": alias,
                        "sources": [source_info],
                    }
                )

    def generate_mermaid(self) -> str:
        """生成 Mermaid 表级血缘图。"""
        lines = ["### 1. 表级血缘图", "```mermaid", "graph LR"]
        for src in self.source_tables:
            lines.append(f"    {src.replace('.', '_')} --> {self.target_table.replace('.', '_')}")
        lines.append("```")

        if self.field_lineage:
            lines.append("\n### 2. 核心字段映射图")
            lines.append("```mermaid")
            lines.append("graph TD")
            for item in self.field_lineage:
                t_field = item["target_field"]
                for src in item["sources"]:
                    s_node = f"{src['table'].replace('.', '_')}_{src['field']}"
                    t_node = f"{self.target_table.replace('.', '_')}_{t_field}"
                    lines.append(
                        f"    {s_node}[{src['table']}.{src['field']}] --> {t_node}[{self.target_table}.{t_field}]"
                    )
            lines.append("```")

        return "\n".join(lines)

    def generate_mermaid_field(self) -> str:
        """生成字段级血缘图。"""
        if not self.field_lineage:
            return ""

        lines = ["```mermaid", "graph TD"]
        for item in self.field_lineage:
            t_field = item["target_field"]
            for src in item["sources"]:
                s_node = f"{src['table'].replace('.', '_')}_{src['field']}"
                t_node = f"{self.target_table.replace('.', '_')}_{t_field}"
                lines.append(
                    f"    {s_node}[{src['table']}.{src['field']}] --> {t_node}[{self.target_table}.{t_field}]"
                )
        lines.append("```")
        return "\n".join(lines)

    def update_design_doc(self, design_file: str | Path) -> bool:
        """更新设计文档，添加血缘信息。"""
        design_path = Path(design_file)
        if not design_path.exists():
            return False

        lineage_md = f"\n\n## 十一、数据血缘联动 (Lineage)\n\n{self.generate_mermaid()}\n"

        with open(design_path, encoding="utf-8") as f:
            content = f.read()

        if "## 十一、数据血缘联动" in content:
            content = re.sub(
                r"## 十一、数据血缘联动.*?(?=##|$)", lineage_md, content, flags=re.DOTALL
            )
        else:
            content += lineage_md

        with open(design_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True


@register_tool
class LineageTool(BaseTool):
    """血缘解析工具 — 注册到全局工具注册中心。"""

    name = "lineage"
    description = "字段级血缘解析 — 从 SQL 提取来源表到目标表的 Mermaid 关系图"

    def execute(self, **kwargs: Any) -> ToolResult:
        sql_file = kwargs.get("sql_file")
        if not sql_file:
            return ToolResult(success=False, error="缺少必填参数 sql_file")

        parser = LineageParser(sql_file)
        parser.load_sql()
        parser.parse_table_lineage()

        return ToolResult(
            success=True,
            data={
                "target": parser.target_table,
                "sources": parser.source_tables,
                "mermaid": parser.generate_mermaid(),
            },
            metadata={"source_count": len(parser.source_tables)},
        )

    def validate(self, **kwargs: Any) -> list[str]:
        if not kwargs.get("sql_file"):
            return ["缺少必填参数: sql_file"]
        return []
