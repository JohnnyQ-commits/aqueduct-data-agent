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

import re
import sys
from datetime import datetime
from pathlib import Path

from scripts.utils import (
    RE_ALIAS_AS,
    RE_INC_DAY_FILTER,
    RE_JOIN_KEYWORD,
    extract_tables,
    parse_target_table,
)

# 排除的关键字（在解析字段别名时使用）
EXCLUDED_KEYWORDS = {'select', 'from', 'where', 'group', 'order', 'having', 'end', 'case', 'when', 'then', 'else', 'as', 'join', 'on', 'limit'}


def extract_tables_from_sql(sql):
    """从 SQL 中提取所有 库.表 格式的表名（别名，供测试导入）"""
    return extract_tables(sql)


def parse_source_tables(sql):
    """解析数据源表及其分区条件"""
    sources = []
    tables = extract_tables_from_sql(sql)
    target = parse_target_table(sql)

    # 排除目标表
    if target:
        tables = [t for t in tables if t != target]

    # 优化快照表关键字匹配
    snapshot_keywords = ['snap', 'snapshot', 'dim_', 'dimension']

    for table in tables:
        partition_info = '-'
        is_snapshot = any(kw in table.lower() for kw in snapshot_keywords)

        if is_snapshot:
            partition_info = '快照表(无分区)'
        else:
            # 在表名附近查找 inc_day 过滤
            table_pos = sql.lower().find(table.lower())
            if table_pos >= 0:
                # 查找表名后 1000 字符内的 inc_day
                context = sql[table_pos:table_pos + 1000]
                m = RE_INC_DAY_FILTER.search(context)
                if m:
                    partition_info = m.group(1) or 'inc_day 有过滤'
                elif 'inc_day' in context.lower():
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
        if not RE_JOIN_KEYWORD.search(stripped):
            continue

        # 尝试解析普通 JOIN: join table alias on ...
        # 处理可能的换行情况
        current_context = ' '.join([line.strip() for line in lines[i:i+3]])
        match = re.search(
            r'\b(?:left\s+join|inner\s+join|right\s+join|full\s+join|join)\s+(\w+\.\w+|\w+)\s+(\w+)\s+on\s+(.+?)(?:\s+(?:left|inner|right|full|join|where|group|order|limit)|$)',
            current_context,
            re.IGNORECASE
        )
        if match:
            joins.append({
                'table': match.group(1),
                'alias': match.group(2),
                'condition': match.group(3).strip(),
            })
        elif '(' in stripped:
            # 简单标记子查询 JOIN
            joins.append({
                'table': '(subquery)',
                'alias': 'alias',
                'condition': 'see SQL',
            })

    return joins


def parse_field_list_from_sql(sql):
    """从 INSERT 的 select 部分提取字段列表（去重）"""
    fields = []
    seen = set()

    for m in RE_ALIAS_AS.finditer(sql):
        field_name = m.group(1)
        if field_name.lower() not in EXCLUDED_KEYWORDS and field_name not in seen:
            fields.append(field_name)
            seen.add(field_name)
    return fields


def generate_design(sql_file, requirement_name=None, output_path=None):
    """生成设计文档"""
    path = Path(sql_file)
    if not path.exists():
        print(f"文件不存在: {sql_file}")
        return 1

    with open(path, encoding='utf-8') as f:
        sql = f.read()

    if not requirement_name:
        requirement_name = path.stem

    if not output_path:
        output_path = path.parent / 'Design_auto.md'

    target_table = parse_target_table(sql)
    source_tables = parse_source_tables(sql)
    join_logic = parse_join_logic(sql)
    all_tables = extract_tables_from_sql(sql)

    # 构建文档
    lines = [
        f'# {requirement_name} - 设计文档',
        '',
        f'> 自动生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        f'> 数据来源: {path.name}',
        '',
        '<!-- 以下部分需手动确认 -->',
        '',
        '## 一、需求概述',
        '',
        '- **需求目标**: [请补充]',
        '- **业务背景**: [请补充]',
        '',
        '## 二、取数逻辑',
        '',
        '[请根据 SQL 补充具体取数逻辑]',
        '',
        '## 三、映射关系',
        '',
        '[请补充各维度映射关系]',
        '',
        '<!-- 以下部分自动生成 -->',
        '',
        '## 四、目标表结构',
        '',
        f'- **表名**: `{target_table or "[未检测到目标表]"}`',
        '- **分区**: `inc_day` string (格式 `YYYYMMDD`)',
        '',
        '| 字段名 | 类型 | 注释 |',
        '|--------|------|------|'
    ]

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
        lines.append('   - [请补充说明]')
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

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"设计文档已生成: {output_path}")
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
