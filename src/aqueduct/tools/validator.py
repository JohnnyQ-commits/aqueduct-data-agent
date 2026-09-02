"""SQL 校验工具 — ValidatorTool。

提供 7 项 SQL 规范性检查，通过 @register_tool 注册到全局工具注册中心。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, TypedDict

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool
from ..utils.regex import PARTITION_FIELD_PATTERNS, RE_COMMENT, RE_JOIN, RE_ON

logger = logging.getLogger(__name__)


class Issue(TypedDict):
    """校验问题条目。"""

    level: str  # "ERROR" | "WARN" | "INFO"
    message: str
    line: int | None


# Validator 专用正则（不与其他工具共享）
_RE_SELECT_STAR = re.compile(r"\bselect\s+\*", re.IGNORECASE)
_RE_UNION = re.compile(r"\bunion\s+all\b|\bunion\b", re.IGNORECASE)
_RE_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)
_RE_DIVISION_VAR = re.compile(r"[a-zA-Z0-9_)]\s*/\s*([a-zA-Z_]\w*)")
_RE_WHEN_ZERO = re.compile(r"when\s+.+?>\s*0", re.IGNORECASE)
_RE_SUM_NVL = re.compile(r"\bSUM\s*\(\s*(nvl|coalesce|case)", re.IGNORECASE)
_RE_SUM_RAW = re.compile(r"\bSUM\s*\(\s*[a-zA-Z_]", re.IGNORECASE)

# P0-1 SQL 规范确定性校验（金样本校准，见知识库"金样本特征提取与linter规则校准"）
_RE_STRING_LITERAL = re.compile(r"'[^']*'")
_RE_CTE_LINE = re.compile(r"^\s*with\s+\w+\s+as\b", re.IGNORECASE)
_RE_FORBIDDEN_PARTITION = re.compile(r"\b(cur_date|data_date|riqi)\b", re.IGNORECASE)
_RE_CROSS_JOIN = re.compile(r"\bcross\s+join\b", re.IGNORECASE)
_RE_CREATE_TABLE_DB = re.compile(
    r"\bcreate\s+table\s+(?!if\s+not\s+exists\b)(?!external\b)(\w+)\.", re.IGNORECASE
)
_RE_DROP_TABLE_DB = re.compile(r"\bdrop\s+table\s+if\s+exists\s+(\w+)\.", re.IGNORECASE)

# 除法分母白名单：这些函数做分母时语义上已非零/有保护
# （nullif/nvl/coalesce/if/case 显式保护；count 聚合分组内 >=1，金样本 CR-001 周日均模式）
_DIV_DENOM_SAFE = {"nullif", "nvl", "coalesce", "if", "case", "count"}
_RE_LINE_COMMENT = re.compile(r"--.*$")


def _strip_literals_and_comments(line: str) -> str:
    """剥离字符串字面量与行中注释，供除法/分区违禁词匹配。

    顺序：先剥字符串（'...' → ''）再剥 -- 注释——
    行中注释里的斜杠短语（如 label_time/label_name）与违禁词不进入匹配，
    字符串里的 --（如 'a--b'）已先被替换，不受注释剥离误伤。
    """
    return _RE_LINE_COMMENT.sub("", _RE_STRING_LITERAL.sub("''", line))


class Validator:
    """SQL 校验器（核心逻辑，与原脚本保持一致）。

    检查项:
      1. SELECT * 禁止
      2. 分区过滤检查 (inc_day 等分区字段)
      3. 关键字大写 (团队规范要求全小写, ERROR)
      4. 除法未判空判零 (ERROR)
      5. JOIN 未指定关联条件
      6. SUM 聚合未使用 NVL 处理空值 (INFO, 金样本校准: 裸 sum 为合法写法)
      7. 分号结尾 (严格模式)
      8. CTE 禁止 (sql_standards §6.3, ERROR)
      9. 分区字段违禁词 cur_date/data_date/riqi (§1.1, ERROR)
      10. CROSS JOIN 禁止造维度骨架 (§10.6, ERROR)
      11. 临时表库名必须 tmp_ 前缀 (§1.3, ERROR, 金样本校准: tmp_ + 业务库)
    """

    def __init__(
        self,
        filepath: str | Path,
        strict: bool = False,
        content: str | None = None,
    ) -> None:
        self.filepath = Path(filepath)
        self.strict = strict
        # 内容模式：直接校验内存中的 SQL（review 修复循环复检场景，不落盘）
        self._content = content
        self.results: list[dict[str, Any]] = []
        self.lines: list[str] = []
        self.content = ""

    def _log(self, level: str, message: str, line_num: int | None = None) -> None:
        """记录一个校验问题。"""
        self.results.append(
            {
                "level": level,
                "message": message,
                "line": line_num,
            }
        )

    def check_select_star(self) -> None:
        """检查 1: 禁止 SELECT * (UNION ALL 合并场景除外)。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if _RE_SELECT_STAR.search(line):
                context_start = max(0, i - 3)
                context_end = min(len(self.lines), i + 2)
                context = " ".join(self.lines[context_start:context_end])
                if _RE_UNION.search(context):
                    continue
                self._log("ERROR", "使用了 SELECT *，必须显式列出字段", i)

    def check_partition_filter(self) -> None:
        """检查 2: 分区过滤。"""
        has_where = False
        has_partition = False
        for _i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if _RE_WHERE.search(line):
                has_where = True
            for pat in PARTITION_FIELD_PATTERNS:
                if pat.search(line):
                    has_partition = True
                    break
        if has_where and not has_partition:
            self._log("WARN", "WHERE 条件中未找到分区字段过滤 (inc_day/day/data_day)")

    def check_keyword_case(self) -> None:
        """检查 3: 关键字应全小写（§2.1 硬规范，ERROR）。"""
        line_start_keywords = [
            "select",
            "from",
            "where",
            "group by",
            "having",
            "order by",
            "left join",
            "right join",
            "inner join",
            "full join",
            "join",
            "union all",
            "union",
            "insert",
            "insert overwrite",
            "create table",
            "drop table",
            "with",
        ]
        for i, line in enumerate(self.lines, 1):
            stripped = line.strip()
            if not stripped or RE_COMMENT.match(line):
                continue
            line_lower = stripped.lower()
            for kw in line_start_keywords:
                if line_lower.startswith(kw):
                    actual_kw = stripped[: len(kw)]
                    if actual_kw != actual_kw.lower():
                        self._log("ERROR", f"关键字应全小写：{actual_kw}", i)
                        break

    def check_division(self) -> None:
        """检查 4: 除法未判空判零（§7.2，ERROR）。

        金样本校准：分母为 nullif/nvl/coalesce/if/case/count 视为已保护；
        字符串与行中注释先剥离再匹配，防止 'yyyy/MM/dd' 格式串、
        注释里的斜杠短语（label_time/label_name）误报。
        """
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            clean = _strip_literals_and_comments(line)
            m = _RE_DIVISION_VAR.search(clean)
            if m and m.group(1).lower() not in _DIV_DENOM_SAFE:
                if i > 1 and _RE_WHEN_ZERO.search(self.lines[i - 2]):
                    continue
                self._log("ERROR", "除法未做判空判零保护，应写为 a / nullif(b, 0)（§7.2）", i)

    def check_join_without_on(self) -> None:
        """检查 5: JOIN 未指定关联条件。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if RE_JOIN.search(line):
                if RE_ON.search(line):
                    continue
                found_on = False
                for j in range(i, min(i + 10, len(self.lines))):
                    next_line = self.lines[j]
                    if RE_ON.search(next_line):
                        found_on = True
                        break
                    if RE_JOIN.search(next_line) and j > i:
                        break
                if not found_on:
                    self._log("WARN", "JOIN 语句缺少 ON 条件", i)

    def check_nvl(self) -> None:
        """检查 6: SUM 聚合未使用 NVL 处理空值（INFO，金样本校准：裸 sum 为合法写法）。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if _RE_SUM_RAW.search(line) and not _RE_SUM_NVL.search(line):
                self._log("INFO", "SUM 聚合未使用 NVL 处理空值", i)

    def check_strict(self) -> None:
        """检查 7: 文件末尾分号（严格模式）。"""
        if not self.strict:
            return
        last_content_line: str | None = None
        for line in reversed(self.lines):
            if line.strip() and not RE_COMMENT.match(line):
                last_content_line = line.strip()
                break
        if last_content_line and not last_content_line.endswith(";"):
            self._log("WARN", "文件末尾语句缺少分号")

    # ── P0-1 SQL 规范确定性校验（金样本校准规则）────────────────────────────

    def check_cte(self) -> None:
        """检查 8: 禁止 CTE/WITH 子句（§6.3，ERROR）。

        替代方案：子查询派生表内联，或 TMP 临时表（drop + create）。
        """
        for i, line in enumerate(self.lines, 1):
            if _RE_CTE_LINE.match(line):
                self._log(
                    "ERROR",
                    "禁止使用 CTE（WITH 子句），改用子查询派生表或 TMP 临时表（§6.3）",
                    i,
                )

    def check_forbidden_partition_cols(self) -> None:
        """检查 9: 分区字段违禁词（§1.1，ERROR）——统一使用 inc_day。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            clean = _strip_literals_and_comments(line)
            m = _RE_FORBIDDEN_PARTITION.search(clean)
            if m:
                self._log(
                    "ERROR",
                    f"禁止分区字段 {m.group(1)}，统一使用 inc_day（§1.1）",
                    i,
                )

    def check_cross_join(self) -> None:
        """检查 10: 禁止 CROSS JOIN 造维度骨架（§10.6，ERROR）。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if _RE_CROSS_JOIN.search(line):
                self._log(
                    "ERROR",
                    "禁止 CROSS JOIN 造维度骨架，聚合阶段用 CASE WHEN 列打平（§10.6）",
                    i,
                )

    def check_tmp_database(self) -> None:
        """检查 11: 临时表库名必须 tmp_ 前缀（§1.3，ERROR）。

        金样本校准：真实模式为 tmp_ + 业务库名（如 tmp_dw_demo.tmp_xxx），
        规范文档中的 tmp_demo 仅为示例环境写法。
        DDL 正式表（create table if not exists / create external table）不受限。
        """
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            for pattern in (_RE_CREATE_TABLE_DB, _RE_DROP_TABLE_DB):
                m = pattern.search(line)
                if m and not m.group(1).lower().startswith("tmp_"):
                    self._log(
                        "ERROR",
                        f"临时表库名 {m.group(1)} 必须 tmp_ 前缀（如 tmp_dw_demo.tmp_xxx，§1.3）",
                        i,
                    )

    def run(self) -> dict[str, Any]:
        """执行全部校验，返回结构化报告。

        内容模式（content 非空）直接校验内存 SQL，不读文件——
        供 review 修复循环对 state 中最新 SQL 现场复检。
        """
        if self._content is not None:
            self.content = self._content
        else:
            if not self.filepath.exists():
                return {"error": f"文件不存在: {self.filepath}"}
            self.content = self.filepath.read_text(encoding="utf-8")
        self.lines = self.content.split("\n")

        self.check_select_star()
        self.check_partition_filter()
        self.check_keyword_case()
        self.check_division()
        self.check_join_without_on()
        self.check_nvl()
        self.check_strict()
        self.check_cte()
        self.check_forbidden_partition_cols()
        self.check_cross_join()
        self.check_tmp_database()

        return {
            "filename": self.filepath.name if self._content is None else "<content>",
            "error_count": len([r for r in self.results if r["level"] == "ERROR"]),
            "warn_count": len([r for r in self.results if r["level"] == "WARN"]),
            "issues": self.results,
        }


# ============================================================
# BaseTool 包装器
# ============================================================


@register_tool
class ValidatorTool(BaseTool):
    """SQL 校验工具 — 注册到全局工具注册中心。

    支持参数:
        sql_file: SQL 文件路径（必填）
        strict: 是否启用严格模式（可选，默认 False）
    """

    name = "validator"
    description = (
        "SQL 规范校验 — 检查 SELECT *、分区过滤、关键字大小写、除法判零、JOIN ON、SUM NVL 等 6+1 项"
    )

    def execute(self, **kwargs: Any) -> ToolResult:
        sql_file = kwargs.get("sql_file")
        if not sql_file:
            return ToolResult(
                success=False,
                error="缺少必填参数 sql_file",
            )

        strict = kwargs.get("strict", False)
        logger.info("校验开始: file=%s", sql_file)

        validator = Validator(sql_file, strict=strict)
        report = validator.run()

        if "error" in report:
            logger.warning("校验异常: %s", report["error"])
            return ToolResult(success=False, error=report["error"])

        logger.info(
            "校验完成: errors=%d, warnings=%d",
            report["error_count"],
            report["warn_count"],
        )

        return ToolResult(
            success=report["error_count"] == 0,
            data=report,
            metadata={
                "error_count": report["error_count"],
                "warn_count": report["warn_count"],
                "issue_count": len(report["issues"]),
            },
        )

    def validate(self, **kwargs: Any) -> list[str]:
        errors = []
        if not kwargs.get("sql_file"):
            errors.append("缺少必填参数: sql_file")
        return errors
