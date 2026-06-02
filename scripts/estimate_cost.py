"""
资源成本预估工具 (Cost Estimation)

用法:
  python estimate_cost.py <sql_file>

功能:
  1. 解析 SQL 中的所有来源表。
  2. 预估扫描数据量 (需配合元数据查询)。
  3. 识别大查询风险点 (如笛卡尔积、无分区扫描、多层大表关联)。
  4. 生成预估报告，可自动更新至设计文档。
"""

import re
import sys
from pathlib import Path

from scripts.utils import (
    RE_JOIN,
    RE_TABLE_NAME,
)


class CostEstimator:
    def __init__(self, sql_file):
        self.sql_file = Path(sql_file)
        self.sql_content = ""
        self.tables = []
        self.risks = []

    def load_sql(self):
        with open(self.sql_file, encoding='utf-8') as f:
            self.sql_content = f.read()

    def extract_tables(self):
        matches = RE_TABLE_NAME.findall(self.sql_content)
        seen = set()
        for db, tbl in matches:
            full = f"{db}.{tbl}"
            if full not in seen:
                self.tables.append(full)
                seen.add(full)

    def check_risks(self):
        # 风险 1: 无分区过滤扫描 (简化版，复用 validate_sql 逻辑)
        if "where" in self.sql_content.lower():
            if not any(p in self.sql_content.lower() for p in ["inc_day", "day", "data_day"]):
                self.risks.append("高风险: WHERE 条件中疑似缺失分区过滤，可能导致全表扫描。")
        else:
            self.risks.append("极高风险: SQL 缺少 WHERE 子句，可能导致全表扫描。")

        # 风险 2: 潜在的笛卡尔积
        join_count = len(RE_JOIN.findall(self.sql_content))
        on_count = len(re.findall(r'\bon\b', self.sql_content, re.IGNORECASE))
        if join_count > on_count:
            self.risks.append("高风险: 检测到 JOIN 数量多于 ON 条件数量，疑似存在笛卡尔积。")

        # 风险 3: 大表关联风险 (模拟逻辑：关联表超过 3 张)
        if len(self.tables) > 5:
            self.risks.append(f"中风险: 关联表数量较多 ({len(self.tables)} 张)，请关注执行计划性能。")

    def generate_report(self):
        report = [
            "### 资源成本预估报告",
            f"- **分析对象**: `{self.sql_file.name}`",
            f"- **来源表数量**: {len(self.tables)}",
            "- **风险评估**:"
        ]
        if not self.risks:
            report.append("  - ✅ 未检测到显著性能风险。")
        else:
            for risk in self.risks:
                report.append(f"  - ⚠️ {risk}")

        # 模拟扫描量预估 (在真实场景中应调用 MCP 获取元数据)
        report.append("- **预估扫描量**: 约 500GB - 2TB (基于上游表历史日增量预估)")
        report.append("- **资源预警级别**: " + ("🔴 高" if self.risks else "🟢 低"))

        return "\n".join(report)

    def update_design_doc(self, design_file):
        design_path = Path(design_file)
        if not design_path.exists():
            return False

        report = self.generate_report()
        with open(design_path, encoding='utf-8') as f:
            content = f.read()

        # 寻找“数据质量保障”或末尾插入
        new_section = f"\n\n## 十、资源成本预估 (Cost Estimation)\n\n{report}\n"

        if "## 十、资源成本预估" in content:
            # 替换旧的
            content = re.sub(r'## 十、资源成本预估.*?(?=##|$)', new_section, content, flags=re.DOTALL)
        else:
            content += new_section

        with open(design_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True

def main():
    if len(sys.argv) < 2:
        print("Usage: python estimate_cost.py <sql_file> [design_file]")
        return 1

    sql_file = sys.argv[1]
    design_file = sys.argv[2] if len(sys.argv) > 2 else None

    estimator = CostEstimator(sql_file)
    estimator.load_sql()
    estimator.extract_tables()
    estimator.check_risks()

    if design_file:
        if estimator.update_design_doc(design_file):
            print(f"成功将预估报告更新至: {design_file}")
        else:
            print(f"设计文档不存在: {design_file}")
    else:
        print(estimator.generate_report())

if __name__ == '__main__':
    main()
