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


@pytest.fixture(autouse=True)
def _isolate_dynamic_knowledge(tmp_path, monkeypatch):
    """知识回流写入重定向到 tmp：测试永不写真实 internal/knowledge。

    缺陷实录见 tests/test_knowledge_isolation.py——node_report 后处理
    （_update_domain_json / _regenerate_semantic_docs）曾硬编码写真实内部
    知识库，任何带 DDL/SQL 的测试都在污染召回语料（kn_spec_test 域即测试
    泄漏产物、order_refund_iterative 内容漂移、test_memory 环境性失败）。
    写点已改走 settings.dynamic_knowledge_dir，此处把动态目录钉到 tmp_path
    兜底未来新写点；monkeypatch 自动还原，不影响真实运行。
    """
    from src.aqueduct.config.settings import get_settings

    monkeypatch.setattr(
        get_settings(),
        "dynamic_knowledge_dir",
        tmp_path / "dynamic_knowledge",
        raising=False,
    )
    yield


@pytest.fixture(autouse=True, scope="session")
def _isolate_settings_from_env_file():
    """Settings 与开发者本地 .env 隔离——断言默认值的测试不得沾染真实配置。

    实录（2026-09-17）：.env 写入 AQUEDUCT_LLM_BACKEND=sdk +
    AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK 后，断言"默认 backend=auto /
    默认空映射"的测试被 env_file 泄入的值击穿；同日内部库 .env 配
    backend=cli + cli_effort=high 再击穿一轮。pydantic-settings 在
    Settings() 实例化时读取 env_file，此处把 env_file 钉为 None：
    测试中的配置一律来自 monkeypatch.setenv（显式、可追溯），真实 .env
    只影响真实运行。自带独立 env 文件与隔离根的用例（test_platform_bdp
    的 load_dp_env 组、test_status_platform）不走 Settings，不受影响。
    """
    from unittest.mock import patch

    from src.aqueduct.config.settings import Settings

    with patch.object(Settings, "model_config", {**Settings.model_config, "env_file": None}):
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
