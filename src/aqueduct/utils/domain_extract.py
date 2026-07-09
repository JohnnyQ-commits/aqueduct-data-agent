"""从 DDL/SQL 中提取结构化数据，用于更新 domain.json。

设计原则：
- 所有 JSON 读写走 raw dict（不走 Pydantic DomainModel），
  避免丢失 hierarchy / derived_attributes / Metric.definition 等扩展字段。
- 合并策略：只增不覆盖（已有实体/指标的 description 保留人工精写内容）。
- 失败不抛异常，返回空结果，由调用方决定是否记录 warning。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 正则（预编译） ────────────────────────────────────────────

# CREATE TABLE 匹配：支持 db.schema.table / db.table / table 三种格式
_RE_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?"
    r"([\w]+(?:\.[\w]+){1,2})"  # group(1): 2-part 或 3-part 表名
    r"\s*\(",
    re.IGNORECASE,
)

# 列定义：name type [COMMENT 'xxx']
_RE_COLUMN = re.compile(
    r"^\s*`?(\w+)`?\s+"
    r"(\w+(?:\([^)]*\))?)"  # type: string / decimal(18,2) / bigint 等
    r"(?:\s+COMMENT\s+['\"]([^'\"]*)['\"])?.*?$",
    re.IGNORECASE | re.MULTILINE,
)

# SQL 注释中的指标定义：-- 指标: id: 名称 | 表达式 [| 过滤条件 [| 单位]]
_RE_METRIC_COMMENT = re.compile(
    r"--\s*指标[：:]\s*"
    r"(\w+)\s*[：:]\s*"
    r"([^|]+?)\s*\|\s*"
    r"([^|]+?)"
    r"(?:\s*\|\s*([^|]*?))?"
    r"(?:\s*\|\s*(.+?))?\s*$",
    re.MULTILINE,
)

# SQL 聚合表达式（用于从 SQL 中提取可能的指标）
_RE_AGGREGATION = re.compile(
    r"(SUM|COUNT|AVG)\s*\(\s*"
    r"(?:DISTINCT\s+)?"
    r"(?:`?(\w+)`?\.)?`?(\w+)`?"
    r"\s*\)",
    re.IGNORECASE,
)

# SELECT alias AS 中文名称
_RE_ALIAS_CN = re.compile(
    r"\)\s*(?:as\s+)?[`']?([^`']+?)[`']?\s*(?:,|\n|$)",
    re.IGNORECASE,
)


# ── DDL 实体提取 ─────────────────────────────────────────────


def extract_entities_from_ddl(ddl: str) -> dict[str, dict[str, Any]]:
    """从 DDL 文本中提取实体定义。

    Args:
        ddl: CREATE TABLE 语句（可包含多条）。

    Returns:
        ``{entity_name: {primary_key, source, description, attributes}}``
    """
    entities: dict[str, dict[str, Any]] = {}

    # 按 CREATE TABLE 切分，逐个解析
    create_positions = [m.start() for m in _RE_CREATE_TABLE.finditer(ddl)]
    if not create_positions:
        return entities

    for i, start in enumerate(create_positions):
        end = create_positions[i + 1] if i + 1 < len(create_positions) else len(ddl)
        block = ddl[start:end]

        m = _RE_CREATE_TABLE.search(block)
        if not m:
            continue

        full_table = m.group(1)  # e.g. "db.schema.table" or "db.table"
        parts = full_table.split(".")
        entity_name = _to_entity_name(parts[-1])
        source = full_table

        # 提取列定义（取括号内内容）
        paren_start = block.index("(", m.end() - 1)
        paren_end = _find_matching_paren(block, paren_start)
        if paren_end < 0:
            continue
        columns_block = block[paren_start + 1 : paren_end]

        attributes = []
        primary_key = ""
        for col_match in _RE_COLUMN.finditer(columns_block):
            col_name = col_match.group(1)
            col_type = _normalize_type(col_match.group(2))
            col_comment = col_match.group(3) or ""

            # 跳过分区/约束行
            if col_name.upper() in ("PARTITIONED", "CLUSTERED", "STORED", "ROW", "LOCATION", "TBLPROPERTIES"):
                continue

            attr: dict[str, Any] = {
                "name": col_name,
                "type": col_type,
                "description": col_comment,
            }
            attributes.append(attr)

            # 识别主键（COMMENT 含"主键"或列名为 *_id 且是第一个）
            if "主键" in col_comment and not primary_key:
                primary_key = col_name

        # 如果没有明确主键，取第一个 _id 列
        if not primary_key:
            for attr in attributes:
                if attr["name"].endswith("_id"):
                    primary_key = attr["name"]
                    break

        entities[entity_name] = {
            "primary_key": primary_key,
            "source": source,
            "description": "",
            "attributes": attributes,
        }

    return entities


# ── SQL 指标提取 ─────────────────────────────────────────────


def extract_metrics_from_sql(sql: str) -> dict[str, dict[str, Any]]:
    """从 SQL 文本中提取指标定义。

    优先解析注释中的结构化指标定义，
    其次从聚合表达式中提取候选指标。

    Returns:
        ``{metric_id: {name, expression, filter, unit}}``
    """
    metrics: dict[str, dict[str, Any]] = {}

    # 1. 解析注释中的结构化定义
    for m in _RE_METRIC_COMMENT.finditer(sql):
        metric_id = m.group(1).strip()
        name = m.group(2).strip()
        expression = m.group(3).strip()
        filter_cond = (m.group(4) or "").strip()
        unit = (m.group(5) or "").strip()

        metrics[metric_id] = {
            "name": name,
            "definition": "",
            "expression": expression,
            "filter": filter_cond,
            "unit": unit,
        }

    # 2. 如果没有注释指标，从聚合表达式中提取候选
    if not metrics:
        seen: set[str] = set()
        for m in _RE_AGGREGATION.finditer(sql):
            func = m.group(1).upper()
            col = m.group(3)
            expr = m.group(0)

            # 生成一个 metric_id
            metric_id = f"{func.lower()}_{col}"
            if metric_id in seen:
                continue
            seen.add(metric_id)

            metrics[metric_id] = {
                "name": col,
                "definition": "",
                "expression": expr,
                "filter": "",
                "unit": "",
            }

    return metrics


def extract_filter_rules(sql: str) -> dict[str, dict[str, Any]]:
    """从 SQL 的 WHERE 子句中提取常见的过滤规则。

    Returns:
        ``{rule_id: {description, conditions/partition}}``
    """
    rules: dict[str, dict[str, Any]] = {}

    # 提取分区过滤
    partition_patterns = [
        (re.compile(r"(\w+)\s*=\s*'[^']*'", re.IGNORECASE), "partition"),
    ]
    for line in sql.split("\n"):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        # 匹配 inc_day / dt / data_day 等分区字段
        for field_re in [r"\binc_day\b", r"\bdt\b", r"\bdata_day\b"]:
            if re.search(field_re, stripped, re.IGNORECASE):
                field_name = re.search(field_re, stripped, re.IGNORECASE)
                if field_name:
                    rule_id = f"{field_name.group(0)}_filter"
                    if rule_id not in rules:
                        rules[rule_id] = {
                            "description": f"{field_name.group(0)} 分区过滤",
                            "partition": stripped.strip().rstrip(","),
                        }

    return rules


# ── 合并逻辑 ─────────────────────────────────────────────────


def merge_domain_updates(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """将提取到的新数据合并到现有 domain dict 中。

    合并策略（只增不覆盖）：
    - entities: 新实体添加，已有实体不动（保留人工精写的 description）
    - metrics: 新指标添加，已有指标不动
    - filter_rules: 新规则添加
    - relationships: 追加不重复的
    - version: bump patch 版本

    Args:
        existing: 从 domain.json 读取的完整 dict（保留所有字段）。
        updates: 本次提取到的增量数据。

    Returns:
        合并后的 dict（modified in place and returned）。
    """
    # entities: 新实体添加
    for name, entity in updates.get("entities", {}).items():
        if name not in existing.get("entities", {}):
            existing.setdefault("entities", {})[name] = entity

    # metrics: 新指标添加
    for mid, metric in updates.get("metrics", {}).items():
        if mid not in existing.get("metrics", {}):
            existing.setdefault("metrics", {})[mid] = metric

    # filter_rules: 新规则添加
    for rid, rule in updates.get("filter_rules", {}).items():
        if rid not in existing.get("filter_rules", {}):
            existing.setdefault("filter_rules", {})[rid] = rule

    # relationships: 追加不重复的（按 from+to+condition 去重）
    existing_rels = existing.get("relationships", [])
    existing_rel_keys = {
        (r.get("from", ""), r.get("to", ""), r.get("condition", ""))
        for r in existing_rels
    }
    for rel in updates.get("relationships", []):
        key = (rel.get("from", ""), rel.get("to", ""), rel.get("condition", ""))
        if key not in existing_rel_keys:
            existing_rels.append(rel)
            existing_rel_keys.add(key)
    existing["relationships"] = existing_rels

    # version bump
    existing["version"] = _bump_patch_version(existing.get("version", "1.0.0"))

    return existing


def create_new_domain(
    domain_id: str,
    name: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """为新业务域创建 domain.json 骨架。

    Args:
        domain_id: 域 ID。
        name: 域名称（中文）。
        updates: 从 DDL/SQL 提取的数据。

    Returns:
        完整的 domain dict。
    """
    domain: dict[str, Any] = {
        "domain_id": domain_id,
        "name": name,
        "version": "1.0.0",
        "description": f"自动生成域：{name}（待人工补充）",
        "entities": updates.get("entities", {}),
        "relationships": updates.get("relationships", []),
        "metrics": updates.get("metrics", {}),
        "business_rules": {},
        "axioms": [],
        "filter_rules": updates.get("filter_rules", {}),
    }
    return domain


# ── domain.json 读写 ──────────────────────────────────────────


def load_domain_dict(path: Path) -> dict[str, Any] | None:
    """读取 domain.json 为 raw dict（不走 Pydantic）。

    Returns:
        dict 或 None（文件不存在时）。
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取 domain.json 失败 (%s): %s", path, e)
        return None


def save_domain_dict(path: Path, domain: dict[str, Any]) -> None:
    """将 domain dict 写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(domain, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("domain.json 已更新: %s", path)


# ── 内部辅助 ─────────────────────────────────────────────────


def _to_entity_name(table_name: str) -> str:
    """将表名转换为实体名（驼峰式）。

    例：dwd_order_info_di → OrderInfo
    """
    # 去掉常见前缀
    name = table_name
    for prefix in ("dwd_", "dws_", "dim_", "ods_", "ads_", "dm_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    # 去掉常见后缀
    for suffix in ("_di", "_df", "_ri", "_rf", "_di", "_info", "_detail"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    # 蛇形 → 驼峰
    parts = name.split("_")
    return "".join(p.capitalize() for p in parts if p)


def _normalize_type(raw_type: str) -> str:
    """将 DDL 类型标准化为 domain.json 常用类型。"""
    t = raw_type.upper().strip()
    if t in ("STRING", "VARCHAR", "CHAR", "TEXT"):
        return "string"
    if t in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT"):
        return "bigint"
    if t.startswith("DECIMAL") or t in ("DOUBLE", "FLOAT", "NUMERIC"):
        return "decimal"
    if t in ("TIMESTAMP", "DATETIME"):
        return "timestamp"
    if t == "DATE":
        return "date"
    if t == "BOOLEAN":
        return "boolean"
    return raw_type.lower()


def _find_matching_paren(text: str, start: int) -> int:
    """找到与 start 位置 '(' 匹配的 ')' 位置。"""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _bump_patch_version(version: str) -> str:
    """递增语义化版本 patch 号。"""
    parts = version.split(".")
    if len(parts) != 3:
        return "1.0.1"
    try:
        parts[2] = str(int(parts[2]) + 1)
    except ValueError:
        parts[2] = "1"
    return ".".join(parts)
