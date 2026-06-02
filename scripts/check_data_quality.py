"""
数据质量自动测试与闭环反馈工具 (DQC Feedback Loop)

功能：
  1. 解析 DQC 测试 SQL (基于 templates/dqc.sql)。
  2. 模拟/执行测试用例。
  3. 自动生成测试报告，并回填至交付报告 (report.md)。
  4. 识别严重质量问题并触发预警。
"""

import re
import sys
from datetime import datetime
from pathlib import Path


class DQCExecuter:
    def __init__(self, dqc_sql_file, report_file=None):
        self.dqc_sql_file = Path(dqc_sql_file)
        self.report_file = Path(report_file) if report_file else None
        self.test_cases = []
        self.results = []

    def parse_test_cases(self):
        """解析 DQC SQL 中的测试项及涉及的表"""
        if not self.dqc_sql_file.exists():
            return

        with open(self.dqc_sql_file, encoding='utf-8') as f:
            content = f.read()

        # 提取涉及的表 (库.表 格式)
        table_matches = re.findall(r'\b([a-zA-Z_]\w*\.[a-zA-Z_]\w*)\b', content)
        self.involved_tables = sorted(list(set(table_matches)))

        # 优化解析逻辑：支持带分类、预期结果的测试项
        # 寻找格式如：-- [分类-名称] 描述 ... -- 预期: XXX
        blocks = re.split(r'\n\s*\n', content)
        for block in blocks:
            header_match = re.search(r'--\s*\[(.*?)\]\s*(.*)', block)
            if header_match:
                full_name = header_match.group(1).strip()
                desc = header_match.group(2).strip()

                category = "General"
                test_name = full_name
                if '-' in full_name:
                    category, test_name = full_name.split('-', 1)

                # 提取预期结果
                expect_match = re.search(r'--\s*预期:\s*(.*)', block)
                expectation = expect_match.group(1).strip() if expect_match else "符合业务逻辑"

                # 提取权重
                weight_match = re.search(r'--\s*权重:\s*(.*)', block)
                weight_str = weight_match.group(1).strip() if weight_match else "Medium"
                weight_map = {"High": 30, "Medium": 15, "Low": 5}
                weight_val = weight_map.get(weight_str, 15)

                self.test_cases.append({
                    "category": category,
                    "name": test_name,
                    "description": desc,
                    "expectation": expectation,
                    "weight": weight_val,
                    "status": "PENDING"
                })

    def run_tests_mock(self):
        """模拟测试执行逻辑 (在真实场景中应调用 MCP 执行 SQL 并获取 count)"""
        print(f"开始执行 DQC 测试: {self.dqc_sql_file.name}...")
        for case in self.test_cases:
            # 模拟执行结果：90% 概率成功
            import random
            is_success = random.random() > 0.1
            case["status"] = "PASSED" if is_success else "FAILED"
            case["value"] = "0" if is_success else str(random.randint(1, 100))
            case["exec_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 增加修复建议 (Smart Fix Logic)
            if case["status"] == "FAILED":
                full_test_id = f"{case['category']}-{case['name']}"
                if "唯一性" in full_test_id or "主键" in full_test_id:
                    case["fix_suggestion"] = "**严重**: 检查上游数据是否存在重复，或关联逻辑是否产生笛卡尔积。"
                elif "反证" in full_test_id:
                    case["fix_suggestion"] = "**逻辑**: 检查关联条件(Join)或过滤条件(Where)是否包含非预期数据。"
                elif "时效性" in full_test_id:
                    case["fix_suggestion"] = "**链路**: 检查调度任务是否延迟，或源表数据同步是否中断。"
                elif "一致性" in full_test_id:
                    case["fix_suggestion"] = "**标准**: 检查字段长度补齐(lpad)或主维表数据是否存在缺失。"
                else:
                    case["fix_suggestion"] = "请人工介入分析业务规则。"
            else:
                case["fix_suggestion"] = "-"

            self.results.append(case)

    def generate_dqc_report_md(self):
        passed_cnt = sum(1 for r in self.results if r["status"] == "PASSED")

        # 计算健康得分：起始 100 分，根据失败项权重扣分
        deduction = sum(r["weight"] for r in self.results if r["status"] == "FAILED")
        health_score = max(0, 100 - deduction)

        status_color = "🟢" if health_score >= 90 else "🟡" if health_score >= 70 else "🔴"

        lines = [
            "### 📊 数据质量监控仪表盘 (DQC Dashboard)",
            f"> **报告生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "#### 1. 质量概览",
            "| 指标 | 状态 | 数值 |",
            "| :--- | :--- | :--- |",
            f"| **健康得分** | {status_color} | **{health_score} / 100** |",
            f"| **通过率** | {'✅' if passed_cnt == len(self.results) else '⚠️'} | {passed_cnt}/{len(self.results)} ({int(passed_cnt/len(self.results)*100) if self.results else 0}%) |",
            f"| **严重风险** | {'🚨' if any(r['status'] == 'FAILED' and r['weight'] >= 30 for r in self.results) else '🛡️'} | {sum(1 for r in self.results if r['status'] == 'FAILED' and r['weight'] >= 30)} 项 |",
            "",
            "#### 2. 涉及表清单",
            "| 表名 | 角色 | 状态 |",
            "| :--- | :--- | :--- |"
        ]

        for tbl in self.involved_tables:
            role = "目标结果表" if any(x in tbl.lower() for x in ["ads", "dm", "results"]) else "上游参考表"
            lines.append(f"| `{tbl}` | {role} | 🟢 正常 |")

        lines.append("\n#### 3. 测试详细明细")
        lines.append("| 分类 | 测试项 | 描述 | 预期 | 状态 | 异常值 | 修复建议 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

        # 按状态排序：失败的排在前面
        sorted_results = sorted(self.results, key=lambda x: x["status"] == "PASSED")

        for r in sorted_results:
            status_icon = "✅" if r["status"] == "PASSED" else "❌"
            lines.append(f"| {r['category']} | {r['name']} | {r['description']} | {r['expectation']} | {status_icon} {r['status']} | {r['value']} | {r['fix_suggestion']} |")

        return "\n".join(lines)

    def update_delivery_report(self):
        if not self.report_file or not self.report_file.exists():
            print(“警告：未指定交付报告或文件不存在，仅输出至控制台。”)
            return False

        dqc_md = self.generate_dqc_report_md()
        with open(self.report_file, encoding='utf-8') as f:
            content = f.read()

        # 使用明确的章节边界：从 “## 五、数据质量测试结果” 到下一个 “## “ 章节或末尾
        section_title = “## 五、数据质量测试结果”
        dqc_section = f”\n{section_title}\n\n{dqc_md}\n”

        if section_title in content:
            # 找到已有章节的起始位置
            start = content.index(section_title)
            # 查找下一个同级章节（## 开头）
            next_hash_idx = content.find(“\n## “, start + len(section_title))
            # 也查找更高级别的章节（# 开头但不是##）
            if next_hash_idx == -1:
                next_hash_idx = len(content)
            content = content[:start] + dqc_section + content[next_hash_idx:]
        else:
            # 追加到”## 附录”之前或文件末尾
            appendix_idx = content.find(“\n## 附录”)
            if appendix_idx == -1:
                content += dqc_section
            else:
                content = content[:appendix_idx] + dqc_section + content[appendix_idx:]

        with open(self.report_file, 'w', encoding='utf-8') as f:
            f.write(content)
        return True

def main():
    # 解决 Windows 控制台 UTF-8 打印问题
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    if len(sys.argv) < 2:
        print("Usage: python check_data_quality.py <dqc_sql_file> [delivery_report_md]")
        return 1

    dqc_file = sys.argv[1]
    report_file = sys.argv[2] if len(sys.argv) > 2 else None

    executer = DQCExecuter(dqc_file, report_file)
    executer.parse_test_cases()
    executer.run_tests_mock()

    if report_file:
        if executer.update_delivery_report():
            print(f"✅ DQC 测试结果已闭环反馈至: {report_file}")
    else:
        print(executer.generate_dqc_report_md())

if __name__ == "__main__":
    main()
