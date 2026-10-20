"""SQL 引擎链与方言转换（探查 / DQC 批量执行快车道共享）。

用户拍板（2026-10-20）：探查与质量测试引擎首选 presto，其次 spark，最后
hive（run 4 实录：同一探查 SQL MR hive 10 分钟超时 ×4 全灭，presto 16 秒
返回）。遇到不兼容语法**优先转换 SQL 语法**（同引擎重试转换句）再降级下
一个引擎；超时不降级——后续引擎只会更慢，白等无益。

三引擎控制器协议同构（payload 一字不差，只换路径），引擎路由在
dp_client.execute_hive_query(engine=...)；本模块只负责「按什么顺序、用什
么形态的 SQL 尝试」。hive SQL 是生成产物的原生方言，hive 引擎无需转换。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# 引擎尝试顺序：presto（16s 级）→ spark → hive（MR 慢队列兜底）
ENGINE_CHAIN = ("presto", "spark", "hive")

# hive 方言 → sqlglot 目标方言名
_ENGINE_DIALECT = {"presto": "presto", "spark": "spark", "hive": "hive"}


def _transpile(sql: str, read: str, write: str) -> list[str]:
    """sqlglot 薄封装（独立函数便于测试替身）。"""
    import sqlglot

    return sqlglot.transpile(sql, read=read, write=write)


def convert_sql_dialect(sql: str, engine: str) -> str:
    """hive 方言 SQL 转换到目标引擎方言。

    hive 引擎原样返回（原生方言）；sqlglot 缺失或转换异常时返回原句——
    转换失败不阻塞快车道，原句在下一引擎大概率也能跑。
    """
    if engine == "hive":
        return sql
    dialect = _ENGINE_DIALECT.get(engine, "hive")
    try:
        return ";\n".join(_transpile(sql, "hive", dialect))
    except Exception:
        return sql


def iter_engine_attempts(sql: str) -> Iterator[tuple[str, str]]:
    """产出 (engine, sql) 尝试序列：每引擎先原句、转换有实质差异再转换句；
    hive 引擎只试原句。大小写归一后相同视为无差异（sqlglot 会把关键字
    转大写，纯大小写差异不值得多一次平台往返）。"""
    for engine in ENGINE_CHAIN:
        yield engine, sql
        if engine != "hive":
            converted = convert_sql_dialect(sql, engine)
            if converted.strip().lower() != sql.strip().lower():
                yield engine, converted


def is_timeout_error(err: str | None) -> bool:
    """错误是否为超时（超时不降级：后续引擎只会更慢，白等无益）。"""
    return bool(re.search(r"任务超时|timeout", err or "", re.IGNORECASE))
