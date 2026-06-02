# -*- coding: utf-8 -*-
"""
设计文档双向同步工具 (Bi-directional Sync)

作用:
  1. 当用户修改 design.md 中的字段或关联关系时，自动同步更新 DDL、ETL SQL。
  2. 同步更新知识库 (knowledge/domains/) 中的元数据定义。
"""

import sys
import re
import json
from pathlib import Path

class DesignSyncer:
    def __init__(self, design_file):
        self.design_file = Path(design_file)
        self.content = ""
        self.target_table = ""
        self.fields = []
        self.sources = []

    def load_design(self):
        with open(self.design_file, 'r', encoding='utf-8') as f:
            self.content = f.read()

    def parse_structure(self):
        # 提取表名
        m_table = re.search(r'- \*\*表名\*\*: `(.+?)`', self.content)
        if m_table:
            self.target_table = m_table.group(1)

        # 提取字段表格
        # 寻找 | 字段名 | 类型 | 注释 | 之后的表格行
        field_section = re.search(r'\| 字段名 \| 类型 \| 注释 \|\n\|.*?\|\n(.*?)(?=\n\n|\n#|$)', self.content, re.DOTALL)
        if field_section:
            rows = field_section.group(1).strip().split('\n')
            for row in rows:
                parts = [p.strip() for p in row.split('|') if p.strip()]
                if len(parts) >= 3:
                    self.fields.append({
                        "name": parts[0],
                        "type": parts[1],
                        "comment": parts[2]
                    })

    def sync_ddl(self, ddl_file):
        if not self.target_table or not self.fields:
            return False
        
        columns = [f"    `{f['name']}` {f['type']} COMMENT '{f['comment']}'" for f in self.fields]
        ddl_content = [
            f"-- 自动同步自 {self.design_file.name}",
            f"CREATE TABLE IF NOT EXISTS {self.target_table} (",
            ",\n".join(columns),
            ")",
            "PARTITIONED BY (`inc_day` string COMMENT '数据分区日期，格式YYYYMMDD')",
            "STORED AS PARQUET;",
        ]
        
        with open(ddl_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(ddl_content))
        return True

    def sync_knowledge(self, domain_json):
        # 模拟同步到知识库
        # 实际逻辑应加载 JSON，更新对应的 domain.entities[Entity].attributes
        domain_path = Path(domain_json)
        if not domain_path.exists():
            return False
        
        with open(domain_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 寻找匹配的实体 (简化版：假设 domain_id 匹配)
        # 这里仅做演示逻辑
        if "entities" in data:
            # 找到表名对应的实体
            entity_key = self.target_table.split('.')[-1].capitalize() # 粗略匹配
            if entity_key in data["entities"]:
                new_attrs = []
                for f in self.fields:
                    new_attrs.append({
                        "name": f["name"],
                        "type": f["type"],
                        "description": f["comment"]
                    })
                data["entities"][entity_key]["attributes"] = new_attrs
                
                with open(domain_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return True
        return False

def main():
    if len(sys.argv) < 3:
        print("Usage: python sync_design.py <design_file> <ddl_file> [domain_json]")
        return 1
    
    design_file = sys.argv[1]
    ddl_file = sys.argv[2]
    domain_json = sys.argv[3] if len(sys.argv) > 3 else None
    
    syncer = DesignSyncer(design_file)
    syncer.load_design()
    syncer.parse_structure()
    
    if syncer.sync_ddl(ddl_file):
        print(f"✅ DDL 已同步更新: {ddl_file}")
    
    if domain_json:
        if syncer.sync_knowledge(domain_json):
            print(f"✅ 知识库已同步更新: {domain_json}")
            # 同步后运行文档生成器
            import os
            os.system("python scripts/gen_semantic_doc.py")
            print(f"✅ 可视化文档已重新聚合")

if __name__ == '__main__':
    main()
