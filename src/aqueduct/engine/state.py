"""工作流状态定义 — WorkflowState。

使用 TypedDict 定义 LangGraph DAG 节点间传递的状态结构。
所有节点只负责参数组装和状态透传，无 Prompt、无业务逻辑。
"""

from __future__ import annotations

from typing import TypedDict


class WorkflowState(TypedDict, total=False):
    """工作流状态 — 开发/审查双模式共享。

    total=False 表示所有字段可选，节点按需写入。

    状态流转:
        开发模式: requirement → design → ddl → sql → review → dqc → report
        审查模式: requirement → review → dqc → report

    字段说明:
        requirement:     需求文档内容（输入）
        mode:            工作流模式："dev"（开发）或 "review"（审查）
        domain_id:       匹配的业务域 ID
        domain_context:  业务域本体模型上下文（JSON 字符串）
        design_scheme:   设计方案内容
        ddl_content:     目标表 DDL 语句
        sql_content:     核心 ETL SQL
        review_result:   代码审查结果
        dqc_result:      DQC 质检结果
        artifacts:       产出文件路径列表
        errors:          错误消息列表
        metadata:        附加元数据（时间戳、Token 用量等）
    """

    # === 输入 ===
    requirement: str  # 需求文档内容
    mode: str  # "dev" | "review"

    # === 上下文 ===
    domain_id: str  # 匹配的业务域 ID
    domain_context: str  # 业务域本体模型上下文

    # === Phase 2: 设计方案 ===
    design_scheme: str  # 设计方案内容

    # === Phase 3: DDL ===
    ddl_content: str  # 目标表 DDL 语句

    # === Phase 4: SQL ===
    sql_content: str  # 核心 ETL SQL

    # === Phase 4.5: 代码审查 ===
    review_result: str  # 代码审查结果

    # === Phase 5: DQC ===
    dqc_result: str  # DQC 质检结果

    # === Phase 6: 报告交付 ===
    artifacts: list[str]  # 产出文件路径列表

    # === 错误处理 ===
    errors: list[str]  # 错误消息列表

    # === 元数据 ===
    metadata: dict[str, str]  # 附加元数据

    # === 变更管理 ===
    original_requirement: str  # 原始需求文档内容
    new_requirement: str  # 新需求文档内容
    change_description: str  # 变更描述
    change_identification: str  # 变更识别结果
    change_requirement_doc: str  # 变更需求文档
    change_sql: str  # 变更 SQL
    change_review: str  # 变更审查报告
    change_merge_report: str  # 合并执行报告
    change_archive: str  # 归档记录
    cr_number: str  # CR 编号
    cr_dir: str  # CR 目录路径
