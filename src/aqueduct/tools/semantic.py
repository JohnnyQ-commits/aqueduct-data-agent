"""语义文档工具 — SemanticTool。

从 knowledge/domains/*.json 聚合生成 semantic-model.md。
"""

from __future__ import annotations

import glob
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool


def _generate_mermaid_er(domain: dict) -> str:
    """生成 Mermaid ER 图代码。"""
    mermaid = ["```mermaid", "erDiagram"]

    entities = domain.get("entities", {})
    if not entities:
        return ""

    for ent_name, ent_info in entities.items():
        mermaid.append(f"    {ent_name} {{")
        pk = ent_info.get("primary_key", "")
        if pk:
            mermaid.append(f"        string {pk} PK")
        mermaid.append("    }")

    cardinality_map = {
        "1:1": "||--||",
        "1:N": "||--o{",
        "N:1": "}o--||",
        "M:N": "}o--o{",
    }
    for rel in domain.get("relationships", []):
        from_ent = rel.get("from")
        to_ent = rel.get("to")
        card = rel.get("cardinality", "1:N")
        symbol = cardinality_map.get(card, "||--o{")
        desc = rel.get("description", "")
        mermaid.append(f'    {from_ent} {symbol} {to_ent} : "{desc}"')

    mermaid.append("```")
    return "\n".join(mermaid)


def _domains_to_markdown(domains: list[dict]) -> str:
    """将领域 JSON 列表聚合为 Markdown 文档。"""
    lines = [
        "# Data Agent 可视化知识库（本体模型）",
        "",
        f"> **自动生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "> **说明**: 本文档由脚本自动从 `knowledge/domains/*.json` 聚合生成。",
        "",
        "---",
        "",
    ]

    # 目录
    lines.append("## 目录")
    for domain in domains:
        name = domain.get("name", "Unknown")
        lines.append(f"- [{name}](#业务域{name})")
    lines.append("\n---\n")

    for domain in domains:
        name = domain.get("name", "Unknown")
        lines.append(f"## 业务域：{name}")
        lines.append(f"- **ID**: `{domain.get('domain_id')}`")
        lines.append(f"- **描述**: {domain.get('description')}")
        ver = domain.get("version", "N/A")
        lines.append(f"- **版本**: {ver}")
        lines.append("")

        # 1. 关系拓扑图
        mermaid = _generate_mermaid_er(domain)
        if mermaid:
            lines.append("### 1. 关系拓扑图 (Relationship Map)")
            lines.append(mermaid)
            lines.append("")

        # 2. 核心实体
        lines.append("### 2. 核心实体 (Entities)")
        lines.append("| 实体名 | 主键 | 属性数 | 物理来源 | 描述 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for ent_name, ent_info in domain.get("entities", {}).items():
            pk = ent_info.get("primary_key", "-")
            attrs = len(ent_info.get("attributes", []))
            src = ent_info.get("source", "-")
            desc = ent_info.get("description", "")
            lines.append(f"| {ent_name} | `{pk}` | {attrs} | `{src}` | {desc} |")
        lines.append("")

        # 3. 层级分类
        if domain.get("hierarchy"):
            lines.append("### 3. 层级分类 (Hierarchy)")
            for parent, children in domain["hierarchy"].items():
                lines.append(f"**{parent}**")
                for child_name, child_info in children.items():
                    rule = child_info.get("rule", "")
                    desc = child_info.get("description", "")
                    lines.append(f"- **{child_name}**: {desc}")
                    if rule:
                        lines.append(f"  - 规则: `{rule}`")
                lines.append("")

        # 4. 指标口径
        lines.append("### 4. 指标口径 (Metrics)")
        lines.append("| 指标名称 | 定义 | 计算式 | 过滤条件 | 单位 | 预警阈值 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for _m_id, m_info in domain.get("metrics", {}).items():
            name = m_info.get("name", "-")
            expr = m_info.get("expression", "-")
            filt = m_info.get("filter", "-")
            unit = m_info.get("unit", "-")
            threshold = m_info.get("risk_threshold", "-")
            definition = m_info.get("definition", "-")
            lines.append(f"| {name} | {definition} | `{expr}` | `{filt}` | {unit} | {threshold} |")
        lines.append("")

        # 5. 计算链路
        if domain.get("computation_chains"):
            lines.append("### 5. 计算链路 (Computation Chains)")
            lines.append("| 复合指标 | 业务定义 | 计算步骤 | 预警阈值 |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for chain_name, chain_info in domain.get("computation_chains", {}).items():
                steps = " -> ".join(
                    [
                        s.get("metric", s.get("operator", "step"))
                        for s in chain_info.get("steps", [])
                    ]
                )
                lines.append(
                    f"| {chain_name} | {chain_info.get('definition')} | `{steps}` | {chain_info.get('risk_threshold', '-')} |"
                )
            lines.append("")

        # 6. 派生属性
        if domain.get("derived_attributes"):
            lines.append("### 6. 派生属性/转换规则 (Derived Attributes)")
            lines.append("| 属性名 | 逻辑说明 | 枚举值 |")
            lines.append("| :--- | :--- | :--- |")
            for attr_name, attr_info in domain.get("derived_attributes", {}).items():
                values = ", ".join(attr_info.get("values", []))
                lines.append(f"| {attr_name} | {attr_info.get('logic', '')} | {values} |")
            lines.append("")

        # 7. 公理
        if domain.get("axioms"):
            lines.append("### 7. 领域公理 (Axioms)")
            lines.append("| 编号 | 公理描述 | 形式化表达 |")
            lines.append("| :--- | :--- | :--- |")
            for ax in domain.get("axioms", []):
                lines.append(f"| {ax['id']} | {ax['statement']} | `{ax['formal']}` |")
            lines.append("")

        # 8. 业务规则
        if domain.get("business_rules"):
            lines.append("### 8. 业务规则 (Business Rules)")
            lines.append("| 规则名 | 内容 |")
            lines.append("| :--- | :--- |")
            for rule_name, rule_desc in domain.get("business_rules", {}).items():
                lines.append(f"| {rule_name} | {rule_desc} |")
            lines.append("")

        # 9. 过滤规则
        if domain.get("filter_rules"):
            lines.append("### 9. 分区与过滤规则 (Filter Rules)")
            lines.append("| 规则名 | 说明 | 条件 |")
            lines.append("| :--- | :--- | :--- |")
            for rule_name, rule_info in domain.get("filter_rules", {}).items():
                desc = rule_info.get("description", "-")
                parts = []
                if "partition" in rule_info:
                    parts.append(rule_info["partition"])
                if "conditions" in rule_info:
                    parts.extend(rule_info["conditions"])
                if "dedup" in rule_info:
                    parts.append(rule_info["dedup"])
                lines.append(
                    f"| {rule_name} | {desc} | `{' AND '.join(parts) if parts else '-'}` |"
                )
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


@register_tool
class SemanticTool(BaseTool):
    """语义文档生成工具 — 注册到全局工具注册中心。

    纯实现：直接读取 knowledge/domains/*.json，聚合为 Markdown 文档。
    """

    name = "semantic"
    description = "语义文档聚合 — 从 knowledge/domains/*.json 生成 semantic-model.md"

    def execute(self, **kwargs: Any) -> ToolResult:
        domains_dir = kwargs.get("domains_dir", "knowledge/domains")
        output_path = kwargs.get("output_path", "knowledge/semantic-model.md")

        domains_dir = Path(domains_dir)
        if not domains_dir.exists():
            return ToolResult(
                success=False,
                error=f"业务域目录不存在: {domains_dir}",
            )

        # 加载所有 domain JSON
        domains = []
        for file_path in glob.glob(str(domains_dir / "*.json")):
            with open(file_path, encoding="utf-8") as f:
                domains.append(json.load(f))

        if not domains:
            return ToolResult(
                success=False,
                error="未找到业务域 JSON 文件",
            )

        # 聚合为 Markdown
        md_content = _domains_to_markdown(domains)

        # 写入文件
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md_content, encoding="utf-8")

        return ToolResult(
            success=True,
            data={"output": str(output_path), "domain_count": len(domains)},
            metadata={"status": "aggregated"},
        )


# ============================================================
# 兼容别名（供测试使用）
# ============================================================


def load_all_domains(domains_dir: str | Path) -> list[dict]:
    """加载所有业务域 JSON 文件。"""
    domains = []
    for file_path in glob.glob(str(Path(domains_dir) / "*.json")):
        with open(file_path, encoding="utf-8") as f:
            domains.append(json.load(f))
    return domains


domains_to_markdown = _domains_to_markdown
generate_mermaid_er = _generate_mermaid_er
