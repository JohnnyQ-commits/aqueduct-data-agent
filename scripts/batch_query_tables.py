# -*- coding: utf-8 -*-
"""
批量查表结构工具 — 数据开发agent 专用

用法：
  模式 1：python3 batch_query_tables.py "库1.表1 库2.表2" output.sql
  模式 2：python3 batch_query_tables.py --file tables.txt output.sql

说明：
  本脚本不直接连接 MCP（当前为浏览器认证模式），而是作为辅助工具：
  1. 从输入文件中提取表名列表
  2. 生成 MCP 查询任务清单（供 AI 在 Claude Code 中批量调用）
  3. 读取 MCP 返回的 JSON 结果，生成标准 DDL 到输出文件

  实际使用流程：
  Step 1: 运行 python3 batch_query_tables.py "表1 表2" output.sql
          → 生成 output.sql（头部包含 MCP 查询清单）
  Step 2: 在 Claude Code 中用 MCP 工具逐表查询，将 JSON 结果贴给 AI
  Step 3: AI 运行 python3 batch_query_tables.py --build input.json output.sql
          → 将 JSON 字段信息转为 CREATE TABLE DDL
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime


def extract_tables(text):
    """从文本中提取 库名.表名 格式的表名"""
    # 匹配 database.table 格式
    pattern = r'[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*'
    matches = re.findall(pattern, text)
    # 过滤掉纯数字前缀（如 0.15）
    tables = []
    seen = set()
    for m in matches:
        if m not in seen:
            tables.append(m)
            seen.add(m)
    return tables


def generate_task_list(tables):
    """生成 MCP 查询任务清单（Markdown 格式）"""
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
        db, tbl = table.split('.', 1)
        lines.append(f"-- [{i}/{len(tables)}] {table}")
        lines.append(f"-- MCP: bdp_hive_table_search keywords='{tbl}' dbName='{db}'")
        lines.append(f"-- → 获取 tblId 后，再调用 bdp_hive_table_get_detail id='<tblId>'")
        lines.append("")
    return '\n'.join(lines)


def build_ddl_from_mcp_result(data):
    """
    从 MCP bdp_hive_table_get_detail 的返回结果生成 CREATE TABLE DDL

    Args:
        data: MCP 返回的 JSON 中的 data 字段

    Returns:
        CREATE TABLE DDL 语句
    """
    db_name = data.get('dbName', 'unknown')
    tbl_name = data.get('tblName', 'unknown')
    comment = data.get('comment', '')
    store_type = data.get('storeType', 'parquet').lower()
    column_list = data.get('columnList', [])

    # 构建字段定义
    columns = []
    for col in column_list:
        col_name = col.get('columnName', '')
        col_type = col.get('columnType', 'string')
        col_comment = col.get('comment', '')
        col_comment_cn = col.get('columnNameCN', '')

        comment_text = col_comment or col_comment_cn
        if comment_text:
            columns.append(f"    `{col_name}` {col_type} COMMENT '{comment_text}'")
        else:
            columns.append(f"    `{col_name}` {col_type}")

    ddl_lines = [
        f"-- {db_name}.{tbl_name}" + (f" ({comment})" if comment else ""),
        f"CREATE TABLE IF NOT EXISTS {db_name}.{tbl_name} (",
        ',\n'.join(columns),
        ")",
    ]

    if comment:
        ddl_lines.append(f"COMMENT '{comment}'")

    ddl_lines.append("PARTITIONED BY (`inc_day` string COMMENT '数据分区日期，格式YYYYMMDD')")

    store_map = {
        'parquet': 'STORED AS PARQUET',
        'orc': 'STORED AS ORC',
        'textfile': 'STORED AS TEXTFILE',
    }
    ddl_lines.append(store_map.get(store_type, f"STORED AS {store_type.upper()}"))
    ddl_lines.append(";")

    return '\n'.join(ddl_lines)


def build_from_json(json_path, output_path):
    """
    从 JSON 文件读取 MCP 返回结果，生成 DDL 到 SQL 文件

    JSON 格式：
    {
      "tables": [
        {"table": "db.table1", "detail": { ... MCP data ... }},
        {"table": "db.table2", "detail": { ... MCP data ... }}
      ]
    }
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tables = data.get('tables', [])
    if not tables:
        print(f"错误：{json_path} 中没有找到表数据")
        return 1

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"-- ==========================================\n")
        f.write(f"-- 表结构 DDL（批量生成）\n")
        f.write(f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"-- 共 {len(tables)} 张表\n")
        f.write(f"-- ==========================================\n\n")

        success_count = 0
        failed_count = 0

        for item in tables:
            table_name = item.get('table', '')
            detail = item.get('detail')

            if not detail:
                f.write(f"-- TODO: {table_name} - 缺少 MCP 查询结果\n\n")
                failed_count += 1
                continue

            try:
                ddl = build_ddl_from_mcp_result(detail)
                f.write(f"\n{ddl}\n\n")
                col_count = len(detail.get('columnList', []))
                print(f"  [OK] {table_name} ({col_count} fields)")
                success_count += 1
            except Exception as e:
                f.write(f"-- TODO: {table_name} - DDL 生成失败：{str(e)}\n\n")
                failed_count += 1

        f.write(f"-- 统计：成功 {success_count}/{len(tables)}，失败 {failed_count}/{len(tables)}\n")

    print(f"\nDDL 已生成到：{output_path}")
    print(f"  成功: {success_count}, 失败: {failed_count}")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    # 模式 2：从 JSON 构建 DDL
    if sys.argv[1] == '--build':
        if len(sys.argv) < 4:
            print("用法: python3 batch_query_tables.py --build <input.json> <output.sql>")
            return 1
        return build_from_json(sys.argv[2], sys.argv[3])

    # 模式 3：从文件读取表名
    if sys.argv[1] == '--file':
        if len(sys.argv) < 4:
            print("用法: python3 batch_query_tables.py --file <tables.txt> <output.sql>")
            return 1
        file_path = sys.argv[2]
        output_path = sys.argv[3]
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        tables = extract_tables(content)
    else:
        # 模式 1：命令行直接传表名（空格分隔）
        tables_input = sys.argv[1]
        output_path = sys.argv[2] if len(sys.argv) > 2 else 'tables_ddl.sql'
        tables = extract_tables(tables_input)

    if not tables:
        print("错误：未找到任何有效的表名（格式：库名.表名）")
        return 1

    print(f"\n找到 {len(tables)} 张表：")
    for i, t in enumerate(tables, 1):
        print(f"  {i}. {t}")

    # 生成任务清单到输出文件
    task_list = generate_task_list(tables)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(task_list)
    print(f"\n任务清单已生成：{output_path}")
    print("请在 Claude Code 中按清单顺序查询 MCP，然后将 JSON 结果保存为 tables_detail.json")
    print("最后运行: python batch_query_tables.py --build tables_detail.json " + output_path)

    return 0


if __name__ == '__main__':
    sys.exit(main())
