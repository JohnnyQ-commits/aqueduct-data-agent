"""DAG 节点函数包。

每个节点模块对应工作流中的一个阶段：
- requirement: Phase 1 需求理解
- design: Phase 2 方案设计
- ddl: Phase 3 DDL 生成
- sql: Phase 4 SQL 开发
- review: Phase 4.5 代码审查
- dqc: Phase 5 DQC 质检
- report: Phase 6 报告交付
- change: 变更管理（6 阶段）
"""

from .change import (
    node_change_archive,
    node_change_document,
    node_change_identify,
    node_change_merge,
    node_change_review,
    node_change_sql,
)
from .ddl import node_ddl
from .design import node_design
from .dqc import node_dqc
from .report import node_report
from .requirement import node_requirement
from .review import node_review
from .sql import node_sql

__all__ = [
    "node_change_archive",
    "node_change_document",
    "node_change_identify",
    "node_change_merge",
    "node_change_review",
    "node_change_sql",
    "node_ddl",
    "node_design",
    "node_dqc",
    "node_report",
    "node_requirement",
    "node_review",
    "node_sql",
]
