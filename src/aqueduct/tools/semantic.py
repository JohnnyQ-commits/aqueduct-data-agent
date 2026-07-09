"""语义文档工具 — SemanticTool。

支持两种目录结构：
- 扁平模式: knowledge/domains/*.json（旧版兼容）
- 域目录模式: knowledge/domains/{domain_id}/domain.json（推荐）

生成产物：
- 单域审计文档: knowledge/domains/{domain_id}/semantic-model.md
- 总索引: knowledge/INDEX.md
"""

from __future__ import annotations

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


def _domain_to_markdown(domain: dict) -> str:
    """将单个领域 JSON 转换为 Markdown 审计文档。"""
    name = domain.get("name", "Unknown")
    lines = [
        f"# 业务域：{name}",
        "",
        f"> **自动生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> **域 ID**: `{domain.get('domain_id')}`",
        f"> **版本**: {domain.get('version', 'N/A')}",
        "",
        "---",
        "",
    ]

    # 描述
    desc = domain.get("description", "")
    if desc:
        lines.append(f"**描述**: {desc}")
        lines.append("")

    # 1. 关系拓扑图
    mermaid = _generate_mermaid_er(domain)
    if mermaid:
        lines.append("## 1. 关系拓扑图 (Relationship Map)")
        lines.append(mermaid)
        lines.append("")

    # 2. 核心实体
    lines.append("## 2. 核心实体 (Entities)")
    lines.append("| 实体名 | 主键 | 属性数 | 物理来源 | 描述 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    for ent_name, ent_info in domain.get("entities", {}).items():
        pk = ent_info.get("primary_key", "-")
        attrs = len(ent_info.get("attributes", []))
        src = ent_info.get("source", "-")
        ent_desc = ent_info.get("description", "")
        lines.append(f"| {ent_name} | `{pk}` | {attrs} | `{src}` | {ent_desc} |")
    lines.append("")

    # 3. 层级分类
    if domain.get("hierarchy"):
        lines.append("## 3. 层级分类 (Hierarchy)")
        for parent, children in domain["hierarchy"].items():
            lines.append(f"**{parent}**")
            for child_name, child_info in children.items():
                rule = child_info.get("rule", "")
                child_desc = child_info.get("description", "")
                lines.append(f"- **{child_name}**: {child_desc}")
                if rule:
                    lines.append(f"  - 规则: `{rule}`")
            lines.append("")

    # 4. 指标口径
    lines.append("## 4. 指标口径 (Metrics)")
    lines.append("| 指标名称 | 定义 | 计算式 | 过滤条件 | 单位 | 预警阈值 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for _m_id, m_info in domain.get("metrics", {}).items():
        m_name = m_info.get("name", "-")
        expr = m_info.get("expression", "-")
        filt = m_info.get("filter", "-")
        unit = m_info.get("unit", "-")
        threshold = m_info.get("risk_threshold", "-")
        definition = m_info.get("definition", "-")
        lines.append(f"| {m_name} | {definition} | `{expr}` | `{filt}` | {unit} | {threshold} |")
    lines.append("")

    # 5. 计算链路
    if domain.get("computation_chains"):
        lines.append("## 5. 计算链路 (Computation Chains)")
        lines.append("| 复合指标 | 业务定义 | 计算步骤 | 预警阈值 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for chain_name, chain_info in domain.get("computation_chains", {}).items():
            steps = " -> ".join(
                [s.get("metric", s.get("operator", "step")) for s in chain_info.get("steps", [])]
            )
            lines.append(
                f"| {chain_name} | {chain_info.get('definition')} | `{steps}` | {chain_info.get('risk_threshold', '-')} |"
            )
        lines.append("")

    # 6. 派生属性
    if domain.get("derived_attributes"):
        lines.append("## 6. 派生属性/转换规则 (Derived Attributes)")
        lines.append("| 属性名 | 逻辑说明 | 枚举值 |")
        lines.append("| :--- | :--- | :--- |")
        for attr_name, attr_info in domain.get("derived_attributes", {}).items():
            values = ", ".join(attr_info.get("values", []))
            lines.append(f"| {attr_name} | {attr_info.get('logic', '')} | {values} |")
        lines.append("")

    # 7. 公理
    if domain.get("axioms"):
        lines.append("## 7. 领域公理 (Axioms)")
        lines.append("| 编号 | 公理描述 | 形式化表达 |")
        lines.append("| :--- | :--- | :--- |")
        for ax in domain.get("axioms", []):
            lines.append(f"| {ax['id']} | {ax['statement']} | `{ax['formal']}` |")
        lines.append("")

    # 8. 业务规则
    if domain.get("business_rules"):
        lines.append("## 8. 业务规则 (Business Rules)")
        lines.append("| 规则名 | 内容 |")
        lines.append("| :--- | :--- |")
        for rule_name, rule_desc in domain.get("business_rules", {}).items():
            lines.append(f"| {rule_name} | {rule_desc} |")
        lines.append("")

    # 9. 过滤规则
    if domain.get("filter_rules"):
        lines.append("## 9. 分区与过滤规则 (Filter Rules)")
        lines.append("| 规则名 | 说明 | 条件 |")
        lines.append("| :--- | :--- | :--- |")
        for rule_name, rule_info in domain.get("filter_rules", {}).items():
            rule_desc = rule_info.get("description", "-")
            parts = []
            if "partition" in rule_info:
                parts.append(rule_info["partition"])
            if "conditions" in rule_info:
                parts.extend(rule_info["conditions"])
            if "dedup" in rule_info:
                parts.append(rule_info["dedup"])
            lines.append(
                f"| {rule_name} | {rule_desc} | `{' AND '.join(parts) if parts else '-'}` |"
            )
        lines.append("")

    return "\n".join(lines)


def _domains_to_markdown(domains: list[dict]) -> str:
    """将领域 JSON 列表聚合为单一大 Markdown 文档（兼容旧模式）。"""
    lines = [
        "# Aqueduct 可视化知识库（本体模型）",
        "",
        f"> **自动生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "> **说明**: 本文档由脚本自动聚合生成。**JSON 用于 AI 执行，本 MD 用于人工审计。**",
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


def _generate_index(domains: list[dict], base_dir: str) -> str:
    """生成 INDEX.md 总索引文档。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Aqueduct 知识库索引",
        "",
        f"> **自动更新时间**: {now}",
        "> **说明**: 本文件为知识库总入口。每个业务域有独立的 `domain.json`（机器执行）和 `semantic-model.md`（人工审计）。",
        "",
        "---",
        "",
        "## 目录结构",
        "",
        "```",
        f"{base_dir}/",
        "├── INDEX.md                    # 本文件（总入口）",
        "├── domains/",
        "│   ├── {domain_id}/",
        "│   │   ├── domain.json         # 机器执行（Pydantic 校验）",
        "│   │   └── semantic-model.md   # 单域审计文档",
        "│   └── ...",
        "```",
        "",
        "---",
        "",
        "## 业务域列表",
        "",
        "| 域 ID | 名称 | 描述 | 版本 | 审计文档 |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for domain in domains:
        domain_id = domain.get("domain_id", "unknown")
        name = domain.get("name", "Unknown")
        desc = domain.get("description", "-")
        # 截断过长的描述
        if len(desc) > 60:
            desc = desc[:57] + "..."
        ver = domain.get("version", "N/A")
        link = f"[查看](domains/{domain_id}/semantic-model.md)"
        lines.append(f"| `{domain_id}` | {name} | {desc} | {ver} | {link} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"**共 {len(domains)} 个业务域**")
    lines.append("")

    return "\n".join(lines)


def load_all_domains(domains_dir: str | Path) -> list[dict]:
    """加载所有业务域 JSON 文件。

    支持两种结构：
    - 扁平模式: domains_dir/*.json
    - 域目录模式: domains_dir/*/domain.json
    """
    domains_dir = Path(domains_dir)
    domains = []

    # 优先尝试域目录模式
    for domain_json in sorted(domains_dir.glob("*/domain.json")):
        with open(domain_json, encoding="utf-8") as f:
            domains.append(json.load(f))

    # 兼容扁平模式
    if not domains:
        for file_path in sorted(domains_dir.glob("*.json")):
            with open(file_path, encoding="utf-8") as f:
                domains.append(json.load(f))

    return domains


def _is_nested_structure(domains_dir: Path) -> bool:
    """检查是否为域目录模式。"""
    return any(domains_dir.glob("*/domain.json"))


@register_tool
class SemanticTool(BaseTool):
    """语义文档生成工具 — 注册到全局工具注册中心。

    支持三种模式：
    - per_domain: 为每个域生成独立的 semantic-model.md（推荐）
    - index: 生成 INDEX.md 总索引
    - all: 同时生成 per_domain + index
    - aggregated: 生成单一大文件（兼容旧模式）
    """

    name = "semantic"
    description = "语义文档生成 — 支持按域生成审计文档和总索引"

    def execute(self, **kwargs: Any) -> ToolResult:
        domains_dir = kwargs.get("domains_dir", "knowledge/domains")
        output_path = kwargs.get("output_path", "")
        mode = kwargs.get("mode", "all")

        domains_dir = Path(domains_dir)
        if not domains_dir.exists():
            return ToolResult(
                success=False,
                error=f"业务域目录不存在: {domains_dir}",
            )

        domains = load_all_domains(domains_dir)
        if not domains:
            return ToolResult(
                success=False,
                error="未找到业务域 JSON 文件",
            )

        results = []
        is_nested = _is_nested_structure(domains_dir)

        if mode in ("per_domain", "all") and is_nested:
            # 为每个域生成独立的 semantic-model.md
            for domain in domains:
                domain_id = domain.get("domain_id", "unknown")
                domain_dir = domains_dir / domain_id
                if not domain_dir.exists():
                    domain_dir = domains_dir  # fallback to flat structure

                md_content = _domain_to_markdown(domain)
                md_path = domain_dir / "semantic-model.md"
                md_path.write_text(md_content, encoding="utf-8")
                results.append(str(md_path))

        if mode in ("index", "all"):
            # 生成 INDEX.md
            base_dir = str(domains_dir.parent)
            index_content = _generate_index(domains, base_dir)
            index_path = domains_dir.parent / "INDEX.md"
            if output_path:
                index_path = Path(output_path)
            index_path.write_text(index_content, encoding="utf-8")
            results.append(str(index_path))

        if mode == "aggregated":
            # 兼容旧模式：生成单一大文件
            md_content = _domains_to_markdown(domains)
            agg_path = (
                Path(output_path) if output_path else domains_dir.parent / "semantic-model.md"
            )
            agg_path.write_text(md_content, encoding="utf-8")
            results.append(str(agg_path))

        return ToolResult(
            success=True,
            data={"files": results, "domain_count": len(domains), "mode": mode},
            metadata={"status": "generated"},
        )


# ============================================================
# 兼容别名（供测试使用）
# ============================================================

domains_to_markdown = _domains_to_markdown
domain_to_markdown = _domain_to_markdown
generate_mermaid_er = _generate_mermaid_er
generate_index = _generate_index
