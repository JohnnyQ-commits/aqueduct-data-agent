"""Aqueduct 全局异常定义。

所有领域异常继承自 `AqueductError`。
工具层异常继承自 `ToolError`。
Skill 层异常继承自 `SkillError`。
工作流/引擎异常继承自 `WorkflowError`。
LLM 层异常继承自 `LLMError`。
Memory/知识层异常继承自 `MemoryError`。

异常层级：
    AqueductError（根）
    ├── ToolError（工具执行失败）
    │   ├── ToolNotFoundError（工具未注册）
    │   └── ToolValidationError（入参校验失败）
    ├── SkillError（Skill 执行失败）
    │   └── SkillNotFoundError（Skill 未注册）
    ├── WorkflowError（DAG 执行失败）
    │   └── WorkflowNodeError（特定节点失败）
    ├── LLMError（LLM 调用失败）
    │   ├── LLMTimeoutError（LLM 调用超时）
    │   └── LLMContextExceededError（超出上下文窗口）
    ├── MemoryError（知识存储操作失败）
    │   └── DomainNotFoundError（业务域不存在）
    └── ConfigError（配置校验失败）
"""

from __future__ import annotations


class AqueductError(Exception):
    """Aqueduct 所有异常的根异常。"""

    code: str = "aqueduct_error"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ToolError(AqueductError):
    """工具执行失败时抛出。"""

    code = "tool_error"


class ToolNotFoundError(ToolError):
    """请求的工具未注册。"""

    code = "tool_not_found"


class ToolValidationError(ToolError):
    """工具入参校验失败。"""

    code = "tool_validation_error"


class SkillError(AqueductError):
    """Skill 执行失败时抛出。"""

    code = "skill_error"


class SkillNotFoundError(SkillError):
    """请求的 Skill 未注册。"""

    code = "skill_not_found"


class WorkflowError(AqueductError):
    """工作流/DAG 执行失败时抛出。"""

    code = "workflow_error"


class WorkflowNodeError(WorkflowError):
    """DAG 中某个特定节点失败。"""

    code = "workflow_node_error"


class LLMError(AqueductError):
    """LLM 调用失败时抛出。"""

    code = "llm_error"


class LLMTimeoutError(LLMError):
    """LLM 调用超时时抛出。"""

    code = "llm_timeout"


class LLMContextExceededError(LLMError):
    """输入超过模型的上下文窗口时抛出。"""

    code = "llm_context_exceeded"


class MemoryError(AqueductError):
    """知识存储操作失败时抛出。"""

    code = "memory_error"


class DomainNotFoundError(MemoryError):
    """请求的业务域不存在。"""

    code = "domain_not_found"


class ConfigError(AqueductError):
    """配置校验失败时抛出。"""

    code = "config_error"
