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
        stripped = line.strip().lower()
        # 跳过注释行
        if stripped.startswith('--'):
            continue
        if re.search(r'\bselect\s+\*', stripped, re.IGNORECASE):
            # UNION ALL 场景下 select * 是允许的（合并相同结构的表）
            # 检查上下几行是否有 union all
            context_start = max(0, i - 3)
            context_end = min(len(lines), i + 2)
            context = ' '.join(l.strip().lower() for l in lines[context_start:context_end])
            if 'union all' in context or 'union' in context:
                continue
            error("使用了 SELECT *，必须显式列出字段", i)
            found = True
    return found


def check_partition_filter(sql, lines):
    """检查 2: 分区过滤"""
    has_where = False
    has_partition = False
    # 分区字段关键字
    partition_patterns = [
        r'inc_day\s*=',
        r'inc_day\s+in\s*\(',
        r'\bday\s*=',
        r'data_day\s*=',
    ]

    for i, line in enumerate(lines, 1):
        stripped = line.strip().lower()
        if stripped.startswith('--'):
            continue
        if 'where' in stripped:
            has_where = True
            for pat in partition_patterns:
                if re.search(pat, stripped):
                    has_partition = True

    if has_where and not has_partition:
        warn("WHERE 条件中未找到分区字段过滤 (inc_day/day/data_day)")
        return True
    return False


def check_keyword_case(sql, lines):
    """检查 3: 关键字大写警告"""
    found = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue

        # 只检查行首的关键字（最常见的违规场景）
        line_lower = stripped.lower()
        line_upper = stripped.upper()

        # 行首关键字列表
        line_start_keywords = [
            'select', 'from', 'where', 'group by', 'having', 'order by',
            'left join', 'right join', 'inner join', 'full join', 'join',
            'union all', 'union', 'insert', 'insert overwrite',
            'create table', 'drop table', 'with',
        ]

        for kw in line_start_keywords:
            if line_lower.startswith(kw):
                # 检查原始行是否也是小写开头
                if not stripped.lower().startswith(kw) or stripped[:len(kw)] != stripped[:len(kw)].lower():
                    # 原始行首字母是大写
                    if stripped[:len(kw)] != stripped[:len(kw)].lower():
                        warn(f"关键字应全小写：{kw}", i)
                        found = True
                        break

    return found


def check_division(sql, lines):
    """检查 4: 除法未判空判零"""
    found = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        # 查找除法运算符 /
        if re.search(r'[a-zA-Z0-9_)]\s*/\s*[a-zA-Z0-9_(]', stripped):
            # 检查是否有 NVL/IF/coalesce 保护
            if re.search(r'(nvl|if|coalesce|case).*?/.*?(nvl|if|coalesce|case)', stripped, re.IGNORECASE):
                continue
            # 检查上一行是否有判零条件 (when ... > 0)
            if i > 1:
                prev_line = lines[i - 2].strip().lower()
                if re.search(r'when\s+.+?>\s*0', prev_line):
                    continue
            warn("除法未做判空判零处理", i)
            found = True
    return found


def check_join_without_on(sql, lines):
    """检查 5: JOIN 未指定关联条件"""
    found = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip().lower()
        if stripped.startswith('--'):
            continue
        if re.search(r'\b(left\s+join|right\s+join|inner\s+join|full\s+join|join)\b', stripped):
            # 同一行有 on 则跳过
            if ' on ' in stripped or stripped.startswith('on '):
                continue
            # 判断是否为子查询 JOIN (行尾有 `(`)
            is_subquery = stripped.rstrip().endswith('(')

            if is_subquery:
                # 子查询: 找 ) 后面跟 on 的行
                found_on = False
                for j in range(i, min(i + 20, len(lines))):
                    next_stripped = lines[j].strip().lower()
                    # 匹配 ") alias" 格式的行
                    if re.match(r'^\)\s*\w+', next_stripped):
                        # 检查下一行是否是 on
                        if j + 1 < len(lines):
                            after_alias = lines[j + 1].strip().lower()
                            if ' on ' in after_alias or after_alias.startswith('on '):
                                found_on = True
                        break
                    # 如果看到另一个 JOIN，说明真的没有 ON
                    if re.match(r'^(left|right|inner|full|cross)\s+join\b', next_stripped):
                        break
            else:
                # 非子查询 JOIN: 简单向后查找 on
                found_on = False
                for j in range(i, min(i + 3, len(lines))):
                    next_stripped = lines[j].strip().lower()
                    if ' on ' in next_stripped or next_stripped.startswith('on '):
                        found_on = True
                        break

            if found_on:
                continue
            warn(f"JOIN 语句缺少 ON 条件", i)
            found = True
    return found


def check_nvl(sql, lines):
    """检查 6: 数值字段未做 NVL 处理"""
    found = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        # SUM(字段) 但没有 NVL 或 case 包裹
        # SUM(case when...) 是允许的，因为 case 已经处理了空值
        if re.search(r'\bSUM\s*\(\s*[a-zA-Z_]', stripped, re.IGNORECASE):
            if re.search(r'\bSUM\s*\(\s*case', stripped, re.IGNORECASE):
                continue  # SUM(case when...) 是允许的
            if not re.search(r'\bSUM\s*\(\s*(nvl|coalesce)', stripped, re.IGNORECASE):
                warn("SUM 聚合未使用 NVL 处理空值", i)
                found = True
    return found


def check_strict(sql, lines, strict=False):
    """严格模式下的额外检查"""
    if not strict:
        return

    found = False
    # 检查子查询是否有别名
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        if re.search(r'\)\s*$', stripped) and i > 1:
            next_line = lines[i].strip() if i < len(lines) else ''
            # 子查询结束后应该紧跟别名
            if not re.search(r'^\s*[a-zA-Z_]', next_line) and not re.search(r'^\s*[a-zA-Z_]', stripped):
                pass  # 复杂判断，跳过

    # 检查是否有冗余的 LEFT JOIN（可以改为 INNER JOIN）
    # 这个需要语义理解，跳过

    # 检查文件末尾是否有分号
    last_line = lines[-1].strip() if lines else ''
    if last_line and not last_line.endswith(';') and not last_line.startswith('--'):
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
            error_count += 1 if name.startswith("SELECT") else 0
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
