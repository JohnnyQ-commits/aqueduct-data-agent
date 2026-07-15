"""Test configuration — import all modules to trigger @register decorators."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_task_logging():
    """阻止测试创建真实任务日志文件和输出目录。

    _run_pipeline() 调用 setup_task_logging()，
    该函数在目录不存在时会静默降级（返回 None），
    但为避免测试期间产生任何文件系统副作用，全局 mock 为 no-op。
    """
    from unittest.mock import patch

    with patch("src.aqueduct.core.setup_task_logging", return_value=None):
        yield


@pytest.fixture(autouse=True, scope="session")
def _no_output_dir_creation():
    """阻止测试在项目 output/ 目录下创建任何子目录。

    拦截 Path.mkdir，当目标路径位于项目 output/ 下时跳过创建。
    解决 node_review、save_manifest 等代码路径在测试中意外创建
    output/test/、output/output/ 等目录的问题。
    """
    import os
    from pathlib import Path
    from unittest.mock import patch

    _project_root = Path(__file__).resolve().parent.parent
    _output_root = _project_root / "output"
    _original_mkdir = Path.mkdir

    def _guarded_mkdir(self, *args, **kwargs):
        try:
            resolved = self.resolve()
            output_str = str(_output_root)
            resolved_str = str(resolved)
            # 跳过 output/ 及其子目录的创建
            if resolved_str == output_str or resolved_str.startswith(output_str + os.sep):
                return self
        except (OSError, ValueError):
            pass
        return _original_mkdir(self, *args, **kwargs)

    with patch.object(Path, "mkdir", _guarded_mkdir):
        yield


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
        executor,  # noqa: F401
        lineage,  # noqa: F401
        productivity,  # noqa: F401
        semantic,  # noqa: F401
        sync,  # noqa: F401
        validator,  # noqa: F401
    )
