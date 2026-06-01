# -*- coding: utf-8 -*-
"""
SQL 自动校验工具

用法:
  python validate_sql.py <sql_file>
  python validate_sql.py <sql_file> --strict   (更严格的检查)

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

def error(msg, line_num=None):
    prefix = f"  Line {line_num}: " if line_num else "  "
    print(f"  {Colors.RED}[ERROR]{Colors.RESET}{prefix}{msg}")

def warn(msg, line_num=None):
    prefix = f"  Line {line_num}: " if line_num else "  "
    print(f"  {Colors.YELLOW}[WARN]{Colors.RESET}{prefix}{msg}")

def ok(msg):
    print(f"  {Colors.GREEN}[OK]{Colors.RESET} {msg}")


def check_select_star(sql, lines):
    """检查 1: 禁止 SELECT * (UNION ALL 合并场景除外)"""
    found = False
    for i, line in enumerate(lines, 1):
        if RE_COMMENT.match(line):
            continue
        if RE_SELECT_STAR.search(line):
            # 检查上下文是否有 union
            context_start = max(0, i - 3)
            context_end = min(len(lines), i + 2)
            context = ' '.join(lines[context_start:context_end])
            if RE_UNION.search(context):
                continue
            error("使用了 SELECT *，必须显式列出字段", i)
            found = True
    return found


def check_partition_filter(sql, lines):
    """检查 2: 分区过滤"""
    has_where = False
    has_partition = False

    for i, line in enumerate(lines, 1):
        if RE_COMMENT.match(line):
            continue
        if RE_WHERE.search(line):
            has_where = True
        
        for pat in PARTITION_PATTERNS:
            if pat.search(line):
                has_partition = True
                break

    if has_where and not has_partition:
        warn("WHERE 条件中未找到分区字段过滤 (inc_day/day/data_day)")
        return True
    return False


def check_keyword_case(sql, lines):
    """检查 3: 关键字大写警告"""
    found = False
    line_start_keywords = [
        'select', 'from', 'where', 'group by', 'having', 'order by',
        'left join', 'right join', 'inner join', 'full join', 'join',
        'union all', 'union', 'insert', 'insert overwrite',
        'create table', 'drop table', 'with',
    ]

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or RE_COMMENT.match(line):
            continue

        line_lower = stripped.lower()
        for kw in line_start_keywords:
            if line_lower.startswith(kw):
                # 检查实际内容是否包含大写
                actual_kw = stripped[:len(kw)]
                if actual_kw != actual_kw.lower():
                    warn(f"关键字应全小写：{actual_kw}", i)
                    found = True
                    break
    return found


def check_division(sql, lines):
    """检查 4: 除法未判空判零"""
    found = False
    for i, line in enumerate(lines, 1):
        if RE_COMMENT.match(line):
            continue
        if RE_DIVISION.search(line):
            if RE_DIV_PROTECT.search(line):
                continue
            if i > 1 and RE_WHEN_ZERO.search(lines[i-2]):
                continue
            warn("除法未做判空判零处理", i)
            found = True
    return found


def check_join_without_on(sql, lines):
    """检查 5: JOIN 未指定关联条件"""
    found = False
    for i, line in enumerate(lines, 1):
        if RE_COMMENT.match(line):
            continue
        if RE_JOIN.search(line):
            if RE_ON.search(line):
                continue
            
            # 向下搜索 ON 条件，直到遇到下一个 JOIN 或语句结束
            found_on = False
            for j in range(i, min(i + 10, len(lines))):
                next_line = lines[j]
                if RE_ON.search(next_line):
                    found_on = True
                    break
                if RE_JOIN.search(next_line) and j > i: # 遇到下一个 JOIN 还没找到 ON
                    break
            
            if not found_on:
                warn(f"JOIN 语句缺少 ON 条件", i)
                found = True
    return found


def check_nvl(sql, lines):
    """检查 6: 数值字段未做 NVL 处理"""
    found = False
    for i, line in enumerate(lines, 1):
        if RE_COMMENT.match(line):
            continue
        if RE_SUM_RAW.search(line):
            if not RE_SUM_NVL.search(line):
                warn("SUM 聚合未使用 NVL 处理空值", i)
                found = True
    return found


def check_strict(sql, lines, strict=False):
    """严格模式下的额外检查"""
    if not strict:
        return

    # 检查文件末尾是否有分号
    last_content_line = None
    for line in reversed(lines):
        if line.strip() and not RE_COMMENT.match(line):
            last_content_line = line.strip()
            break
    
    if last_content_line and not last_content_line.endswith(';'):
        warn("文件末尾语句缺少分号")


def validate_file(filepath, strict=False):
    """主验证函数"""
    path = Path(filepath)
    if not path.exists():
        print(f"{Colors.RED}文件不存在: {filepath}{Colors.RESET}")
        return 1

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')

    print(f"\n{Colors.BOLD}=== SQL 校验: {path.name} ==={Colors.RESET}\n")

    checks = [
        ("SELECT * 检查", lambda: check_select_star(content, lines)),
        ("分区过滤检查", lambda: check_partition_filter(content, lines)),
        ("关键字大小写检查", lambda: check_keyword_case(content, lines)),
        ("除法判空检查", lambda: check_division(content, lines)),
        ("JOIN 条件检查", lambda: check_join_without_on(content, lines)),
        ("NVL 聚合检查", lambda: check_nvl(content, lines)),
    ]

    error_count = 0
    warn_count = 0

    for name, check_fn in checks:
        result = check_fn()
        if result:
            # 只有 SELECT * 是 ERROR，其他是 WARN
            if "SELECT *" in name:
                error_count += 1
            else:
                warn_count += 1

    if strict:
        check_strict(content, lines, strict=True)

    print(f"\n{Colors.BOLD}=== 校验完成 ==={Colors.RESET}")
    print(f"  文件: {path.name}")
    print(f"  行数: {len(lines)}")
    print(f"  问题: {error_count} 个 ERROR, {warn_count} 个 WARN")

    if error_count == 0 and warn_count == 0:
        ok("所有检查通过")
        return 0
    elif error_count > 0:
        print(f"\n{Colors.RED}{Colors.BOLD}存在 ERROR，建议修复后使用{Colors.RESET}")
        return 1
    else:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}存在 WARN，请确认后使用{Colors.RESET}")
        return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    filepath = sys.argv[1]
    strict = '--strict' in sys.argv
    return validate_file(filepath, strict)


if __name__ == '__main__':
    sys.exit(main())
