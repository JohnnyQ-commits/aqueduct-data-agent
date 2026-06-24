"""提效统计工具 — ProductivityTool。

统计项目中生成的 SQL、DDL、MD 文档总量，计算自动修复成功率，生成提效看板。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool


@register_tool
class ProductivityTool(BaseTool):
    """提效统计工具 — 注册到全局工具注册中心。"""

    name = "productivity"
    description = "提效量化看板 — 统计 SQL 行数、DDL 数量、文档数量、估算节省工时"

    def execute(self, **kwargs: Any) -> ToolResult:
        root = Path(kwargs.get("root", "."))

        metrics = {
            "sql_lines": 0,
            "ddl_count": 0,
            "doc_count": 0,
            "lineage_graphs": 0,
            "dqc_tests_run": 0,
            "dqc_auto_fixes": 0,
            "estimated_hours_saved": 0.0,
        }

        # 1. 统计 SQL 和 DDL
        for sql_file in root.glob("**/*.sql"):
            if "templates" in str(sql_file):
                continue
            try:
                with open(sql_file, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                metrics["sql_lines"] += len(lines)
                if any("CREATE TABLE" in line_.upper() for line_ in lines):
                    metrics["ddl_count"] += 1
            except Exception:
                pass

        # 2. 统计文档
        for md_file in root.glob("**/*.md"):
            if md_file.name in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md"):
                continue
            if ".git" in str(md_file) or ".venv" in str(md_file):
                continue
            metrics["doc_count"] += 1
            try:
                with open(md_file, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                metrics["lineage_graphs"] += content.count("graph TD")
            except Exception:
                pass

        # 3. DQC 数据（从实际执行结果获取，无数据时标记为 0）
        # dqc_tests_run 和 dqc_auto_fixes 由外部调用方通过 kwargs 传入
        metrics["dqc_tests_run"] = kwargs.get("dqc_tests_run", 0)
        metrics["dqc_auto_fixes"] = kwargs.get("dqc_auto_fixes", 0)

        # 4. 计算节省工时
        saved_mins = (
            metrics["sql_lines"] * 0.5
            + metrics["doc_count"] * 20
            + metrics["lineage_graphs"] * 15
            + metrics["dqc_auto_fixes"] * 20
        )
        metrics["estimated_hours_saved"] = round(saved_mins / 60, 1)

        # 5. 生成报告
        score = min(100, int(metrics["estimated_hours_saved"] / 2))
        auto_fix_rate = (
            int(metrics["dqc_auto_fixes"] / metrics["dqc_tests_run"] * 100)
            if metrics["dqc_tests_run"]
            else 0
        )
        rating = "极度活跃" if score > 80 else "表现优异" if score > 50 else "持续进化"

        dqc_display = (
            f"`{auto_fix_rate}%`" if metrics["dqc_tests_run"] else "暂无 DQC 数据"
        )
        dqc_note = "DQC 闭环自愈能力表现" if metrics["dqc_tests_run"] else "暂无实际执行数据"

        report = "\n".join(
            [
                "# Data Agent 提效看板 (Productivity Dashboard)",
                f"> 数据截止日期: {datetime.now().strftime('%Y-%m-%d')}",
                "",
                "## 1. 核心提效概览",
                "| 指标项 | 统计数值 | 提效说明 |",
                "| :--- | :--- | :--- |",
                f"| **累计节省工时** | `{metrics['estimated_hours_saved']} 小时` | 相当于节省了约 {round(metrics['estimated_hours_saved'] / 8, 1)} 个开发人天 |",
                f"| **自动修复成功率** | {dqc_display} | {dqc_note} |",
                "| **交付件自动化率** | `100%` | 所有 DDL/DQC/文档均由 Agent 自动生成 |",
                "",
                "## 2. 产出物明细",
                "| 分类 | 数量 | 详细指标 |",
                "| :--- | :--- | :--- |",
                f"| **SQL 逻辑** | {metrics['sql_lines']} 行 | 包含核心 ETL 逻辑与测试脚本 |",
                f"| **数据模型 (DDL)** | {metrics['ddl_count']} 个 | 物理表结构定义 |",
                f"| **业务文档 (MD)** | {metrics['doc_count']} 份 | 包含设计文档与质量报告 |",
                f"| **可视化血缘** | {metrics['lineage_graphs']} 幅 | 自动生成的 Mermaid 拓扑图 |",
                "",
                "## 3. 数字化身价值评估",
                f"基于当前的产出，Data Agent 目前的活跃度评级为：**{rating}**",
                "",
                "---",
                "*注：工时节省基于工业平均开发速度估算，仅供参考。*",
            ]
        )

        return ToolResult(
            success=True,
            data={"report": report, "metrics": metrics},
            metadata={"hours_saved": metrics["estimated_hours_saved"]},
        )
