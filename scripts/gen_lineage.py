# -*- coding: utf-8 -*-
"""
自动化血缘解析工具 (Lineage Generator)

功能：
  1. 解析表级血缘 (Table-level Lineage)
  2. 解析字段级血缘 (Field-level Lineage)
  3. 生成 Mermaid 格式的可视化图表
  4. 自动更新至设计文档
"""

import sys
import re
import json
from pathlib import Path

# 预编译正则
RE_INSERT = re.compile(r'insert\s+(?:overwrite|into)\s+table\s+(\w+\.\w+)', re.IGNORECASE)
RE_TABLE_NAME = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b')
RE_SELECT_BLOCK = re.compile(r'select\s+(.*?)\s+from', re.IGNORECASE | re.DOTALL)
RE_FIELD_ALIAS = re.compile(r'([\w\(\)\.\s+\-*/]+)\s+as\s+(\w+)', re.IGNORECASE)
RE_JOIN_ALIAS = re.compile(r'\b(\w+\.\w+)\s+(\w+)\b', re.IGNORECASE)

class LineageParser:
    def __init__(self, sql_file):
        self.sql_file = Path(sql_file)
        self.sql_content = ""
        self.target_table = "unknown_target"
        self.source_tables = []
        self.field_lineage = [] # 存储格式: {"target_field": "", "sources": [{"table": "", "field": ""}]}

    def load_sql(self):
        with open(self.sql_file, 'r', encoding='utf-8') as f:
            # 简单清洗：去除单行注释
            content = f.read()
            self.sql_content = re.sub(r'--.*', '', content)

    def parse_table_lineage(self):
        # 1. 提取目标表
        m_target = RE_INSERT.search(self.sql_content)
        if m_target:
            self.target_table = m_target.group(1)
        
        # 2. 提取所有库.表
        all_tables = RE_TABLE_NAME.findall(self.sql_content)
        seen = {self.target_table}
        for db, tbl in all_tables:
            full = f"{db}.{tbl}"
            if full not in seen:
                self.source_tables.append(full)
                seen.add(full)

    def parse_field_lineage(self):
        """简单的字段血缘解析逻辑"""
        # 1. 建立表别名映射
        alias_map = {} # alias -> table
        matches = RE_JOIN_ALIAS.findall(self.sql_content)
        for tbl, alias in matches:
            if '.' in tbl: # 确保是库.表
                alias_map[alias] = tbl

        # 2. 提取 SELECT 块
        select_match = RE_SELECT_BLOCK.search(self.sql_content)
        if select_match:
            fields_str = select_match.group(1)
            # 解析 col as alias
            field_matches = RE_FIELD_ALIAS.findall(fields_str)
            for raw_col, alias in field_matches:
                raw_col = raw_col.strip()
                source_info = {"table": "unknown", "field": raw_col}
                
                # 尝试识别 alias.field 格式
                if '.' in raw_col:
                    parts = raw_col.split('.')
                    if parts[0] in alias_map:
                        source_info["table"] = alias_map[parts[0]]
                        source_info["field"] = parts[1]
                
                self.field_lineage.append({
                    "target_field": alias,
                    "sources": [source_info]
                })

    def generate_mermaid(self):
        lines = ["### 1. 表级血缘图", "```mermaid", "graph LR"]
        for src in self.source_tables:
            lines.append(f"    {src.replace('.', '_')} --> {self.target_table.replace('.', '_')}")
        lines.append("```")
        
        if self.field_lineage:
            lines.append("\n### 2. 核心字段映射图")
            lines.append("```mermaid")
            lines.append("graph TD")
            for item in self.field_lineage:
                t_field = item["target_field"]
                for src in item["sources"]:
                    s_node = f"{src['table'].replace('.', '_')}_{src['field']}"
                    t_node = f"{self.target_table.replace('.', '_')}_{t_field}"
                    lines.append(f"    {s_node}[{src['table']}.{src['field']}] --> {t_node}[{self.target_table}.{t_field}]")
            lines.append("```")
        
        return "\n".join(lines)

    def update_design_doc(self, design_file):
        design_path = Path(design_file)
        if not design_path.exists():
            return False
        
        lineage_md = f"\n\n## 十一、数据血缘联动 (Lineage)\n\n{self.generate_mermaid()}\n"
        
        with open(design_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        if "## 十一、数据血缘联动" in content:
            content = re.sub(r'## 十一、数据血缘联动.*?(?=##|$)', lineage_md, content, flags=re.DOTALL)
        else:
            content += lineage_md
            
        with open(design_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True

def main():
    if len(sys.argv) < 2:
        print("Usage: python gen_lineage.py <sql_file> [design_file]")
        return 1
    
    sql_file = sys.argv[1]
    design_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    parser = LineageParser(sql_file)
    parser.load_sql()
    parser.parse_table_lineage()
    parser.parse_field_lineage()
    
    if design_file:
        if parser.update_design_doc(design_file):
            print(f"✅ 血缘信息已更新至: {design_file}")
    else:
        print(parser.generate_mermaid())

if __name__ == "__main__":
    main()
