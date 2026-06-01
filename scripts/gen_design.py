# -*- coding: utf-8 -*-
"""
设计文档自动生成工具

用法:
  python gen_design.py <sql_file> [requirement_name]

说明:
  读取核心 ETL SQL 文件，自动填充 Design.txt 的 80% 内容：
  - 目标表结构（从 CREATE TABLE 或 INSERT 语句解析）
  - 数据来源（从 FROM/JOIN 语句解析）
  - 关联方式（从 ON 条件解析）
  - 上游依赖（从 SQL 中所有 库.表 解析）
  - 文件清单（自动生成）

  剩余需手动确认的部分：需求概述、取数逻辑、数据质量保障细节
"""

import sys
import re
from pathlib import Path
from datetime import datetime


def extract_tables_from_sql(sql):
    """从 SQL 中提取所有 库.表 格式的表名"""
    # 库名通常有下划线，且不是简单别名（别名通常1-2个字母）
    # 匹配模式：至少包含一个下划线的库名 + 点 + 表名
    pattern = r'([a-zA-Z_][a-zA-Z0-9_]*_[a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*_(?:di|df|hi|ho|d|snap|tmp|_di|_df))\b'
    matches = re.findall(pattern, sql)
    # 也匹配更一般的库.表模式，但排除常见别名前缀
    tables = []
    seen = set()
    for db, tbl in matches:
        full = f'{db}.{tbl}'
        if full not in seen:
            tables.append(full)
            seen.add(full)
    return tables


def parse_target_table(sql):
    """解析目标表名（从 INSERT OVERWRITE 或 CREATE TABLE）"""
    # INSERT OVERWRITE TABLE db.table
    m = re.search(r'insert\s+overwrite\s+table\s+(\w+\.\w+)', sql, re.IGNORECASE)
    if m:
        return m.group(1)

    # INSERT INTO db.table
    m = re.search(r'insert\s+into\s+(\w+\.\w+)', sql, re.IGNORECASE)
    if m:
        return m.group(1)

    # CREATE TABLE db.table
    m = re.search(r'create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+\.\w+)', sql, re.IGNORECASE)
    if m:
        return m.group(1)

    return None


def parse_source_tables(sql):
    """解析数据源表及其分区条件"""
    sources = []
    tables = extract_tables_from_sql(sql)

    target = parse_target_table(sql)
    # 排除目标表
    if target:
        tables = [t for t in tables if t != target]

    for table in tables:
        partition_info = '-'

        # 快照表识别
        snapshot_keywords = ['snap', 'snapshot', '_dim_', 'dimension', '_dim']
        is_snapshot = any(kw in table.lower() for kw in snapshot_keywords)

        if is_snapshot:
            partition_info = '快照表(无分区)'
            sources.append({
                'table': table,
                'partition': partition_info,
            })
            continue

        # 检查是否有 inc_day 过滤
        patterns = [
            rf'from\s+{re.escape(table)}\b.*?where.*?inc_day\s*=\s*[\'"]([^\']*)[\'"]',
            rf'{re.escape(table)}.*?where.*?inc_day\s*=\s*[\'"]([^\']*)[\'"]',
        ]

        has_partition = False
        for pat in patterns:
            m = re.search(pat, sql, re.IGNORECASE | re.DOTALL)
            if m:
                has_partition = True
                if m.group(1):
                    partition_info = m.group(1)
                else:
                    partition_info = 'inc_day 有过滤'
                break

        # 兜底：检查 inc_day 是否出现在该表附近
        if not has_partition:
            table_pos = sql.lower().find(table.lower())
            if table_pos >= 0:
                context = sql[table_pos:table_pos + 500].lower()
                if 'inc_day' in context and 'where' in context:
                    partition_info = 'inc_day 有过滤'

        sources.append({
            'table': table,
            'partition': partition_info,
        })

    return sources


def parse_join_logic(sql):
    """解析关联逻辑（从 ON 条件提取）"""
    joins = []
    lines = sql.split('\n')

    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if not re.search(r'\b(left\s+join|inner\s+join|right\s+join|full\s+join|join)\b', stripped):
            continue

        if '(' in stripped and stripped.rstrip().endswith('('):
            # 子查询 JOIN: left join ( ... ) alias on ...
            # 向后找 ) alias 和下一行的 on
            depth = 0
            for j in range(i, len(lines)):
                for ch in lines[j]:
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                        if depth == 0:
                            # 这一行包含 ) alias
                            end_line = lines[j].strip()
                            alias_match = re.match(r'\)\s+(\w+)\s*$', end_line)
                            alias = alias_match.group(1) if alias_match else '?'

                            # 下一行找 on 条件
                            if j + 1 < len(lines):
                                on_line = lines[j + 1].strip()
                                if on_line.lower().startswith('on '):
                                    condition = on_line[3:].strip()
                                    joins.append({
                                        'table': '(subquery)',
                                        'alias': alias,
                                        'condition': condition,
                                    })
                            break
                if depth <= 0 and joins and joins[-1]['alias'] != '?':
                    break
        else:
            # 普通 JOIN: join table alias on ...
            match = re.match(
                r'(?:left\s+join|inner\s+join|right\s+join|full\s+join|join)\s+(?:table\s+)?(\w+\.\w+|\w+)\s+(\w+)\s*$',
                stripped
            )
            if match:
                table = match.group(1)
                alias = match.group(2)
                # 下一行找 on
                if i + 1 < len(lines):
                    on_line = lines[i + 1].strip().lower()
                    if on_line.startswith('on '):
                        condition = lines[i + 1].strip()[3:].strip()
                        joins.append({
                            'table': table,
                            'alias': alias,
                            'condition': condition,
                        })

    return joins


def parse_field_list_from_sql(sql):
    """从 INSERT 的 select 部分提取字段列表（去重）"""
    fields = []
    seen = set()

    # 找所有 as xxx 的别名
    alias_pattern = r'as\s+([a-zA-Z_]\w*)\s*(?:,|$|\n)'

    for m in re.finditer(alias_pattern, sql, re.IGNORECASE):
        field_name = m.group(1)
        if field_name.lower() not in ('select', 'from', 'where', 'group', 'order', 'having', 'end', 'case', 'when', 'then', 'else', 'as'):
            if field_name not in seen:
                fields.append(field_name)
                seen.add(field_name)

    return fields


def generate_design(sql_file, requirement_name=None, output_path=None):
    """生成设计文档"""
    path = Path(sql_file)
    if not path.exists():
        print(f"文件不存在: {sql_file}")
        return 1

    with open(path, 'r', encoding='utf-8') as f:
        sql = f.read()

    if not requirement_name:
        requirement_name = path.stem

    if not output_path:
        output_path = path.parent / 'Design_auto.md'

    target_table = parse_target_table(sql)
    source_tables = parse_source_tables(sql)
    join_logic = parse_join_logic(sql)
    all_tables = extract_tables_from_sql(sql)

    # 构建文档（Markdown 格式）
    lines = []
    lines.append(f'# {requirement_name} - 设计文档')
    lines.append('')
    lines.append(f'> 自动生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 数据来源: {path.name}')
    lines.append('')
    lines.append('<!-- 以下部分需手动确认 -->')
    lines.append('')

    lines.append('## 一、需求概述')
    lines.append('')
    lines.append('- **需求目标**: [请补充]')
    lines.append('- **业务背景**: [请补充]')
    lines.append('')

    lines.append('## 二、取数逻辑')
    lines.append('')
    lines.append('[请根据 SQL 补充具体取数逻辑]')
    lines.append('')

    lines.append('## 三、映射关系')
    lines.append('')
    lines.append('[请补充各维度映射关系]')
    lines.append('')

    lines.append('<!-- 以下部分自动生成 -->')
    lines.append('')

    lines.append('## 四、目标表结构')
    lines.append('')
    if target_table:
        lines.append(f'- **表名**: `{target_table}`')
    else:
        lines.append('- **表名**: [未检测到目标表，请手动指定]')
    lines.append('- **分区**: `inc_day` string (格式 `YYYYMMDD`)')
    lines.append('')
    lines.append('| 字段名 | 类型 | 注释 |')
    lines.append('|--------|------|------|')

    fields = parse_field_list_from_sql(sql)
    if fields:
        for f in fields:
            lines.append(f'| {f} | string | [请补充注释] |')
    else:
        lines.append('| [请从 SQL 中提取或手动补充] | | |')
    lines.append('')

    lines.append('## 五、数据来源与关联关系')
    lines.append('')
    for i, src in enumerate(source_tables, 1):
        lines.append(f'{i}. **{src["table"]}**')
        lines.append(f'   - [请补充说明]')
        lines.append(f'   - 分区: `{src["partition"]}`')
        lines.append('')

    if join_logic:
        lines.append('**关联方式**:')
        lines.append('')
        lines.append('| 关联表 | 别名 | 关联条件 |')
        lines.append('|--------|------|----------|')
        for j in join_logic:
            lines.append(f'| {j["table"]} | {j["alias"]} | {j["condition"]} |')
    else:
        lines.append('**关联方式**: [请根据 SQL 补充]')
    lines.append('')

    lines.append('## 六、调度配置')
    lines.append('')
    lines.append('- **调度频率**: 每天一次 (T+1)')
    lines.append('- **调度时间**: 上游表产出后执行')
    lines.append('- **分区变量**: `inc_day = $[time(yyyyMMdd,-1d)]`')
    lines.append('- **失败策略**: 告警通知 + 重试')
    lines.append('')

    lines.append('## 七、数据质量保障')
    lines.append('')
    lines.append('详见 `数据质量测试.sql`')
    lines.append('')

    lines.append('## 八、上下游依赖')
    lines.append('')
    lines.append('**上游**:')

    upstream = [t for t in all_tables if t != target_table]
    for t in upstream:
        partition_note = ''
        for src in source_tables:
            if src['table'] == t:
                partition_note = f' (`{src["partition"]}`)'
                break
        lines.append(f'- {t}{partition_note}')

    lines.append('')
    lines.append('**下游**:')
    lines.append('- [请补充下游应用/报表]')
    lines.append('')

    lines.append('## 九、文件清单')
    lines.append('')
    lines.append('| 文件 | 用途 |')
    lines.append('|------|------|')
    lines.append('| 表结构.sql | 目标表DDL |')
    lines.append(f'| {requirement_name}.sql | 核心ETL逻辑 |')
    lines.append('| 数据质量测试.sql | DQC测试用例 |')
    lines.append('| Design.md | 本设计文档 |')
    lines.append('')

    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"设计文档已生成: {output_path}")
    print(f"  目标表: {target_table or '未检测到'}")
    print(f"  数据源: {len(source_tables)} 张表")
    print(f"  关联逻辑: {len(join_logic)} 条")
    print(f"  需手动确认部分: 需求概述、取数逻辑、映射关系")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    sql_file = sys.argv[1]
    requirement_name = sys.argv[2] if len(sys.argv) > 2 else None
    output_path = sys.argv[3] if len(sys.argv) > 3 else None
    return generate_design(sql_file, requirement_name, output_path)


if __name__ == '__main__':
    sys.exit(main())
