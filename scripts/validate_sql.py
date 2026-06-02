# -*- coding: utf-8 -*-
"""
SQL 自动校验工具

用法:
  python validate_sql.py <sql_file>
  python validate_sql.py <sql_file> --strict   (更严格的检查)
  python validate_sql.py <sql_file> --json     (输出 JSON 格式，供 AI 自动修复使用)

检查项:
  1. SELECT * 禁止
  2. 分区过滤检查 (inc_day 等分区字段)
  3. 关键字大写警告 (团队规范要求全小写)
  4. 除法未判空判零
  5. JOIN 未指定关联条件
  6. 表未定义别名
  7. 分号结尾 (文件末尾)
"""

import sys
import re
import json
from pathlib import Path

# 颜色输出
class Colors:
    RED = '\033[91m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

# 预编译正则表达式以优化性能
RE_SELECT_STAR = re.compile(r'\bselect\s+\*', re.IGNORECASE)
RE_UNION = re.compile(r'\bunion\s+all\b|\bunion\b', re.IGNORECASE)
RE_COMMENT = re.compile(r'^\s*--')
RE_WHERE = re.compile(r'\bwhere\b', re.IGNORECASE)
RE_DIVISION = re.compile(r'[a-zA-Z0-9_)]\s*/\s*[a-zA-Z0-9_(]')
RE_DIV_PROTECT = re.compile(r'(nvl|if|coalesce|case).*?/.*?(nvl|if|coalesce|case)', re.IGNORECASE)
RE_WHEN_ZERO = re.compile(r'when\s+.+?>\s*0', re.IGNORECASE)
RE_JOIN = re.compile(r'\b(left\s+join|right\s+join|inner\s+join|full\s+join|join)\b', re.IGNORECASE)
RE_ON = re.compile(r'\bon\b', re.IGNORECASE)
RE_SUBQUERY_ALIAS = re.compile(r'^\)\s*\w+')
RE_SUM_NVL = re.compile(r'\bSUM\s*\(\s*(nvl|coalesce|case)', re.IGNORECASE)
RE_SUM_RAW = re.compile(r'\bSUM\s*\(\s*[a-zA-Z_]', re.IGNORECASE)

# 分区字段正则列表
PARTITION_PATTERNS = [
    re.compile(r'\binc_day\s*(=|in|between)', re.IGNORECASE),
    re.compile(r'\bday\s*(=|in|between)', re.IGNORECASE),
    re.compile(r'\bdata_day\s*(=|in|between)', re.IGNORECASE),
]

class Validator:
    def __init__(self, filepath, strict=False):
        self.filepath = Path(filepath)
        self.strict = strict
        self.results = []
        self.lines = []
        self.content = ""

    def log(self, level, message, line_num=None):
        self.results.append({
            "level": level,
            "message": message,
            "line": line_num
        })

    def check_select_star(self):
        """检查 1: 禁止 SELECT * (UNION ALL 合并场景除外)"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if RE_SELECT_STAR.search(line):
                context_start = max(0, i - 3)
                context_end = min(len(self.lines), i + 2)
                context = ' '.join(self.lines[context_start:context_end])
                if RE_UNION.search(context):
                    continue
                self.log("ERROR", "使用了 SELECT *，必须显式列出字段", i)

    def check_partition_filter(self):
        """检查 2: 分区过滤"""
        has_where = False
        has_partition = False
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if RE_WHERE.search(line):
                has_where = True
            for pat in PARTITION_PATTERNS:
                if pat.search(line):
                    has_partition = True
                    break
        if has_where and not has_partition:
            self.log("WARN", "WHERE 条件中未找到分区字段过滤 (inc_day/day/data_day)")

    def check_keyword_case(self):
        """检查 3: 关键字大写警告"""
        line_start_keywords = [
            'select', 'from', 'where', 'group by', 'having', 'order by',
            'left join', 'right join', 'inner join', 'full join', 'join',
            'union all', 'union', 'insert', 'insert overwrite',
            'create table', 'drop table', 'with',
        ]
        for i, line in enumerate(self.lines, 1):
            stripped = line.strip()
            if not stripped or RE_COMMENT.match(line):
                continue
            line_lower = stripped.lower()
            for kw in line_start_keywords:
                if line_lower.startswith(kw):
                    actual_kw = stripped[:len(kw)]
                    if actual_kw != actual_kw.lower():
                        self.log("WARN", f"关键字应全小写：{actual_kw}", i)
                        break

    def check_division(self):
        """检查 4: 除法未判空判零"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if RE_DIVISION.search(line):
                if RE_DIV_PROTECT.search(line):
                    continue
                if i > 1 and RE_WHEN_ZERO.search(self.lines[i-2]):
                    continue
                self.log("WARN", "除法未做判空判零处理", i)

    def check_join_without_on(self):
        """检查 5: JOIN 未指定关联条件"""
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
                    self.log("WARN", "JOIN 语句缺少 ON 条件", i)

    def check_nvl(self):
        """检查 6: 数值字段未做 NVL 处理"""
        for i, line in enumerate(self.lines, 1):
            if RE_COMMENT.match(line):
                continue
            if RE_SUM_RAW.search(line):
                if not RE_SUM_NVL.search(line):
                    self.log("WARN", "SUM 聚合未使用 NVL 处理空值", i)

    def check_strict(self):
        if not self.strict:
            return
        last_content_line = None
        for line in reversed(self.lines):
            if line.strip() and not RE_COMMENT.match(line):
                last_content_line = line.strip()
                break
        if last_content_line and not last_content_line.endswith(';'):
            self.log("WARN", "文件末尾语句缺少分号")

    def run(self):
        if not self.filepath.exists():
            return {"error": f"文件不存在: {self.filepath}"}
        
        with open(self.filepath, 'r', encoding='utf-8') as f:
            self.content = f.read()
        self.lines = self.content.split('\n')

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
            "issues": self.results
        }

def print_text_report(report):
    print(f"\n{Colors.BOLD}=== SQL 校验: {report['filename']} ==={Colors.RESET}\n")
    for issue in report["issues"]:
        color = Colors.RED if issue["level"] == "ERROR" else Colors.YELLOW
        line_info = f"Line {issue['line']}: " if issue["line"] else ""
        print(f"  {color}[{issue['level']}]{Colors.RESET} {line_info}{issue['message']}")
    
    print(f"\n{Colors.BOLD}=== 校验完成 ==={Colors.RESET}")
    print(f"  问题: {report['error_count']} 个 ERROR, {report['warn_count']} 个 WARN")
    
    if report['error_count'] == 0 and report['warn_count'] == 0:
        print(f"  {Colors.GREEN}[OK]{Colors.RESET} 所有检查通过")
    elif report['error_count'] > 0:
        print(f"\n{Colors.RED}{Colors.BOLD}存在 ERROR，建议修复后使用{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}存在 WARN，请确认后使用{Colors.RESET}")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    filepath = sys.argv[1]
    strict = '--strict' in sys.argv
    as_json = '--json' in sys.argv

    validator = Validator(filepath, strict)
    report = validator.run()

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)

    return 1 if report.get("error_count", 0) > 0 else 0

if __name__ == '__main__':
    sys.exit(main())
