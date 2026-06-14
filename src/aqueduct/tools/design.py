"""设计文档工具 — DesignTool。

从 SQL 文件提取目标表、来源表、关联逻辑，生成设计文档。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool
from ..utils.regex import (
    RE_INC_DAY_FILTER,
    RE_JOIN_KEYWORD,
    extract_tables,
    parse_target_table,
)

# 排除的关键字（在解析字段别名时使用）
_EXCLUDED_KEYWORDS = {
    "select",
    "from",
    "where",
    "group",
    "order",
    "having",
    "end",
    "case",
    "when",
    "then",
    "else",
    "as",
    "join",
    "on",
    "limit",
}


def _parse_source_tables(sql: str) -> list[dict[str, str]]:
    """解析数据源表及其分区条件。"""
    sources = []
    tables = extract_tables(sql)
    target = parse_target_table(sql)

    if target:
        tables = [t for t in tables if t != target]

    snapshot_keywords = ["snap", "snapshot", "dim_", "dimension"]

    for table in tables:
        partition_info = "-"
        is_snapshot = any(kw in table.lower() for kw in snapshot_keywords)

        if is_snapshot:
            partition_info = "快照表(无分区)"
        else:
            table_pos = sql.lower().find(table.lower())
            if table_pos >= 0:
                context = sql[table_pos : table_pos + 1000]
                m = RE_INC_DAY_FILTER.search(context)
                if m:
                    partition_info = m.group(1) or "inc_day 有过滤"
                elif "inc_day" in context.lower():
                    partition_info = "inc_day 有过滤"

        sources.append(
            {
                "table": table,
                "partition": partition_info,
            }
        )
    return sources


def _parse_join_logic(sql: str) -> list[dict[str, str]]:
    """解析关联逻辑（从 ON 条件提取）。"""
    joins = []
    lines = sql.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if not RE_JOIN_KEYWORD.search(stripped):
            continue

        current_context = " ".join([(line_.strip()) for line_ in lines[i : i + 3]])
        match = re.search(
            r"\b(?:left\s+join|inner\s+join|right\s+join|full\s+join|join)\s+(\w+\.\w+|\w+)\s+(\w+)\s+on\s+(.+?)(?:\s+(?:left|inner|right|full|join|where|group|order|limit)|$)",
            current_context,
            re.IGNORECASE,
        )
        if match:
            joins.append(
                {
                    "table": match.group(1),
                    "alias": match.group(2),
                    "condition": match.group(3).strip(),
                }
            )
        elif "(" in stripped:
            # 简单标记子查询 JOIN
            joins.append(
                {
                    "table": "(subquery)",
                    "alias": "alias",
                    "condition": "see SQL",
                }
            )

    return joins


def _parse_field_list(sql: str) -> list[str]:
    """从 SQL 中提取字段别名列表。"""
    fields = []
    seen = set()

    for m in re.finditer(r"\bas\s+([a-zA-Z_]\w*)\b", sql, re.IGNORECASE):
        field_name = m.group(1)
        if field_name.lower() not in _EXCLUDED_KEYWORDS and field_name not in seen:
            fields.append(field_name)
            seen.add(field_name)
    return fields


@register_tool
class DesignTool(BaseTool):
    """设计文档生成工具 — 注册到全局工具注册中心。

    纯实现：读取 SQL 文件，自动提取目标表、来源表、关联逻辑，
    生成 Design.md 设计文档。
    """

    name = "design"
    description = "设计文档自动生成 — 从 SQL 提取目标表、来源表、关联逻辑"

    def execute(self, **kwargs: Any) -> ToolResult:
        sql_file = kwargs.get("sql_file")
        if not sql_file:
            return ToolResult(
                success=False,
                error="缺少必填参数 sql_file",
            )

        sql_path = Path(sql_file)
        if not sql_path.exists():
            return ToolResult(
                success=False,
                error=f"SQL 文件不存在: {sql_file}",
            )

        sql = sql_path.read_text(encoding="utf-8")
        requirement_name = kwargs.get("requirement_name", sql_path.stem)
        output_path = Path(kwargs.get("output_path", sql_path.parent / "Design.md"))

        target_table = parse_target_table(sql)
        source_tables = _parse_source_tables(sql)
        join_logic = _parse_join_logic(sql)
        all_tables = extract_tables(sql)

        fields = _parse_field_list(sql)

        # 构建设计文档
        lines = [
            f"# {requirement_name} - 设计文档",
            "",
            f"> 自动生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 数据来源: {sql_path.name}",
            "",
            "<!-- 以下部分需手动确认 -->",
            "",
            "## 一、需求概述",
            "",
            "- **需求目标**: [请补充]",
            "- **业务背景**: [请补充]",
            "",
            "## 二、取数逻辑",
            "",
            "[请根据 SQL 补充具体取数逻辑]",
            "",
            "## 三、映射关系",
            "",
            "[请补充各维度映射关系]",
            "",
            "<!-- 以下部分自动生成 -->",
            "",
            "## 四、目标表结构",
            "",
            f"- **表名**: `{target_table or '[未检测到目标表]'}`",
            "- **分区**: `inc_day` string (格式 `YYYYMMDD`)",
            "",
            "| 字段名 | 类型 | 注释 |",
            "|--------|------|------|",
        ]

        if fields:
            for f in fields:
                lines.append(f"| {f} | string | [请补充注释] |")
        else:
            lines.append("| [请从 SQL 中提取或手动补充] | | |")

        lines.append("")
        lines.append("## 五、数据来源与关联关系")
        lines.append("")
        for i, src in enumerate(source_tables, 1):
            lines.append(f"{i}. **{src['table']}**")
            lines.append("   - [请补充说明]")
            lines.append(f"   - 分区: `{src['partition']}`")
            lines.append("")

        if join_logic:
            lines.append("**关联方式**:")
            lines.append("")
            lines.append("| 关联表 | 别名 | 关联条件 |")
            lines.append("|--------|------|----------|")
            for j in join_logic:
                lines.append(f"| {j['table']} | {j['alias']} | {j['condition']} |")
        else:
            lines.append("**关联方式**: [请根据 SQL 补充]")

        lines.append("")
        lines.append("## 六、调度配置")
        lines.append("")
        lines.append("- **调度频率**: 每天一次 (T+1)")
        lines.append("- **调度时间**: 上游表产出后执行")
        lines.append("- **分区变量**: `inc_day = $[time(yyyyMMdd,-1d)]`")
        lines.append("- **失败策略**: 告警通知 + 重试")
        lines.append("")
        lines.append("## 七、数据质量保障")
        lines.append("")
        lines.append("详见 `数据质量测试.sql`")
        lines.append("")
        lines.append("## 八、上下游依赖")
        lines.append("")
        lines.append("**上游**:")

        upstream = [t for t in all_tables if t != target_table]
        for t in upstream:
            partition_note = ""
            for src in source_tables:
                if src["table"] == t:
                    partition_note = f" (`{src['partition']}`)"
                    break
            lines.append(f"- {t}{partition_note}")

        lines.append("")
        lines.append("**下游**:")
        lines.append("- [请补充下游应用/报表]")
        lines.append("")
        lines.append("## 九、文件清单")
        lines.append("")
        lines.append("| 文件 | 用途 |")
        lines.append("|------|------|")
        lines.append("| 表结构.sql | 目标表DDL |")
        lines.append(f"| {requirement_name}.sql | 核心ETL逻辑 |")
        lines.append("| 数据质量测试.sql | DQC测试用例 |")
        lines.append("| Design.md | 本设计文档 |")
        lines.append("")

        output_path.write_text("\n".join(lines), encoding="utf-8")

        return ToolResult(
            success=True,
            data={"output": str(output_path)},
            metadata={"status": "generated"},
        )


# ============================================================
# 兼容别名（供测试使用）
# ============================================================

extract_tables_from_sql = extract_tables
parse_source_tables = _parse_source_tables
parse_join_logic = _parse_join_logic
parse_field_list_from_sql = _parse_field_list
