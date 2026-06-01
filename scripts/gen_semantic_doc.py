# -*- coding: utf-8 -*-
"""
语义层文档转换工具 (JSON -> Markdown + Mermaid)

作用：
  解决“JSON 适合 AI 但不便人工审查”的问题。
  自动读取 semantic-model.json，生成包含可视化关系图（Mermaid）的 Markdown 文档。
"""

import json
from pathlib import Path
from datetime import datetime

def load_semantic_model(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def generate_mermaid_er(domain):
    """生成 Mermaid ER 图代码"""
    mermaid = ["```mermaid", "erDiagram"]
    
    # 实体定义
    for ent_name, ent_info in domain.get('entities', {}).items():
        # Mermaid 属性简写
        mermaid.append(f"    {ent_name} {{")
        pk = ent_info.get('primary_key', '')
        if pk:
            mermaid.append(f"        string {pk} PK")
        mermaid.append("    }")

    # 关系定义
    for rel in domain.get('relationships', []):
        from_ent = rel.get('from')
        to_ent = rel.get('to')
        # 简单处理基数，默认为 ||--o{
        mermaid.append(f"    {from_ent} ||--o{{ {to_ent} : \"{rel.get('name', rel.get('description', ''))}\"")
    
    mermaid.append("```")
    return "\n".join(mermaid)

def json_to_markdown(data):
    lines = [
        f"# {data.get('project', 'Semantic Layer')} 可视化知识库",
        "",
        f"> **版本**: {data.get('version', 'Unknown')}",
        f"> **自动生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "> **说明**: 本文档由脚本自动从 `semantic-model.json` 生成。**JSON 用于 AI 执行，本 MD 用于人工审计。**",
        "",
        "---",
        ""
    ]

    for domain in data.get('domains', []):
        lines.append(f"## 业务域：{domain.get('name')}")
        lines.append(f"- **ID**: `{domain.get('domain_id')}`")
        lines.append(f"- **描述**: {domain.get('description')}")
        lines.append("")
        
        lines.append("### 1. 关系拓扑图 (Relationship Map)")
        lines.append(generate_mermaid_er(domain))
        lines.append("")
        
        lines.append("### 2. 核心实体 (Entities)")
        lines.append("| 实体名 | 主键 | 物理来源 | 描述 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for ent_name, ent_info in domain.get('entities', {}).items():
            lines.append(f"| {ent_name} | `{ent_info.get('primary_key', '-')}` | `{ent_info.get('source', '-')}` | {ent_info.get('description', '')} |")
        lines.append("")
        
        lines.append("### 3. 指标口径 (Metrics)")
        lines.append("| 指标名称 | 计算表达式 | 过滤条件 | 单位 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for m_id, m_info in domain.get('metrics', {}).items():
            lines.append(f"| {m_info.get('name')} | `{m_info.get('expression')}` | `{m_info.get('filter', '-')}` | {m_info.get('unit', '-')} |")
        lines.append("")

        if domain.get('derived_attributes'):
            lines.append("### 4. 派生属性/转换规则")
            lines.append("| 属性名 | 逻辑说明 | 枚举值 |")
            lines.append("| :--- | :--- | :--- |")
            for attr_name, attr_info in domain.get('derived_attributes', {}).items():
                values = ", ".join(attr_info.get('values', []))
                lines.append(f"| {attr_name} | {attr_info.get('logic', '')} | {values} |")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)

def main():
    json_path = Path("knowledge/semantic-model.json")
    output_path = Path("knowledge/semantic-model.md")
    
    if not json_path.exists():
        print(f"Error: {json_path} not found.")
        return

    try:
        data = load_semantic_model(json_path)
        md_content = json_to_markdown(data)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"Success: Documentation generated at {output_path}")
    except Exception as e:
        print(f"Error during generation: {e}")

if __name__ == "__main__":
    main()
