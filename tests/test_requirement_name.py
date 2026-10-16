"""UI 需求名透传单元测试。

2026-10-15 实录：Gradio UI 粘贴文本跑管道，交付物叫 Phase4-requirement.sql、
报告头需求名称显示 "requirement"（core.py 文本输入路径硬编码占位名）。
本批：文本路径从需求标题/首行派生名 + dev/launch/session 全链透传显式名。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.aqueduct.utils.file_parser import derive_requirement_name


class TestDeriveRequirementName:
    def test_heading_preferred(self):
        text = "\n---\ntitle: x\n\n# 网点每日收派件汇总\n\n正文"
        assert derive_requirement_name(text) == "网点每日收派件汇总"

    def test_first_nonempty_line_fallback(self):
        assert derive_requirement_name("电商每日统计需求\n正文") == "电商每日统计需求"

    def test_sanitize_path_chars(self):
        name = derive_requirement_name("# 收派/汇总:日结*报表?")
        for ch in '\\/:*?"<>|':
            assert ch not in name

    def test_truncated_to_30_chars(self):
        name = derive_requirement_name("# " + "超长" * 30)
        assert len(name) <= 30

    def test_empty_text_falls_back(self):
        assert derive_requirement_name("") == "requirement"
        assert derive_requirement_name("   \n  \n") == "requirement"


class TestDevKwarg:
    def test_dev_accepts_requirement_name_kwarg(self):
        """dev() 签名带 requirement_name 且透传到 state metadata。"""
        import inspect

        from src.aqueduct.core import Aqueduct

        sig = inspect.signature(Aqueduct.dev)
        assert "requirement_name" in sig.parameters


class TestSessionPassthrough:
    def test_session_run_passes_requirement_name(self, monkeypatch):
        from src.aqueduct.ui import session as session_mod

        captured = {}

        class FakeAgent:
            def dev(self, req, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(state={}, artifacts=[], errors=[], halted=False, success=True)

        monkeypatch.setattr(session_mod, "Aqueduct", FakeAgent)
        s = session_mod.UiSession()
        s.run("需求文本", requirement_name="网点每日收派件汇总")
        assert captured.get("requirement_name") == "网点每日收派件汇总"


class TestDevUiStem:
    def test_dev_ui_passes_file_stem(self, monkeypatch):
        """CI 无 gradio：伪造 find_spec + app 模块，专测 stem 透传。"""
        import importlib.util
        import pathlib
        import sys
        import tempfile
        from types import ModuleType, SimpleNamespace

        from src.aqueduct.cli.main import _dev_ui

        captured = {}

        def fake_launch(**kwargs):
            captured.update(kwargs)

        fake_app = ModuleType("src.aqueduct.ui.app")
        fake_app.launch = fake_launch
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name: object() if name == "gradio" else real_find_spec(name),
        )
        monkeypatch.setitem(sys.modules, "src.aqueduct.ui.app", fake_app)

        with tempfile.TemporaryDirectory() as d:
            req = pathlib.Path(d) / "网点每日收派件汇总.md"
            req.write_text("# t\n", encoding="utf-8")
            args = SimpleNamespace(requirement=str(req), output=None, resume=False)
            rc = _dev_ui(args)
        assert rc == 0
        assert captured.get("requirement_name") == "网点每日收派件汇总"
