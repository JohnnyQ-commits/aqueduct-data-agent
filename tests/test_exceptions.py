"""异常层次结构测试。"""

from __future__ import annotations

import pytest

from src.aqueduct.exceptions import (
    AqueductError,
    ConfigError,
    DomainNotFoundError,
    LLMContextExceededError,
    LLMError,
    MemoryError,
    SkillError,
    SkillNotFoundError,
    ToolError,
    ToolNotFoundError,
    ToolValidationError,
    WorkflowError,
    WorkflowNodeError,
)


class TestExceptionHierarchy:
    """异常继承关系测试。"""

    def test_aqueduct_error_is_root(self):
        assert issubclass(AqueductError, Exception)

    def test_tool_errors_inherit_aqueduct(self):
        assert issubclass(ToolError, AqueductError)
        assert issubclass(ToolNotFoundError, ToolError)
        assert issubclass(ToolValidationError, ToolError)

    def test_skill_errors_inherit_aqueduct(self):
        assert issubclass(SkillError, AqueductError)
        assert issubclass(SkillNotFoundError, SkillError)

    def test_workflow_errors_inherit_aqueduct(self):
        assert issubclass(WorkflowError, AqueductError)
        assert issubclass(WorkflowNodeError, WorkflowError)

    def test_llm_errors_inherit_aqueduct(self):
        assert issubclass(LLMError, AqueductError)
        assert issubclass(LLMContextExceededError, LLMError)

    def test_memory_errors_inherit_aqueduct(self):
        assert issubclass(MemoryError, AqueductError)
        assert issubclass(DomainNotFoundError, MemoryError)

    def test_config_error_inherits_aqueduct(self):
        assert issubclass(ConfigError, AqueductError)


class TestExceptionMessages:
    """异常消息测试。"""

    def test_aqueduct_error_message(self):
        err = AqueductError("test error")
        assert "test error" in str(err)

    def test_tool_not_found_message(self):
        err = ToolNotFoundError("validator")
        assert "validator" in str(err)

    def test_skill_not_found_message(self):
        err = SkillNotFoundError("requirement_clarify")
        assert "requirement_clarify" in str(err)

    def test_exceptions_can_be_caught_as_aqueduct_error(self):
        with pytest.raises(AqueductError):
            raise ToolNotFoundError("test_tool")
