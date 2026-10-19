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


def _extract_fn(demo, name: str):
    fns = demo.fns.values() if hasattr(demo.fns, "values") else demo.fns
    return next(f for f in fns if getattr(f.fn, "__name__", "") == name).fn


def _locate_components(demo):
    """按类型+定义顺序定位 create_app 内的组件（定义顺序即布局顺序）。

    Markdown: [标题, 进度, 状态, 问答信息]；Button: [开始, 确认, 停止]。
    """
    blocks = list(demo.blocks.values())
    mds = [b for b in blocks if type(b).__name__ == "Markdown"]
    btns = [b for b in blocks if type(b).__name__ == "Button"]
    group = next(b for b in blocks if type(b).__name__ == "Group")
    state = next(b for b in blocks if type(b).__name__ == "State")
    return {
        "progress": mds[1],
        "status": mds[2],
        "qa_md": mds[3],
        "btn_start": btns[0],
        "group": group,
        "state": state,
    }


def _get(update, key):
    """gradio 6 的 gr.update() 返回 dict；兼容对象属性与裸值（State 可直接
    yield session 本体）三种形态。"""
    if isinstance(update, dict):
        return update.get(key)
    value = getattr(update, key, None)
    return value if value is not None else update


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
                (
                    "done",
                    SimpleNamespace(
                        success=True, halted=False, errors=[], artifacts=[], degradations=[]
                    ),
                )
            )

        def run(self, *args, **kwargs):
            return

    monkeypatch.setattr(ui_app, "UiSession", FakeSession)
    demo = ui_app.create_app()
    start = _extract_start_fn(demo)
    comp = _locate_components(demo)

    updates = list(start("需求文本", None))
    # _start 把 session 存入应用级单例槽（刷新恢复的依据）
    assert demo._aq_session_holder["session"] is not None

    def _get(update, key):
        """gradio 6 的 gr.update() 返回 dict；兼容对象属性两种形态。"""
        return update[key] if isinstance(update, dict) else getattr(update, key)

    # 第一次 yield：运行中状态（按钮禁用）    assert _get(updates[0][comp["btn_start"]], "interactive") is False
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


class TestDuplicateStartGuard:
    """防重复开始（2026-10-16 实录：QA 等待中再次点开始 → 双 worker 并发
    写同一输出目录 + 旧 worker 永久阻塞泄漏 + 任务日志双写）。"""

    def test_start_rejects_when_session_alive(self):
        from src.aqueduct.ui import app as ui_app
        from src.aqueduct.ui.session import UiSession

        started = []

        class AliveSession(UiSession):
            def is_alive(self):
                return True

            def start_worker(self, *args, **kwargs):
                started.append(1)

        demo = ui_app.create_app()
        demo._aq_session_holder["session"] = AliveSession()
        start = _extract_start_fn(demo)
        comp = _locate_components(demo)

        updates = list(start("需求文本", None))
        assert started == []  # 未起新 worker
        # 状态块提示运行中 + 按钮保持禁用 + 重新挂 session（恢复作答通道）
        assert "运行中" in _get(updates[-1][comp["status"]], "value")
        assert _get(updates[-1][comp["btn_start"]], "interactive") is False
        assert _get(updates[-1][comp["state"]], "value") is not None

    def test_start_allowed_after_session_finished(self, monkeypatch):
        from src.aqueduct.ui import app as ui_app
        from src.aqueduct.ui.session import UiSession

        started = []

        class FakeSession(UiSession):
            def is_alive(self):
                return False

            def start_worker(self, *args, **kwargs):
                started.append(1)

            def run(self, *args, **kwargs):
                pass

        # 必须替换 ui_app 命名空间的 UiSession——否则 _start 会起真管道打 LLM
        monkeypatch.setattr(ui_app, "UiSession", FakeSession)
        demo = ui_app.create_app()
        demo._aq_session_holder["session"] = FakeSession()
        start = _extract_start_fn(demo)

        gen = start("需求文本", None)
        next(gen)  # 第一次 yield 即已起新 worker
        assert started == [1]


class TestRestoreOnLoad:
    """页面加载恢复（2026-10-16 实录：刷新页面丢失进度与 QA 面板，worker
    不知情继续跑/永久卡死在等待作答）。恢复 = 接回单例 session 继续消费事件流。"""

    def test_restore_reattaches_live_session_with_pending_confirm(self):
        from src.aqueduct.ui import app as ui_app
        from src.aqueduct.ui.session import ConfirmRequest, UiSession

        class LiveSession(UiSession):
            def __init__(self):
                super().__init__()
                self.events.put(
                    (
                        "done",
                        SimpleNamespace(
                            success=True,
                            halted=False,
                            errors=[],
                            artifacts=["a.sql"],
                            degradations=[],
                        ),
                    )
                )

            def is_alive(self):
                return True

        session = LiveSession()
        session.current_idx = 3
        session.pending_confirm = ConfirmRequest(summary="摘要内容待恢复", questions=["问题1"])

        demo = ui_app.create_app()
        demo._aq_session_holder["session"] = session
        restore = _extract_fn(demo, "_restore")
        comp = _locate_components(demo)

        updates = list(restore())
        # 首个 yield：进度快照 + QA 面板重挂 + session 重新挂载（作答通道接回）
        first = updates[0]
        assert "▶️" in _get(first[comp["progress"]], "value")
        assert _get(first[comp["group"]], "visible") is True
        assert "摘要内容待恢复" in _get(first[comp["qa_md"]], "value")
        assert _get(first[comp["btn_start"]], "interactive") is False
        assert _get(first[comp["state"]], "value") is session
        # 后续继续消费事件流直到 done（终态渲染，生成器终止）
        final = updates[-1]
        assert "✅" in _get(final[comp["status"]], "value")

    def test_restore_empty_when_no_session(self):
        from src.aqueduct.ui import app as ui_app

        demo = ui_app.create_app()
        restore = _extract_fn(demo, "_restore")
        assert list(restore()) == [{}] or list(restore()) == []

    def test_restore_renders_last_result_after_finish(self):
        from src.aqueduct.ui import app as ui_app
        from src.aqueduct.ui.session import UiSession

        class DeadSession(UiSession):
            def is_alive(self):
                return False

        session = DeadSession()
        demo = ui_app.create_app()
        demo._aq_session_holder["session"] = session
        demo._aq_session_holder["last_result"] = (
            session,
            SimpleNamespace(
                success=True, halted=False, errors=[], artifacts=["a.sql", "b.md"], degradations=[]
            ),
        )
        restore = _extract_fn(demo, "_restore")
        comp = _locate_components(demo)

        updates = list(restore())
        final = updates[-1]
        assert "✅" in _get(final[comp["status"]], "value")
        assert _get(final[comp["btn_start"]], "interactive") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
