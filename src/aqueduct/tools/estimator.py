"""资源成本预估工具 — EstimatorTool。

基于 SQL 解析结果预估计算资源消耗和数据量。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 语义提取（2026-10-15 实录修复）：RE_TABLE_NAME 全局匹配把 alias.column
# 配对当表名（真实 3 源表被报 30 张）。改为 from/join 锚定 + 注释/字符串
# 剥离 + 大小写不敏感去重；JOIN/ON 笛卡尔积判据同样剥离噪声并感知
# full/left outer join 变体，消除误报。
# ---------------------------------------------------------------------------

_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_LINE_COMMENT = re.compile(r"--[^\n]*")
_RE_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

# 只认 from/join 后跟 库.表 的引用——select 列表里的 alias.column 不再误配
_RE_FROM_JOIN_TABLE = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_]\w*)\.([\w.]+)", re.IGNORECASE
)

# JOIN 变体全谱（含 full outer join / left outer join——旧判据只认
# full\s+join，outer 变体落进裸 join 分支导致计数错位）
_RE_JOIN_VARIANTS = re.compile(
    r"\b(?:cross\s+join|inner\s+join|left(?:\s+outer)?\s+join"
    r"|right(?:\s+outer)?\s+join|full(?:\s+outer)?\s+join|join)\b",
    re.IGNORECASE,
)
_RE_ON_OR_USING = re.compile(r"\bon\b|\busing\b", re.IGNORECASE)


def _strip_noise(sql: str) -> str:
    """剥离注释与字符串字面量——注释里的 join/on 示例不应参与统计。"""
    sql = _RE_BLOCK_COMMENT.sub(" ", sql)
    sql = _RE_STRING.sub("''", sql)
    sql = _RE_LINE_COMMENT.sub(" ", sql)
    return sql


class CostEstimator:
    """资源成本预估器（核心逻辑，与原脚本保持一致）。

    检查项:
      1. 无分区过滤扫描风险
      2. 潜在笛卡尔积（JOIN 多于 ON）
      3. 大表关联风险（关联表 > 5 张）
    """

    def __init__(self, sql_file: str | Path) -> None:
        self.sql_file = Path(sql_file)
        self.sql_content = ""
        self.tables: list[str] = []
        self.risks: list[str] = []

    def load_sql(self) -> None:
        """加载 SQL 文件内容。"""
        with open(self.sql_file, encoding="utf-8") as f:
            self.sql_content = f.read()

    def extract_tables(self) -> None:
        """从 SQL 中提取来源表（from/join 锚定，大小写不敏感去重）。"""
        sql = _strip_noise(self.sql_content)
        seen: set[str] = set()
        for db, tbl in _RE_FROM_JOIN_TABLE.findall(sql):
            full = f"{db}.{tbl}".rstrip(".")
            key = full.lower()
            if key in seen:
                continue
            seen.add(key)
            self.tables.append(full)

    def check_risks(self) -> None:
        """执行三项风险扫描。"""
        # 风险 1: 无分区过滤
        if "where" in self.sql_content.lower():
            if not any(p in self.sql_content.lower() for p in ["inc_day", "day", "data_day"]):
                self.risks.append("高风险: WHERE 条件中疑似缺失分区过滤，可能导致全表扫描。")
        else:
            self.risks.append("极高风险: SQL 缺少 WHERE 子句，可能导致全表扫描。")

        # 风险 2: 潜在笛卡尔积——噪声剥离后 JOIN 变体全谱计数，
        # on/using 与 join 一一对应时不误报（full outer join 链实录教训）
        sql = _strip_noise(self.sql_content)
        join_count = len(_RE_JOIN_VARIANTS.findall(sql))
        on_count = len(_RE_ON_OR_USING.findall(sql))
        if join_count > on_count:
            self.risks.append("高风险: 检测到 JOIN 数量多于 ON 条件数量，疑似存在笛卡尔积。")

        # 风险 3: 大表关联
        if len(self.tables) > 5:
            self.risks.append(
                f"中风险: 关联表数量较多 ({len(self.tables)} 张)，请关注执行计划性能。"
            )

    def generate_report(self) -> str:
        """生成 Markdown 格式的预估报告。"""
        report = [
            "### 资源成本预估报告",
            f"- **分析对象**: `{self.sql_file.name}`",
            f"- **来源表数量**: {len(self.tables)}",
            "- **风险评估**:",
        ]
        if not self.risks:
            report.append("  - ✅ 未检测到显著性能风险。")
        else:
            for risk in self.risks:
                report.append(f"  - ⚠️ {risk}")

        if self.risks:
            report.append("- **资源预警级别**: 🔴 高")
        else:
            report.append("- **资源预警级别**: 🟢 低")
        return "\n".join(report)

    def update_design_doc(self, design_file: str | Path) -> bool:
        """将预估报告更新至设计文档。"""
        design_path = Path(design_file)
        if not design_path.exists():
            return False

        report = self.generate_report()
        content = design_path.read_text(encoding="utf-8")

        section_header = "## 十、资源成本预估"
        next_section = "## 十一"
        new_section = f"\n\n{section_header} (Cost Estimation)\n\n{report}\n"

        if section_header in content:
            start = content.index(section_header)
            end_marker = content.find(next_section, start + len(section_header))
            if end_marker == -1:
                end_marker = len(content)
            content = content[:start] + new_section + content[end_marker:]
        else:
            content += new_section

        design_path.write_text(content, encoding="utf-8")
        return True


# ============================================================
# BaseTool 包装器
# ============================================================


@register_tool
class EstimatorTool(BaseTool):
    """资源成本预估工具 — 注册到全局工具注册中心。

    支持参数:
        sql_file: SQL 文件路径（必填）
        design_file: 设计文档路径（可选，传入则自动更新）
    """

    name = "estimator"
    description = "资源成本预估 — 扫描分区过滤、笛卡尔积、大表关联等风险，生成预警报告"

    def execute(self, **kwargs: Any) -> ToolResult:
        sql_file = kwargs.get("sql_file")
        if not sql_file:
            return ToolResult(success=False, error="缺少必填参数 sql_file")

        logger.info("成本预估开始: file=%s", sql_file)

        estimator = CostEstimator(sql_file)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        report_md = estimator.generate_report()

        # 可选：更新设计文档
        design_file = kwargs.get("design_file")
        if design_file:
            estimator.update_design_doc(design_file)

        logger.info(
            "成本预估完成: tables=%d, risks=%d",
            len(estimator.tables),
            len(estimator.risks),
        )

        return ToolResult(
            success=True,
            data={"report": report_md, "tables": estimator.tables, "risks": estimator.risks},
            metadata={
                "table_count": len(estimator.tables),
                "risk_count": len(estimator.risks),
                "risk_level": "🔴 高" if estimator.risks else "🟢 低",
            },
        )

    def validate(self, **kwargs: Any) -> list[str]:
        errors = []
        if not kwargs.get("sql_file"):
            errors.append("缺少必填参数: sql_file")
        return errors
