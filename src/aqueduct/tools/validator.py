"""SQL 校验工具 — ValidatorTool。

提供 7 项 SQL 规范性检查，通过 @register_tool 注册到全局工具注册中心。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypedDict

from ..tools.base import BaseTool, ToolResult
from ..tools.registry import register_tool
from ..utils.regex import RE_COMMENT


class Issue(TypedDict):
    """校验问题条目。"""

    level: str  # "ERROR" | "WARN" | "INFO"
    message: str
    line: int | None

# Validator 专用正则（不与其他工具共享）
_RE_SELECT_STAR = re.compile(r"\bselect\s+\*", re.IGNORECASE)
_RE_UNION = re.compile(r"\bunion\s+all\b|\bunion\b", re.IGNORECASE)
_RE_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)
_RE_DIVISION = re.compile(r"[a-zA-Z0-9_)]\s*/\s*[a-zA-Z0-9_(]")
_RE_DIV_PROTECT = re.compile(
    r"(nvl|if|coalesce|case).*?/.*?(nvl|if|coalesce|case)",
    re.IGNORECASE,
)
_RE_WHEN_ZERO = re.compile(r"when\s+.+?>\s*0", re.IGNORECASE)
_RE_JOIN = re.compile(
    r"\b(left\s+join|right\s+join|inner\s+join|full\s+join|join)\b",
    re.IGNORECASE,
)
_RE_ON = re.compile(r"\bon\b", re.IGNORECASE)
_RE_SUM_NVL = re.compile(r"\bSUM\s*\(\s*(nvl|coalesce|case)", re.IGNORECASE)
_RE_SUM_RAW = re.compile(r"\bSUM\s*\(\s*[a-zA-Z_]", re.IGNORECASE)

# 分区字段正则列表
_PARTITION_PATTERNS = [
    re.compile(r"\binc_day\s*(=|in|between)", re.IGNORECASE),
    re.compile(r"\bday\s*(=|in|between)", re.IGNORECASE),
    re.compile(r"\bdata_day\s*(=|in|between)", re.IGNORECASE),
]


class Validator:
    """SQL 校验器（核心逻辑，与原脚本保持一致）。

    检查项:
      1. SELECT * 禁止
      2. 分区过滤检查 (inc_day 等分区字段)
      3. 关键字大写警告 (团队规范要求全小写)
      4. 除法未判空判零
      5. JOIN 未指定关联条件
      6. SUM 聚合未使用 NVL 处理空值
      7. 分号结尾 (严格模式)
    """

    def __init__(self, filepath: str | Path, strict: bool = False) -> None:
        self.filepath = Path(filepath)
        self.strict = strict
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
            for pat in _PARTITION_PATTERNS:
                if pat.search(line):
                    has_partition = True
                    break
        if has_where and not has_partition:
            self._log("WARN", "WHERE 条件中未找到分区字段过滤 (inc_day/day/data_day)")

    def check_keyword_case(self) -> None:
        """检查 3: 关键字应全小写。"""
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
                        self._log("WARN", f"关键字应全小写：{actual_kw}", i)
                        break

    def check_division(self) -> None:
        """检查 4: 除法未判空判零。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if _RE_DIVISION.search(line):
                if _RE_DIV_PROTECT.search(line):
                    continue
                if i > 1 and _RE_WHEN_ZERO.search(self.lines[i - 2]):
                    continue
                self._log("WARN", "除法未做判空判零处理", i)

    def check_join_without_on(self) -> None:
        """检查 5: JOIN 未指定关联条件。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if _RE_JOIN.search(line):
                if _RE_ON.search(line):
                    continue
                found_on = False
                for j in range(i, min(i + 10, len(self.lines))):
                    next_line = self.lines[j]
                    if _RE_ON.search(next_line):
                        found_on = True
                        break
                    if _RE_JOIN.search(next_line) and j > i:
                        break
                if not found_on:
                    self._log("WARN", "JOIN 语句缺少 ON 条件", i)

    def check_nvl(self) -> None:
        """检查 6: SUM 聚合未使用 NVL 处理空值。"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if _RE_SUM_RAW.search(line) and not _RE_SUM_NVL.search(line):
                self._log("WARN", "SUM 聚合未使用 NVL 处理空值", i)

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

    def run(self) -> dict[str, Any]:
        """执行全部校验，返回结构化报告。"""
        if not self.filepath.exists():
            return {"error": f"文件不存在: {self.filepath}"}

        with open(self.filepath, encoding="utf-8") as f:
            self.content = f.read()
        self.lines = self.content.split("\n")

        self.check_select_star()
        self.check_partition_filter()
        self.check_keyword_case()
        self.check_division()
        self.check_join_without_on()
        self.check_nvl()
        self.check_strict()

        return {
            "filename": self.filepath.name,
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
        validator = Validator(sql_file, strict=strict)
        report = validator.run()

        if "error" in report:
            return ToolResult(success=False, error=report["error"])

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
