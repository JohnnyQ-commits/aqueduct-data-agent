"""Aqueduct UI 层 — Gradio 最小演示（可选依赖）。

本包 __init__ 不 import gradio：所有入口惰性转发到 app.py，
非 --ui 路径（CLI 常规命令、全部测试）永不触碰 gradio。
安装可选依赖：pip install "aqueduct[ui]"
"""

from __future__ import annotations


def create_app(*args, **kwargs):
    """构建 Gradio Blocks 应用（惰性导入 gradio）。"""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)


def launch(*args, **kwargs):
    """构建并启动 Gradio 服务（惰性导入 gradio）。"""
    from .app import launch as _launch

    return _launch(*args, **kwargs)
