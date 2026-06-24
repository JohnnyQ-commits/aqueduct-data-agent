"""工作流状态定义 — WorkflowState。

使用 TypedDict 定义 LangGraph DAG 节点间传递的状态结构。
所有节点只负责参数组装和状态透传，无 Prompt、无业务逻辑。

状态按职责拆分为子类型（文档与类型提示用途，WorkflowState 保持扁平兼容）:
    PhaseContext        — 输入上下文（业务域、需求摘要）
    PhaseArtifacts      — 产出物（SQL、DDL、审查结果）
    ChangeManagementState — 变更管理专用字段
"""

from __future__ import annotations

import sys
from typing import TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired


# === 子类型：输入上下文 ===


class PhaseContext(TypedDict, total=False):
    """输入上下文 — 由 Phase 1 需求理解阶段写入。"""

    domain_id: str  # 匹配的业务域 ID
    domain_context: str  # 业务域本体模型上下文
    requirement_summary: str  # 需求摘要


# === 子类型：产出物 ===


class PhaseArtifacts(TypedDict, total=False):
    """产出物 — 由 Phase 2-6 各节点写入。"""

    design_scheme: str
    ddl_content: str
    sql_content: str
    sql_file: str
    review_result: str
    dqc_result: str
    validation_result: dict
    lineage_result: dict
    cost_result: dict
    fix_iterations: int


# === 子类型：变更管理 ===


class ChangeManagementState(TypedDict, total=False):
    """变更管理专用字段 — 仅在 change 模式下使用。"""

    original_requirement: str
    new_requirement: str
    change_description: str
    change_identification: str
    change_requirement_doc: str
    change_sql: str
    change_review: str
    change_merge_report: str
    change_archive: str
    cr_number: str
    cr_dir: str


# === 主状态（向后兼容，保持扁平结构） ===


class WorkflowState(TypedDict):
    """工作流状态 — 开发/审查双模式共享。

    状态流转:
        开发模式: requirement → design → ddl → sql → review → dqc → report
        审查模式: requirement → review → dqc → report

    必填字段:
        requirement: 需求文档内容（输入）
        mode:        工作流模式："dev" | "review" | "change"
        errors:      错误消息列表
        artifacts:   产出文件路径列表

    可选字段: 由各个 Phase 节点按需写入。
    子类型定义见 PhaseContext / PhaseArtifacts / ChangeManagementState。
    """

    # === 必填输入 ===
    requirement: str  # 需求文档内容
    mode: str  # "dev" | "review" | "change"
    errors: list[str]  # 错误消息列表
    artifacts: list[str]  # 产出文件路径列表

    # === 可选：上下文（见 PhaseContext） ===
    domain_id: NotRequired[str]
    domain_context: NotRequired[str]

    # === 可选：Phase 2 设计方案 ===
    design_scheme: NotRequired[str]

    # === 可选：Phase 3 DDL ===
    ddl_content: NotRequired[str]

    # === 可选：Phase 4 SQL ===
    sql_content: NotRequired[str]
    sql_file: NotRequired[str]
    external_sql_path: NotRequired[str]  # 外部 SQL 文件路径（跳过 LLM 生成）

    # === 可选：Phase 4.5 代码审查 ===
    review_result: NotRequired[str]
    fix_iterations: NotRequired[int]  # 审查→修复循环当前迭代次数

    # === 可选：Phase 5 DQC ===
    dqc_result: NotRequired[str]

    # === 可选：元数据 ===
    metadata: NotRequired[dict[str, str]]

    # === 可选：中间结果（由自动校验等子任务写入） ===
    requirement_summary: NotRequired[str]
    validation_result: NotRequired[dict]
    lineage_result: NotRequired[dict]
    cost_result: NotRequired[dict]
    online_sql: NotRequired[str]
    changed_sql: NotRequired[str]

    # === 可选：变更管理（见 ChangeManagementState） ===
    original_requirement: NotRequired[str]
    new_requirement: NotRequired[str]
    change_description: NotRequired[str]
    change_identification: NotRequired[str]
    change_requirement_doc: NotRequired[str]
    change_sql: NotRequired[str]
    change_review: NotRequired[str]
    change_merge_report: NotRequired[str]
    change_archive: NotRequired[str]
    cr_number: NotRequired[str]
    cr_dir: NotRequired[str]
