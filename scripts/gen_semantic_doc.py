"""
语义层文档转换工具 (JSON -> Markdown + Mermaid)

作用：
  解决"JSON 适合 AI 但不便人工审查"的问题。
  自动读取 knowledge/domains/*.json，生成包含可视化关系图（Mermaid）的聚合 Markdown 文档。

支持的本体字段：
  entities, hierarchy, relationships (含 cardinality), metrics,
  computation_chains, derived_attributes, business_rules, axioms, filter_rules
"""

import glob
import json
from datetime import datetime
from pathlib import Path


def load_all_domains(domains_dir):
    domains = []
    for file_path in glob.glob(str(domains_dir / "*.json")):
        with open(file_path, encoding='utf-8') as f:
            domains.append(json.load(f))
    return domains


def generate_mermaid_er(domain):
    """生成 Mermaid ER 图代码（支持 cardinality 标注）"""
    mermaid = ["```mermaid", "erDiagram"]

    # 实体定义（含属性）
    entities = domain.get('entities', {})
    if not entities:
        return ""

    for ent_name, ent_info in entities.items():
        mermaid.append(f"    {ent_name} {{")
        pk = ent_info.get('primary_key', '')
        for attr in ent_info.get('attributes', []):
            attr_name = attr.get('name', '')
            attr_type = attr.get('type', 'string')
            constraints = attr.get('constraints', [])
            if attr_name == pk:
                mermaid.append(f"        {attr_type} {attr_name} PK")
            elif 'NOT NULL' in constraints:
                mermaid.append(f"        {attr_type} {attr_name} NOT-NULL")
            else:
                mermaid.append(f"        {attr_type} {attr_name}")
        mermaid.append("    }")

    # 关系定义（支持 cardinality 映射到 Mermaid 符号）
    cardinality_map = {
        "1:1": "||--||",
        "1:N": "||--o{",
        "N:1": "}o--||",
        "M:N": "}o--o{",
    }
    for rel in domain.get('relationships', []):
        from_ent = rel.get('from')
        to_ent = rel.get('to')
        card = rel.get('cardinality', '1:N')
        symbol = cardinality_map.get(card, "||--o{")
        desc = rel.get('description', rel.get('name', ''))
        mermaid.append(f"    {from_ent} {symbol} {to_ent} : \"{desc}\"")

    mermaid.append("```")
    return "\n".join(mermaid)


def domains_to_markdown(domains):
    lines = [
        "# Data Agent 可视化知识库（本体模型）",
        "",
        f"> **自动生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "> **说明**: 本文档由脚本自动从 `knowledge/domains/*.json` 聚合生成。**JSON 用于 AI 执行，本 MD 用于人工审计。**",
        "",
        "---",
        ""
    ]

    # 目录
    lines.append("## 目录")
    for domain in domains:
        name = domain.get('name', 'Unknown')
        lines.append(f"- [{name}](#业务域{name})")
    lines.append("\n---\n")

    for domain in domains:
        name = domain.get('name', 'Unknown')
        lines.append(f"## 业务域：{name}")
        lines.append(f"- **ID**: `{domain.get('domain_id')}`")
        lines.append(f"- **描述**: {domain.get('description')}")
        ver = domain.get('version', 'N/A')
        lines.append(f"- **版本**: {ver}")
        lines.append("")

        # 1. 关系拓扑图
        mermaid = generate_mermaid_er(domain)
        if mermaid:
            lines.append("### 1. 关系拓扑图 (Relationship Map)")
            lines.append(mermaid)
            lines.append("")

        # 2. 核心实体
        lines.append("### 2. 核心实体 (Entities)")
        lines.append("| 实体名 | 主键 | 属性数 | 物理来源 | 描述 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for ent_name, ent_info in domain.get('entities', {}).items():
            pk = ent_info.get('primary_key', '-')
            attrs = len(ent_info.get('attributes', []))
            src = ent_info.get('source', '-')
            desc = ent_info.get('description', '')
            lines.append(f"| {ent_name} | `{pk}` | {attrs} | `{src}` | {desc} |")
        lines.append("")

        # 3. 层级分类
        if domain.get('hierarchy'):
            lines.append("### 3. 层级分类 (Hierarchy)")
            for parent, children in domain['hierarchy'].items():
                lines.append(f"**{parent}**")
                for child_name, child_info in children.items():
                    rule = child_info.get('rule', '')
                    desc = child_info.get('description', '')
                    lines.append(f"- **{child_name}**: {desc}")
                    if rule:
                        lines.append(f"  - 规则: `{rule}`")
                lines.append("")

        # 4. 指标口径
        lines.append("### 4. 指标口径 (Metrics)")
        lines.append("| 指标名称 | 定义 | 计算式 | 过滤条件 | 单位 | 预警阈值 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for _m_id, m_info in domain.get('metrics', {}).items():
            name = m_info.get('name', '-')
            expr = m_info.get('expression', '-')
            filt = m_info.get('filter', '-')
            unit = m_info.get('unit', '-')
            threshold = m_info.get('risk_threshold', '-')
            definition = m_info.get('definition', '-')
            lines.append(f"| {name} | {definition} | `{expr}` | `{filt}` | {unit} | {threshold} |")
        lines.append("")

        # 5. 计算链路
        if domain.get('computation_chains'):
            lines.append("### 5. 计算链路 (Computation Chains)")
            lines.append("| 复合指标 | 业务定义 | 计算步骤 | 预警阈值 |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for chain_name, chain_info in domain.get('computation_chains', {}).items():
                steps = " -> ".join([
                    s.get('metric', s.get('operation', s.get('description', 'step')))
                    for s in chain_info.get('steps', [])
                ])
                lines.append(f"| {chain_name} | {chain_info.get('definition')} | `{steps}` | {chain_info.get('risk_threshold', '-')} |")
            lines.append("")

        # 6. 派生属性/转换规则
        if domain.get('derived_attributes'):
            lines.append("### 6. 派生属性/转换规则 (Derived Attributes)")
            lines.append("| 属性名 | 逻辑说明 | 枚举值 |")
            lines.append("| :--- | :--- | :--- |")
            for attr_name, attr_info in domain.get('derived_attributes', {}).items():
                values = ", ".join(attr_info.get('values', []))
                lines.append(f"| {attr_name} | {attr_info.get('logic', '')} | {values} |")
            lines.append("")

        # 7. 公理
        if domain.get('axioms'):
            lines.append("### 7. 领域公理 (Axioms)")
            lines.append("| 编号 | 公理描述 | 形式化表达 |")
            lines.append("| :--- | :--- | :--- |")
            for ax in domain.get('axioms', []):
                lines.append(f"| {ax['id']} | {ax['statement']} | `{ax['formal']}` |")
            lines.append("")

        # 8. 业务规则
        if domain.get('business_rules'):
            lines.append("### 8. 业务规则 (Business Rules)")
            lines.append("| 规则名 | 内容 |")
            lines.append("| :--- | :--- |")
            for rule_name, rule_desc in domain.get('business_rules', {}).items():
                lines.append(f"| {rule_name} | {rule_desc} |")
            lines.append("")

        # 9. 过滤规则
        if domain.get('filter_rules'):
            lines.append("### 9. 分区与过滤规则 (Filter Rules)")
            lines.append("| 规则名 | 说明 | 条件 |")
            lines.append("| :--- | :--- | :--- |")
            for rule_name, rule_info in domain.get('filter_rules', {}).items():
                desc = rule_info.get('description', '-')
                parts = []
                if 'partition' in rule_info:
                    parts.append(rule_info['partition'])
                if 'conditions' in rule_info:
                    parts.extend(rule_info['conditions'])
                if 'dedup' in rule_info:
                    parts.append(rule_info['dedup'])
                lines.append(f"| {rule_name} | {desc} | `{' AND '.join(parts) if parts else '-'}` |")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main():
    domains_dir = Path("knowledge/domains")
    output_path = Path("knowledge/semantic-model.md")

    if not domains_dir.exists():
        print(f"Error: {domains_dir} not found.")
        return

    try:
        domains = load_all_domains(domains_dir)
        if not domains:
            print("No domain JSON files found.")
            return

        md_content = domains_to_markdown(domains)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        print(f"Success: Documentation aggregated at {output_path}")
    except Exception as e:
        print(f"Error during generation: {e}")


if __name__ == "__main__":
    main()
