"""本体模型 Pydantic 定义。

定义业务域本体模型的强类型结构：
Entity（实体）、Attribute（属性）、Relationship（关系）、
Metric（度量）、DomainModel（业务域模型）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Attribute(BaseModel):
    """实体属性定义。"""

    name: str  # 属性名
    type: str  # 数据类型（string/int/bigint 等）
    description: str = ""  # 中文描述
    constraints: list[str] = Field(default_factory=list)  # 约束（NOT NULL, UNIQUE 等）


class Entity(BaseModel):
    """业务实体定义。"""

    primary_key: str = ""  # 主键字段名
    source: str = ""  # 物理来源表（库.表）
    description: str = ""  # 实体描述
    attributes: list[Attribute] = Field(default_factory=list)  # 属性列表
    filters: list[str] = Field(default_factory=list)  # 默认过滤条件

    def search_attributes(self, keyword: str) -> list[Attribute]:
        """搜索匹配关键字的属性。"""
        kw = keyword.lower()
        return [a for a in self.attributes if kw in a.name.lower() or kw in a.description.lower()]


class Relationship(BaseModel):
    """实体间关系定义。"""

    from_entity: str = Field(alias="from")  # 源实体名
    to_entity: str = Field(alias="to")  # 目标实体名
    cardinality: str = "1:N"  # 基数（1:1/1:N/N:1/M:N）
    join_type: str = "LEFT JOIN"  # JOIN 类型
    condition: str = ""  # JOIN 条件
    description: str = ""  # 关系描述

    model_config = {"populate_by_name": True}


class Metric(BaseModel):
    """指标定义。"""

    name: str  # 指标中文名
    expression: str  # 可执行 SQL 表达式
    filter: str = ""  # 过滤条件
    unit: str = ""  # 单位
    description: str = ""  # 指标定义描述
    risk_threshold: str = ""  # 预警阈值


class ComputationChain(BaseModel):
    """计算链路定义。"""

    definition: str  # 业务定义
    steps: list[dict[str, Any]]  # 计算步骤
    unit: str = ""  # 单位
    risk_threshold: str = ""  # 预警阈值


def _to_str(value: Any) -> str:
    """任意值转字符串：str 原样返回，其余 JSON 序列化。"""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _normalize_entity(ent: dict[str, Any]) -> dict[str, Any]:
    """实体字段归一: table→source、primary_key list→str。"""
    ent = dict(ent)
    if "table" in ent and "source" not in ent:
        ent["source"] = ent.pop("table")
    if isinstance(ent.get("primary_key"), list):
        ent["primary_key"] = ", ".join(ent["primary_key"])
    return ent


def _normalize_metric(metric: dict[str, Any]) -> dict[str, Any]:
    """指标字段归一: formula→expression。"""
    metric = dict(metric)
    if "formula" in metric and "expression" not in metric:
        metric["expression"] = metric.pop("formula")
    return metric


def _normalize_relationship(rel: dict[str, Any]) -> dict[str, Any]:
    """关系字段归一: type→cardinality。"""
    rel = dict(rel)
    if "type" in rel and "cardinality" not in rel:
        rel["cardinality"] = rel.pop("type")
    return rel


def _normalize_domain_data(data: dict[str, Any]) -> dict[str, Any]:
    """兼容新旧两种业务域 JSON schema，统一转为内部模型结构。

    新 schema（data-developer Skill Phase 6 生成）:
        域名为 domain_name；entities/metrics 为 list；
        实体物理表为 table、主键为 list；指标公式为 formula；关系基数为 type。
    旧 schema（demo 域）:
        域名为 name；entities/metrics 为 dict；指标公式为 expression；关系基数为 cardinality。
    dict 与 list 形态均做字段级归一（兼容两者混用的过渡 schema）。
    """
    data = dict(data)

    # 域名: domain_name → name
    if "domain_name" in data and "name" not in data:
        data["name"] = data["domain_name"]

    # entities: list 转 dict（key 为实体名），dict 保留原 key；统一做字段归一
    if isinstance(data.get("entities"), list):
        data["entities"] = {e.get("name", ""): _normalize_entity(e) for e in data["entities"]}
    elif isinstance(data.get("entities"), dict):
        data["entities"] = {k: _normalize_entity(v) for k, v in data["entities"].items()}

    # metrics: list 转 dict（key 为指标名），dict 保留原 key；统一做字段归一
    if isinstance(data.get("metrics"), list):
        data["metrics"] = {m.get("name", ""): _normalize_metric(m) for m in data["metrics"]}
    elif isinstance(data.get("metrics"), dict):
        data["metrics"] = {k: _normalize_metric(v) for k, v in data["metrics"].items()}

    # business_rules: list → dict；dict 形态的 value 统一转 str（自由扩展结构序列化保留）
    rules = data.get("business_rules")
    if isinstance(rules, list):
        data["business_rules"] = {f"rule_{i}": _to_str(rule) for i, rule in enumerate(rules, 1)}
    elif isinstance(rules, dict):
        data["business_rules"] = {k: _to_str(v) for k, v in rules.items()}

    # relationships: 统一做字段归一
    if isinstance(data.get("relationships"), list):
        data["relationships"] = [_normalize_relationship(r) for r in data["relationships"]]

    return data


class DomainModel(BaseModel):
    """业务域本体模型 — 完整定义一个业务领域。"""

    domain_id: str  # 业务域唯一 ID
    name: str  # 业务域中文名
    version: str = "1.0.0"  # 版本号
    description: str = ""  # 业务域描述
    entities: dict[str, Entity] = Field(default_factory=dict)  # 实体集合
    relationships: list[Relationship] = Field(default_factory=list)  # 关系集合
    metrics: dict[str, Metric] = Field(default_factory=dict)  # 指标集合
    computation_chains: dict[str, ComputationChain] = Field(default_factory=dict)
    business_rules: dict[str, str] = Field(default_factory=dict)
    axioms: list[dict[str, Any]] = Field(default_factory=list)
    filter_rules: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_json(cls, path: str | Path) -> DomainModel:
        """从 JSON 文件加载业务域模型。

        兼容新旧两种 schema（见 _normalize_domain_data）。

        Args:
            path: JSON 文件路径。

        Returns:
            DomainModel 实例。
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"领域模型文件不存在: {path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return cls.model_validate(_normalize_domain_data(data))

    def to_json(self, path: str | Path) -> None:
        """保存业务域模型到 JSON 文件。

        Args:
            path: JSON 文件路径。
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                self.model_dump(by_alias=True, exclude_none=True), f, ensure_ascii=False, indent=2
            )

    def search_entities(self, keyword: str) -> list[tuple[str, list[Attribute]]]:
        """在所有实体中搜索匹配关键字的属性。

        Args:
            keyword: 搜索关键字。

        Returns:
            列表，每项为 (实体名, 匹配属性列表)。
        """
        results = []
        for entity_name, entity in self.entities.items():
            attrs = entity.search_attributes(keyword)
            if attrs:
                results.append((entity_name, attrs))
        return results

    def search_metrics(self, keyword: str) -> list[tuple[str, Metric]]:
        """在所有指标中搜索匹配关键字的指标。

        Args:
            keyword: 搜索关键字。

        Returns:
            列表，每项为 (指标 ID, 指标定义)。
        """
        kw = keyword.lower()
        return [
            (mid, m)
            for mid, m in self.metrics.items()
            if kw in mid.lower() or kw in m.name.lower() or kw in m.description.lower()
        ]

    def to_mermaid(self) -> str:
        """生成 Mermaid ER 图。

        Returns:
            Mermaid ER 图文本。
        """
        lines = ["```mermaid", "erDiagram"]

        # 实体定义
        for entity_name, entity in self.entities.items():
            lines.append(f"    {entity_name} {{")
            if entity.primary_key:
                lines.append(f"        string {entity.primary_key} PK")
            lines.append("    }")

        # 基数到 Mermaid 符号映射
        cardinality_map = {
            "1:1": "||--||",
            "1:N": "||--o{",
            "N:1": "}o--||",
            "M:N": "}o--o{",
        }

        # 关系定义
        for rel in self.relationships:
            symbol = cardinality_map.get(rel.cardinality, "||--o{")
            desc = rel.description or rel.condition
            lines.append(f'    {rel.from_entity} {symbol} {rel.to_entity} : "{desc}"')

        lines.append("```")
        return "\n".join(lines)
