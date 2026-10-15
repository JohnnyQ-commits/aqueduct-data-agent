"""ui/app.py 冒烟测试 — 需要 gradio（CI 无 gradio 时干净跳过）。"""

from types import SimpleNamespace

import pytest

gradio = pytest.importorskip("gradio")


def test_create_app_returns_blocks():
    """create_app 构建 Blocks 成功（线程仅在开始点击时由 _start 创建，构建期不跑管道）。"""
    from src.aqueduct.ui.app import create_app

    demo = create_app(requirement_text="预填需求")
    assert isinstance(demo, gradio.Blocks)


def _extract_start_fn(demo):
    fns = demo.fns.values() if hasattr(demo.fns, "values") else demo.fns
    fn = next(f for f in fns if getattr(f.fn, "__name__", "") == "_start")
    return fn.fn


def _locate_components(demo):
    """按类型+定义顺序定位 create_app 内的组件（定义顺序即布局顺序）。

    Markdown: [标题, 进度, 状态, 问答信息]；Button: [开始, 确认, 停止]。
    """
    blocks = list(demo.blocks.values())
    mds = [b for b in blocks if type(b).__name__ == "Markdown"]
    btns = [b for b in blocks if type(b).__name__ == "Button"]
    group = next(b for b in blocks if type(b).__name__ == "Group")
    return {
        "progress": mds[1],
        "status": mds[2],
        "qa_md": mds[3],
        "btn_start": btns[0],
        "group": group,
    }


def test_start_generator_consumes_event_stream(monkeypatch):
    """_start 生成器必须能消费真实事件契约（回归：progress 是 3 元组，2 元组解包 ValueError）。

    覆盖全事件流：progress → confirm → confirm_resolved → progress → done。
    """
    from src.aqueduct.ui import app as ui_app
    from src.aqueduct.ui.session import ConfirmRequest, UiSession

    class FakeSession(UiSession):
        """预填事件流、run() 空转（不真跑管道），事件时序确定。"""

        def __init__(self):
            super().__init__()
            self.events.put(("progress", 1, 7))
            self.events.put(("confirm", ConfirmRequest(summary="摘要", questions=["问题1"])))
            self.events.put(("confirm_resolved", None))
            self.events.put(("progress", 2, 7))
            self.events.put(
                ("done", SimpleNamespace(success=True, halted=False, errors=[], artifacts=[]))
            )

        def run(self, *args, **kwargs):
            return

    monkeypatch.setattr(ui_app, "UiSession", FakeSession)
    demo = ui_app.create_app()
    start = _extract_start_fn(demo)
    comp = _locate_components(demo)

    updates = list(start("需求文本"))

    def _get(update, key):
        """gradio 6 的 gr.update() 返回 dict；兼容对象属性两种形态。"""
        return update[key] if isinstance(update, dict) else getattr(update, key)

    # 第一次 yield：运行中状态（按钮禁用）
    assert _get(updates[0][comp["btn_start"]], "interactive") is False
    # confirm 事件：问答面板可见、问题渲染（第一次 yield 也含 group 但 visible=False）
    confirm_yield = next(
        u for u in updates[1:] if comp["group"] in u and _get(u[comp["group"]], "visible") is True
    )
    assert _get(confirm_yield[comp["group"]], "visible") is True
    assert "问题1" in _get(confirm_yield[comp["qa_md"]], "value")
    # done 事件：终态横幅 + 按钮恢复
    final = updates[-1]
    assert "✅" in _get(final[comp["status"]], "value")
    assert _get(final[comp["btn_start"]], "interactive") is True
    # 生成器正常终止（收到 done 后 return，不抛异常）


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
