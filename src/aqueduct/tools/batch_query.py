"""批量查询工具 — BatchQueryTool。

根据表名列表批量查询元数据信息。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool
from ..utils.regex import RE_TABLE_NAME


def extract_tables(text: str) -> list[str]:
    """从文本中提取 库名.表名 格式的表名。"""
    matches = RE_TABLE_NAME.findall(text)
    tables = []
    seen = set()
    for db, tbl in matches:
        full = f"{db}.{tbl}"
        if full not in seen:
            tables.append(full)
            seen.add(full)
    return tables


def generate_task_list(tables: list[str]) -> str:
    """生成 MCP 查询任务清单。"""
    lines = [
        "-- ==========================================",
        "-- 批量查表任务清单",
        f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"-- 共 {len(tables)} 张表",
        "-- ==========================================",
        "",
        "-- 请在 Claude Code 中依次执行以下 MCP 查询，",
        "-- 将返回的 JSON 结果保存到 tables_detail.json 文件中",
        "",
    ]
    for i, table in enumerate(tables, 1):
        db, tbl = table.split(".", 1)
        lines.append(f"-- [{i}/{len(tables)}] {table}")

        # 根据库名猜测类型（常见数仓库名通常有后缀或前缀）
        if "mysql" in db.lower() or "rds" in db.lower():
            lines.append("-- Type: MySQL")
            lines.append(f"-- MCP: dp_mysql_search keywords='{tbl}' dbName='{db}'")
            lines.append("-- → 获取 id 后，再调用 dp_mysql_get_detail id='<id>'")
        elif "mongo" in db.lower():
            lines.append("-- Type: MongoDB")
            lines.append(f"-- MCP: dp_mongodb_search keywords='{tbl}' dbName='{db}'")
            lines.append("-- → 获取 id 后，再调用 dp_mongodb_get_detail id='<id>'")
        else:
            lines.append("-- Type: Hive")
            lines.append(f"-- MCP: dp_hive_table_search keywords='{tbl}' dbName='{db}'")
            lines.append("-- → 获取 tblId 后，再调用 dp_hive_table_get_detail id='<tblId>'")
        lines.append("")
    return "\n".join(lines)


def build_ddl_from_mcp_result(data: dict) -> str:
    """从 MCP 返回的 JSON 结果生成 DDL。"""
    # 兼容不同数据源的字段名
    db_name = data.get("dbName") or data.get("database") or "unknown"
    tbl_name = (
        data.get("tblName") or data.get("tableName") or data.get("collectionName") or "unknown"
    )
    comment = data.get("comment") or data.get("remarks") or ""

    # 获取列信息（不同源字段名不同）
    column_list = data.get("columnList") or data.get("columns") or data.get("fields") or []

    # 构建字段定义
    columns = []
    for col in column_list:
        col_name = col.get("columnName") or col.get("name") or col.get("fieldName") or ""
        col_type = col.get("columnType") or col.get("type") or "string"
        col_comment = col.get("comment") or col.get("columnNameCN") or col.get("remarks") or ""

        if col_comment:
            columns.append(f"    `{col_name}` {col_type} COMMENT '{col_comment}'")
        else:
            columns.append(f"    `{col_name}` {col_type}")

    ddl_lines = [
        f"-- {db_name}.{tbl_name}" + (f" ({comment})" if comment else ""),
        f"CREATE TABLE IF NOT EXISTS {db_name}.{tbl_name} (",
        ",\n".join(columns),
        ")",
    ]

    if comment:
        ddl_lines.append(f"COMMENT '{comment}'")

    # 数仓表默认增加分区
    if "mysql" not in db_name.lower() and "mongo" not in db_name.lower():
        ddl_lines.append("PARTITIONED BY (`inc_day` string COMMENT '数据分区日期，格式YYYYMMDD')")
        store_type = data.get("storeType", "parquet").lower()
        store_map = {
            "parquet": "STORED AS PARQUET",
            "orc": "STORED AS ORC",
            "textfile": "STORED AS TEXTFILE",
        }
        ddl_lines.append(store_map.get(store_type, f"STORED AS {store_type.upper()}"))

    ddl_lines.append(";")
    return "\n".join(ddl_lines)


def build_from_json(json_path: str | Path, output_path: str | Path) -> int:
    """从 JSON 文件读取结果并生成 DDL。"""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    tables = data.get("tables", [])
    if not tables:
        print(f"错误：{json_path} 中没有找到表数据")
        return 1

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("-- ==========================================\n")
        f.write("-- 表结构 DDL（批量生成）\n")
        f.write(f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"-- 共 {len(tables)} 张表\n")
        f.write("-- ==========================================\n\n")

        success_count = 0
        failed_count = 0

        for item in tables:
            table_name = item.get("table", "")
            detail = item.get("detail")

            if not detail:
                f.write(f"-- TODO: {table_name} - 缺少 MCP 查询结果\n\n")
                failed_count += 1
                continue

            try:
                ddl = build_ddl_from_mcp_result(detail)
                f.write(f"\n{ddl}\n\n")
                success_count += 1
                print(f"  [OK] {table_name}")
            except Exception as e:
                f.write(f"-- TODO: {table_name} - DDL 生成失败：{e!s}\n\n")
                failed_count += 1

    print(f"\nDDL 已生成到：{output_path}")
    print(f"  成功: {success_count}, 失败: {failed_count}")
    return 0


@register_tool
class BatchQueryTool(BaseTool):
    """批量元数据查询工具 — 注册到全局工具注册中心。

    纯实现：根据表名列表批量查询元数据信息，
    生成结构化的查询结果报告。
    """

    name = "batch_query"
    description = "批量元数据查询 — 根据表名列表查询结构、分区、字段等元数据"

    def execute(self, **kwargs: Any) -> ToolResult:
        tables = kwargs.get("tables", [])
        if not tables:
            return ToolResult(
                success=False,
                error="缺少必填参数: tables",
            )

        # 批量查询元数据（占位实现，实际需调用 MCP 工具）
        results = []
        for table_name in tables:
            results.append(
                {
                    "table": table_name,
                    "status": "pending",
                    "note": "需通过 MCP 工具查询元数据",
                }
            )

        return ToolResult(
            success=True,
            data={
                "results": results,
                "table_count": len(tables),
            },
            metadata={"table_count": len(tables)},
        )
