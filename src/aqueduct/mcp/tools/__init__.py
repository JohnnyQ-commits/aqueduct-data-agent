"""MCP 工具接口定义。

定义 aqueduct 需要调用的 MCP 工具接口。
用户的数据平台需要实现这些接口才能与 aqueduct 对接。

工具清单:
  - get_table_schema: 查询表结构（字段名、类型、注释、分区）
  - execute_sql: 执行 SQL 查询（用于验证、测试）
  - list_tables: 列出可用的数据表
  - get_table_data: 查询表数据样本（用于 DQC 验证）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColumnInfo:
    """表字段信息。"""

    name: str  # 字段名
    type: str  # 数据类型（string/int/bigint 等）
    comment: str = ""  # 字段注释
    is_partition: bool = False  # 是否分区字段


@dataclass
class TableSchema:
    """表结构信息。"""

    database: str  # 库名
    table: str  # 表名
    columns: list[ColumnInfo]  # 字段列表
    partition_columns: list[str] = field(default_factory=list)  # 分区字段
    comment: str = ""  # 表注释


@dataclass
class QueryResult:
    """SQL 执行结果。"""

    columns: list[str]  # 列名
    rows: list[list[Any]]  # 数据行
    row_count: int = 0  # 行数
    error: str = ""  # 错误信息（成功时为空）
    success: bool = True


class TableNotFoundError(Exception):
    """表不存在异常。"""

    def __init__(self, database: str, table: str) -> None:
        self.database = database
        self.table = table
        super().__init__(f"表不存在: {database}.{table}")


class SQLError(Exception):
    """SQL 执行失败异常。"""

    def __init__(self, sql: str, message: str) -> None:
        self.sql = sql
        self.message = message
        super().__init__(f"SQL 执行失败: {message}\nSQL: {sql[:200]}")
