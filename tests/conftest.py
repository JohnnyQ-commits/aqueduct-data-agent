"""Test configuration — import all modules to trigger @register decorators."""

from __future__ import annotations


def pytest_configure(config):
    """在测试开始前导入所有工具和 Skill 模块，触发注册装饰器。"""
    # 导入所有 Tool 模块（触发 @register_tool）
    # 导入所有 Skill 模块（触发 @register_skill）
    from src.aqueduct.skills import (
        code_review,  # noqa: F401
        ddl_generate,  # noqa: F401
        design_scheme,  # noqa: F401
        dqc_quality,  # noqa: F401
        report_delivery,  # noqa: F401
        requirement_clarify,  # noqa: F401
        sql_develop,  # noqa: F401
    )
    from src.aqueduct.skills.extra import productivity_board  # noqa: F401
    from src.aqueduct.tools import (
        batch_query,  # noqa: F401
        design,  # noqa: F401
        dqc,  # noqa: F401
        estimator,  # noqa: F401
        lineage,  # noqa: F401
        productivity,  # noqa: F401
        semantic,  # noqa: F401
        sync,  # noqa: F401
        validator,  # noqa: F401
    )
