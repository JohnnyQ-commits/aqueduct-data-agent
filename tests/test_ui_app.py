"""ui/app.py 冒烟测试 — 需要 gradio（CI 无 gradio 时干净跳过）。"""

import pytest

gradio = pytest.importorskip("gradio")


def test_create_app_returns_blocks():
    """create_app 构建 Blocks 成功（线程仅在开始点击时由 _start 创建，构建期不跑管道）。"""
    from src.aqueduct.ui.app import create_app

    demo = create_app(requirement_text="预填需求")
    assert isinstance(demo, gradio.Blocks)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
