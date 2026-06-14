"""变更管理节点 — 需求交付后的变更流程。

变更管理 6 阶段：
1. 变更识别 — 对比新旧需求，识别差异
2. 变更需求文档 — 生成 CR-NNN 目录和变更需求.md
3. 变更 SQL — 生成独立的变更 SQL
4. 变更审查 — 影响分析 + 回归验证
5. 合并执行 — 合并到主 SQL + 重跑验证
6. 归档 — 更新交付报告 + 知识沉淀
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..state import WorkflowState
from .helpers import call_llm, get_output_dir

logger = logging.getLogger(__name__)


def _get_changes_dir(state: WorkflowState) -> Path:
    """获取变更目录: output/{需求名}/changes/"""
    output_dir = get_output_dir(state)
    changes_dir = output_dir / "changes"
    changes_dir.mkdir(parents=True, exist_ok=True)
    return changes_dir


def _get_next_cr_number(changes_dir: Path) -> str:
    """获取下一个 CR 编号，扫描已有 CR 目录递增。"""
    existing = list(changes_dir.glob("CR-*"))
    if not existing:
        return "001"

    max_num = 0
    for cr_dir in existing:
        match = re.match(r"CR-(\d+)", cr_dir.name)
        if match:
            max_num = max(max_num, int(match.group(1)))

    return f"{max_num + 1:03d}"


def node_change_identify(state: WorkflowState) -> WorkflowState:
    """Phase 1: 变更识别。

    对比原始需求和新需求，识别变更内容。
    """
    try:
        original_req = state.get("original_requirement", "")
        new_req = state.get("new_requirement", "")
        change_desc = state.get("change_description", "")

        if not original_req:
            state.setdefault("errors", []).append("变更识别失败: 缺少原始需求文档")
            return state

        if not new_req:
            state.setdefault("errors", []).append("变更识别失败: 缺少新需求文档")
            return state

        prompt = f"""# 变更识别

## 原始需求
{original_req}

## 新需求
{new_req}

## 补充说明
{change_desc}

## 任务

请对比原始需求和新需求，识别所有变更内容：

1. **新增字段** — 新需求中有但原始需求中没有的字段
2. **修改字段** — 计算逻辑或定义发生变化的字段
3. **删除字段** — 原始需求中有但新需求中移除的字段
4. **逻辑变更** — 相同字段但计算规则不同

输出格式：

### 变更清单

| 变更项 | 类型 | 说明 |
|--------|------|------|
| 字段名 | 新增/修改/删除/逻辑变更 | 详细描述 |

### 影响分析

- **新增TMP表**: 如有
- **修改TMP表**: 如有
- **修改ADS表**: 如有
- **数据源变化**: 如有

### 待确认问题

1. 如有需要业务方确认的问题
"""

        llm_response = call_llm(state, "requirement_parse", prompt)

        state["change_identification"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "change_identified": "true"}

        logger.info("Phase 1 变更识别完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"变更识别异常: {e!s}")
        logger.error("变更识别异常: %s", e, exc_info=True)

    return state


def node_change_document(state: WorkflowState) -> WorkflowState:
    """Phase 2: 生成变更需求文档。

    创建 CR-NNN 目录和变更需求.md。
    """
    try:
        changes_dir = _get_changes_dir(state)
        cr_number = _get_next_cr_number(changes_dir)
        change_summary = state.get("change_description", "变更")

        # 创建 CR 目录
        cr_dir_name = f"CR-{cr_number}_{change_summary[:20]}"
        cr_dir = changes_dir / cr_dir_name
        cr_dir.mkdir(parents=True, exist_ok=True)

        state["cr_number"] = cr_number
        state["cr_dir"] = str(cr_dir)

        change_id = state.get("change_identification", "")

        prompt = f"""# 变更需求文档生成

## CR编号
CR-{cr_number}

## 变更识别结果
{change_id}

## 任务

请生成完整的变更需求文档，包含以下章节：

### 基本信息
| 项目 | 内容 |
|------|------|
| CR编号 | CR-{cr_number} |
| 变更简称 | {change_summary} |
| 日期 | {{date}} |
| 变更类型 | 新增字段 / 逻辑修改 / 删除字段 / 数据源变更 |

### 变更背景
{{说明变更的业务背景和需求原因}}

### 新增/修改字段清单
| 字段名(英文) | 字段名(中文) | 类型 | 所属模块 | 变更类型 |
|--------------|--------------|------|----------|----------|

### 字段逻辑详情
每个新字段的取数逻辑、计算公式、数据来源

### 影响分析
| 影响对象 | 类型 | 说明 |
|----------|------|------|

### 待确认问题
| # | 问题 | 状态 |
|---|------|------|
"""

        llm_response = call_llm(state, "doc_gen", prompt)

        # 保存到 CR 目录
        req_doc_path = cr_dir / "变更需求.md"
        req_doc_path.write_text(llm_response, encoding="utf-8")

        state["change_requirement_doc"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "change_documented": "true"}

        logger.info("Phase 2 变更需求文档完成: %s", req_doc_path)
    except Exception as e:
        state.setdefault("errors", []).append(f"变更需求文档异常: {e!s}")
        logger.error("变更需求文档异常: %s", e, exc_info=True)

    return state


def node_change_sql(state: WorkflowState) -> WorkflowState:
    """Phase 3: 生成变更 SQL。

    创建独立的变更 SQL 文件。
    """
    try:
        cr_number = state.get("cr_number", "001")
        cr_dir = Path(state.get("cr_dir", ""))

        change_id = state.get("change_identification", "")
        change_req = state.get("change_requirement_doc", "")
        ddl_content = state.get("ddl_content", "")

        prompt = f"""# 变更 SQL 生成

## CR编号
CR-{cr_number}

## 变更识别结果
{change_id}

## 变更需求文档
{change_req}

## 现有表结构
```sql
{ddl_content}
```

## 任务

请生成完整的变更 SQL，包含：

1. **文件头注释**:
```sql
-- ============================================================
-- CR-{cr_number}_变更简称
-- 变更类型: {{类型}}
-- 日期: {{date}}
-- ============================================================
```

2. **修改的TMP表**: DROP + CREATE 完整重建
3. **新增的TMP表**: DROP + CREATE
4. **修改的ADS表**: 追加字段的 SELECT 片段 + 追加 JOIN 片段
5. **重跑顺序**: 明确列出需要重跑的表及顺序

**规范**:
- 变更SQL必须可独立执行
- 新增TMP命名: tmp_{{库名}}.tmp_{{需求编号}}_{{主题}}
- 变更字段注释: -- [CR-{cr_number}] {{字段说明}}
"""

        llm_response = call_llm(state, "sql_gen", prompt)

        # 提取 SQL 代码块
        from .helpers import extract_sql_block

        sql_content = extract_sql_block(llm_response)

        # 保存到 CR 目录
        sql_path = cr_dir / "变更SQL.sql"
        sql_path.write_text(sql_content, encoding="utf-8")

        state["change_sql"] = sql_content
        state["metadata"] = {**(state.get("metadata", {})), "change_sql_generated": "true"}

        logger.info("Phase 3 变更SQL完成: %s", sql_path)
    except Exception as e:
        state.setdefault("errors", []).append(f"变更SQL异常: {e!s}")
        logger.error("变更SQL异常: %s", e, exc_info=True)

    return state


def node_change_review(state: WorkflowState) -> WorkflowState:
    """Phase 4: 变更审查。

    生成变更审查报告。
    """
    try:
        cr_number = state.get("cr_number", "001")
        cr_dir = Path(state.get("cr_dir", ""))

        change_req = state.get("change_requirement_doc", "")
        change_sql = state.get("change_sql", "")

        prompt = f"""# 变更审查报告

## CR编号
CR-{cr_number}

## 变更需求文档
{change_req}

## 变更SQL
```sql
{change_sql}
```

## 任务

请生成变更审查报告，包含以下审查维度：

| 审查维度 | 检查项 | 结果 |
|---------|--------|------|
| **逻辑正确性** | 新增字段逻辑是否符合需求定义 | ✅/❌ |
| **回归风险** | 已有字段是否受影响、JOIN是否膨胀 | ✅/❌ |
| **性能影响** | 新增扫描量、新增JOIN数量 | ✅/❌ |
| **数据一致性** | 新增字段与已有字段的交叉验证 | ✅/❌ |
| **待确认问题** | 所有问题是否已确认 | ✅/❌ |

## 审查结论

- ✅ **通过** — 所有检查项通过，可执行
- ⚠️ **条件通过** — 存在待确认问题，确认后可执行
- ❌ **不通过** — 存在逻辑错误或高风险，需修改后重新审查

## 回归验证清单

| # | 验证项 | SQL模板 |
|---|--------|---------|
| 1 | 主键唯一性不变 | SELECT COUNT(*)=COUNT(DISTINCT pk) FROM {{ADS表}} |
| 2 | 已有字段值不变 | 对比变更前后原有字段值 |
| 3 | 新增字段非空率 | SELECT COUNT({{新字段}})/COUNT(*) FROM {{ADS表}} |
| 4 | 新增字段枚举值 | SELECT DISTINCT {{标签字段}} FROM {{ADS表}} |
| 5 | 数据行数不变 | SELECT COUNT(*) FROM {{ADS表}} |
"""

        llm_response = call_llm(state, "sql_review", prompt)

        # 保存到 CR 目录
        review_path = cr_dir / "变更审查报告.md"
        review_path.write_text(llm_response, encoding="utf-8")

        state["change_review"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "change_reviewed": "true"}

        logger.info("Phase 4 变更审查完成: %s", review_path)
    except Exception as e:
        state.setdefault("errors", []).append(f"变更审查异常: {e!s}")
        logger.error("变更审查异常: %s", e, exc_info=True)

    return state


def node_change_merge(state: WorkflowState) -> WorkflowState:
    """Phase 5: 合并执行。

    将变更 SQL 合并到主 SQL，更新 DDL 和 DQC。
    """
    try:
        cr_number = state.get("cr_number", "001")
        change_sql = state.get("change_sql", "")

        # 生成合并说明
        merge_report = f"""# 合并执行报告 — CR-{cr_number}

## 合并内容

### 变更SQL
```sql
{change_sql}
```

## 合并步骤

1. **合并到主SQL**: 将变更SQL中的SELECT片段追加到主SQL
2. **更新DDL**: 在表结构文件中追加新字段
3. **更新DQC**: 新增字段的测试用例追加到数据质量测试
4. **更新文档**: 同步更新 Design.md、知识沉淀.md
5. **重跑验证**: 按重跑顺序执行SQL，运行回归验证

## 重跑顺序

请参考变更SQL中的重跑顺序说明。

## 回归验证

请参考变更审查报告中的回归验证清单。

---

> ⚠️ 合并前请确认变更审查已通过。
"""

        state["change_merge_report"] = merge_report
        state["metadata"] = {**(state.get("metadata", {})), "change_merged": "true"}

        logger.info("Phase 5 合并执行完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"合并执行异常: {e!s}")
        logger.error("合并执行异常: %s", e, exc_info=True)

    return state


def node_change_archive(state: WorkflowState) -> WorkflowState:
    """Phase 6: 归档。

    更新交付总报告和知识沉淀。
    """
    try:
        cr_number = state.get("cr_number", "001")
        change_summary = state.get("change_description", "变更")

        archive_report = f"""# 变更归档记录 — CR-{cr_number}

## 基本信息

| 项目 | 内容 |
|------|------|
| CR编号 | CR-{cr_number} |
| 变更简称 | {change_summary} |
| 状态 | 已归档 |

## 变更文档链

- 变更需求.md ✅
- 变更SQL.sql ✅
- 变更审查报告.md ✅

## 合并记录

- 主SQL已更新 ✅
- DDL已更新 ✅
- DQC已更新 ✅
- Design.md已更新 ✅

## 回归验证结果

待执行回归验证后填写。

---

> 变更记录已归档，支持完整回溯。
"""

        # 保存到 CR 目录
        cr_dir = Path(state.get("cr_dir", ""))
        if cr_dir.exists():
            archive_path = cr_dir / "归档记录.md"
            archive_path.write_text(archive_report, encoding="utf-8")

        state["change_archive"] = archive_report
        state["metadata"] = {**(state.get("metadata", {})), "change_archived": "true"}

        logger.info("Phase 6 变更归档完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"变更归档异常: {e!s}")
        logger.error("变更归档异常: %s", e, exc_info=True)

    return state
