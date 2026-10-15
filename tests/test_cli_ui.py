"""CLI --ui 接线测试：参数解析、gradio 缺失降级、需求文件校验。

注意：cli.main 模块在 Windows 上会替换 sys.stdout（GBK 兼容），
因此一律测试内延迟导入，避免影响 pytest 的 stdout capture（同 test_cli.py 纪律）。
"""

from __future__ import annotations

import argparse

import pytest


def _parser():
    from src.aqueduct.cli.main import create_parser

    return create_parser()


def _ns(**kw) -> argparse.Namespace:
    """构造 dev 子命令命名空间（缺省 + 覆盖）。"""
    base = dict(
        command="dev",
        requirement=None,
        ui=False,
        output=None,
        resume=False,
        sql_file=None,
        skill_dir=None,
        tool_dir=None,
        verbose=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


class TestParser:
    def test_parser_accepts_dev_ui_without_requirement(self):
        args = _parser().parse_args(["dev", "--ui"])
        assert args.ui is True
        assert args.requirement is None

    def test_parser_still_accepts_requirement_with_ui(self):
        args = _parser().parse_args(["dev", "--ui", "req.md"])
        assert args.ui is True
        assert args.requirement == "req.md"

    def test_parser_ui_defaults_false(self):
        args = _parser().parse_args(["dev", "req.md"])
        assert args.ui is False


class TestDevModeDispatch:
    def test_dev_mode_without_requirement_returns_usage_error(self, capsys):
        from src.aqueduct.cli.main import _dev_mode

        assert _dev_mode(_ns(requirement=None, ui=False)) == 1
        out = capsys.readouterr().out
        assert "aqueduct dev" in out or "requirement" in out


class TestDevUi:
    def test_dev_ui_gradio_missing_clear_error(self, capsys, monkeypatch):
        import importlib.util

        from src.aqueduct.cli.main import _dev_ui

        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
        assert _dev_ui(_ns(ui=True)) == 2
        assert "aqueduct[ui]" in capsys.readouterr().out

    def test_dev_ui_missing_requirement_file(self, capsys, monkeypatch):
        import importlib.util

        from src.aqueduct.cli.main import _dev_ui

        # 环境无关：强制 gradio 探测通过（CI 无 gradio 也要走到文件校验分支）
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
        assert _dev_ui(_ns(ui=True, requirement="不存在的文件.md")) == 1

    def test_dev_ui_launches_with_prefilled_text(self, monkeypatch):
        # 惰性导入路径：launch 收到预填参数即返回（不真起服务）。
        # app.py 真实 import gradio，CI（无 gradio）跳过；本地装 aqueduct[ui] 后执行。
        import importlib

        pytest.importorskip("gradio")

        ui_app = importlib.import_module("src.aqueduct.ui.app")

        captured = {}
        monkeypatch.setattr(ui_app, "launch", lambda **kw: captured.update(kw))

        from src.aqueduct.cli.main import _dev_ui

        assert _dev_ui(_ns(ui=True, requirement=None)) == 0
        assert captured == {"requirement_text": "", "output_dir": None, "resume": False}


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
