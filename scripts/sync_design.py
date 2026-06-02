"""
设计文档双向同步工具 (Bi-directional Sync)

作用:
  1. 当用户修改 design.md 中的字段或关联关系时，自动同步更新 DDL、ETL SQL。
  2. 同步更新知识库 (knowledge/domains/) 中的元数据定义。

用法:
  python sync_design.py <design_file> <ddl_file> [domain_json]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class DesignSyncer:
    def __init__(self, design_file: str | Path) -> None:
        self.design_file = Path(design_file)
        self.content = ""
        self.target_table = ""
        self.fields: list[dict[str, str]] = []
        self.sources: list[dict[str, str]] = []
        self.join_conditions: list[dict[str, str]] = []

    def load_design(self) -> None:
        with open(self.design_file, encoding="utf-8") as f:
            self.content = f.read()

    def parse_structure(self) -> None:
        # 提取表名
        m_table = re.search(r"- \*\*表名\*\*: `(.+?)`", self.content)
        if m_table:
            self.target_table = m_table.group(1)

        # 提取字段表格
        field_section = re.search(
            r"\| 字段名 \| 类型 \| 注释 \|\n\|.*?\|\n(.*?)(?=\n\n|\n#|$)",
            self.content,
            re.DOTALL,
        )
        if field_section:
            rows = field_section.group(1).strip().split("\n")
            for row in rows:
                parts = [p.strip() for p in row.split("|") if p.strip()]
                if len(parts) >= 3:
                    self.fields.append({
                        "name": parts[0],
                        "type": parts[1],
                        "comment": parts[2],
                    })

        # 提取数据来源（第五节）
        source_section = re.search(
            r"## 五、数据来源与关联关系\s*\n(.*?)(?=##|$)",
            self.content,
            re.DOTALL,
        )
        if source_section:
            src_text = source_section.group(1)
            table_matches = re.findall(
                r"\d+\.\s+\*\*([a-zA-Z_]\w*\.[a-zA-Z_]\w*)\*\*",
                src_text,
            )
            for tbl in table_matches:
                # 提取分区信息
                partition_match = re.search(
                    rf"{re.escape(tbl)}.*?`([^`]+)`",
                    src_text,
                )
                partition = partition_match.group(1) if partition_match else "未知"
                self.sources.append({
                    "table": tbl,
                    "partition": partition,
                })

        # 提取关联条件
        join_section = re.search(
            r"\| 关联表 \| 别名 \| 关联条件 \|\s*\n\|[-|]+\s*\n(.*?)(?=\n\n|\n#|$)",
            self.content,
            re.DOTALL,
        )
        if join_section:
            rows = join_section.group(1).strip().split("\n")
            for row in rows:
                parts = [p.strip() for p in row.split("|") if p.strip()]
                if len(parts) >= 3:
                    self.join_conditions.append({
                        "table": parts[0],
                        "alias": parts[1],
                        "condition": parts[2],
                    })

    def sync_ddl(self, ddl_file: str | Path) -> bool:
        if not self.target_table or not self.fields:
            return False

        columns = [
            f"    `{f['name']}` {f['type']} COMMENT '{f['comment']}'"
            for f in self.fields
        ]
        ddl_content = [
            f"-- 自动同步自 {self.design_file.name}",
            f"CREATE TABLE IF NOT EXISTS {self.target_table} (",
            ",\n".join(columns),
            ")",
            "PARTITIONED BY (`inc_day` string COMMENT '数据分区日期，格式YYYYMMDD')",
            "STORED AS PARQUET;",
        ]

        with open(ddl_file, "w", encoding="utf-8") as f:
            f.write("\n".join(ddl_content))
        return True

    def sync_knowledge(self, domain_json: str | Path) -> bool:
        """同步设计文档中的字段和关系至知识库 JSON。

        匹配策略：
          1. 精确匹配实体名（表名最后一段）
          2. 模糊匹配（实体描述中包含表名关键词）
          3. 无匹配时创建新实体
        """
        domain_path = Path(domain_json)
        if not domain_path.exists():
            return False

        with open(domain_path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        entities = data.get("entities", {})
        table_short_name = self.target_table.split(".")[-1]

        # 策略 1: 精确匹配实体名
        matched_key: str | None = None
        for key in entities:
            if key.lower() == table_short_name.lower():
                matched_key = key
                break

        # 策略 2: 模糊匹配（表名关键词在实体来源中）
        if not matched_key:
            for key, ent_info in entities.items():
                source = ent_info.get("source", "")
                if table_short_name.lower() in source.lower():
                    matched_key = key
                    break

        # 构建新属性列表
        new_attrs: list[dict[str, str]] = []
        for f in self.fields:
            new_attrs.append({
                "name": f["name"],
                "type": f["type"],
                "description": f["comment"],
            })

        if matched_key:
            # 更新已有实体
            entities[matched_key]["attributes"] = new_attrs
            entities[matched_key]["source"] = self.target_table
        else:
            # 创建新实体
            entities[table_short_name] = {
                "source": self.target_table,
                "attributes": new_attrs,
                "description": f"同步自设计文档 {self.design_file.name}",
            }
            data["entities"] = entities

        # 同步关联关系
        if self.join_conditions and "relationships" in data:
            relationships: list[dict[str, Any]] = data["relationships"]
            existing_pairs = {
                (r.get("from"), r.get("to"))
                for r in relationships
            }

            for jc in self.join_conditions:
                src_table = jc["table"]
                src_short = src_table.split(".")[-1]
                rel_key = (matched_key or table_short_name, src_short)

                if rel_key not in existing_pairs:
                    relationships.append({
                        "from": matched_key or table_short_name,
                        "to": src_short,
                        "join_type": "LEFT JOIN",
                        "condition": jc["condition"],
                        "description": f"同步自设计文档: {jc['table']} ({jc['alias']})",
                    })

        with open(domain_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return True

    def run_semantic_doc_gen(self) -> bool:
        """同步后重新聚合语义文档"""
        try:
            result = subprocess.run(
                [sys.executable, "scripts/gen_semantic_doc.py"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False


def main() -> int:
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

    if domain_json and syncer.sync_knowledge(domain_json):
        print(f"✅ 知识库已同步更新: {domain_json}")
        if syncer.run_semantic_doc_gen():
            print("✅ 可视化文档已重新聚合")
        else:
            print("⚠️ 可视化文档生成失败，可手动运行 gen_semantic_doc.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
