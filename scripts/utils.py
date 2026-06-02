"""
共享工具函数与常量

从各脚本中提取的重复定义，统一维护。
"""

from __future__ import annotations

import re

# ============================================================
# 通用正则表达式
# ============================================================

# 匹配 库.表 格式的表名（用于提取所有数据源表）
RE_TABLE_NAME = re.compile(
    r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b",
)

# JOIN 关键字匹配
RE_JOIN_KEYWORD = re.compile(
    r"\b(left\s+join|right\s+join|inner\s+join|full\s+join|join)\b",
    re.IGNORECASE,
)
RE_JOIN = RE_JOIN_KEYWORD  # 别名，部分脚本使用此名称

# ON 关键字匹配
RE_ON = re.compile(r"\bon\b", re.IGNORECASE)

# INSERT OVERWRITE / INTO 匹配
RE_INSERT_OVERWRITE = re.compile(
    r"insert\s+overwrite\s+table\s+(\w+\.\w+)", re.IGNORECASE,
)
RE_INSERT_INTO = re.compile(
    r"insert\s+into\s+(\w+\.\w+)", re.IGNORECASE,
)

# CREATE TABLE 匹配
RE_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+\.\w+)",
    re.IGNORECASE,
)

# SELECT ... AS ... 字段别名提取
RE_ALIAS_AS = re.compile(
    r"\bas\s+([a-zA-Z_]\w*)\b", re.IGNORECASE,
)

# 单行注释
RE_COMMENT = re.compile(r"^\s*--")

# 分区字段匹配
RE_INC_DAY_FILTER = re.compile(
    r"\binc_day\s*=\s*['\"]([^'\"]*)['\"]", re.IGNORECASE,
)

PARTITION_FIELD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\binc_day\s*(=|in|between)", re.IGNORECASE),
    re.compile(r"\bday\s*(=|in|between)", re.IGNORECASE),
    re.compile(r"\bdata_day\s*(=|in|between)", re.IGNORECASE),
]


# ============================================================
# 通用函数
# ============================================================


def extract_tables(sql_text: str) -> list[str]:
    """从 SQL 文本中提取所有 库.表 格式的表名（去重）"""
    matches = RE_TABLE_NAME.findall(sql_text)
    tables: list[str] = []
    seen: set[str] = set()
    for db, tbl in matches:
        if len(db) > 1:
            full = f"{db}.{tbl}"
            if full not in seen:
                tables.append(full)
                seen.add(full)
    return tables


def parse_target_table(sql_text: str) -> str | None:
    """从 SQL 中解析目标表（优先 INSERT，其次 CREATE TABLE）"""
    for reg in [RE_INSERT_OVERWRITE, RE_INSERT_INTO, RE_CREATE_TABLE]:
        m = reg.search(sql_text)
        if m:
            return m.group(1)
    return None


def strip_comments(text: str) -> str:
    """去除单行注释"""
    return re.sub(r"--.*", "", text)


def has_partition_filter(lines: list[str]) -> bool:
    """检查 SQL 行列表中是否包含分区字段过滤"""
    for line in lines:
        if RE_COMMENT.match(line):
            continue
        for pat in PARTITION_FIELD_PATTERNS:
            if pat.search(line):
                return True
    return False
