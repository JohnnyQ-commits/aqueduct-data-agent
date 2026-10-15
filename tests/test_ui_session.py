"""ui/session.py — UiSession 线程握手与进度渲染单元测试（无 gradio 依赖）。

线程模型：worker 线程跑 dev()，经 events/answers 两个 queue 与 UI 握手；
本测试以真实线程 + 真实 queue 验证握手契约。
"""

import threading

import pytest

from src.aqueduct.ui.session import ConfirmRequest, UiSession, pair_answers


def _summary_with_questions(n: int = 2) -> str:
    questions = "\n".join(f"{i}. 问题{i}" for i in range(1, n + 1))
    return f"## 需求摘要\n\n### 待确认问题\n\n{questions}\n"


class TestProgress:
    def test_progress_events_enqueued_and_idx1_resets(self):
        session = UiSession()
        session.on_progress("requirement", 1, 7, {})
        assert session.current_idx == 1
        # 自动续跑重跑：idx==1 归零重推
        session.on_progress("design", 2, 7, {})
        session.on_progress("requirement", 1, 7, {})
        assert session.current_idx == 1

        events = []
        while not session.events.empty():
            events.append(session.events.get_nowait())
        assert events[0] == ("progress", 1, 7)
        assert events[-1] == ("progress", 1, 7)
        assert len(events) == 3


class TestConfirm:
    def test_confirm_with_answers_continues_and_updates_state(self, tmp_path):
        summary = _summary_with_questions(2)
        state = {
            "requirement_summary": summary,
            "metadata": {"output_dir": str(tmp_path)},
        }
        (tmp_path / "Phase1-需求理解摘要.md").write_text(summary, encoding="utf-8")

        session = UiSession()
        result_holder = {}

        def worker():
            result_holder["ret"] = session.on_confirm(state)

        t = threading.Thread(target=worker)
        t.start()

        ev, payload = session.events.get(timeout=5)
        assert ev == "confirm"
        assert isinstance(payload, ConfirmRequest)
        assert payload.questions == ["问题1", "问题2"]

        session.submit("continue", ["答案一", "答案二"])
        t.join(timeout=5)
        assert result_holder["ret"] is True

        # state 更新（后续 phase 读 requirement_summary）
        assert "### 用户澄清记录" in state["requirement_summary"]
        assert "**A1**: 答案一" in state["requirement_summary"]
        # 文件追加
        content = (tmp_path / "Phase1-需求理解摘要.md").read_text(encoding="utf-8")
        assert "### 用户澄清记录" in content
        # confirm_resolved 事件
        ev, _payload = session.events.get(timeout=5)
        assert ev == "confirm_resolved"

    def test_confirm_stop_returns_false(self, tmp_path):
        state = {
            "requirement_summary": _summary_with_questions(1),
            "metadata": {"output_dir": str(tmp_path)},
        }
        session = UiSession()
        ret = {}

        t = threading.Thread(target=lambda: ret.update(val=session.on_confirm(state)))
        t.start()
        session.events.get(timeout=5)  # 等确认请求
        session.submit("stop", [])
        t.join(timeout=5)

        assert ret["val"] is False
        assert "### 用户澄清记录" not in state["requirement_summary"]

    def test_confirm_no_summary_short_circuits(self):
        session = UiSession()
        assert session.on_confirm({"requirement_summary": ""}) is True
        assert session.events.empty()


class TestPairAnswers:
    def test_pads_missing_answers(self):
        pairs = pair_answers(["问题1", "问题2"], ["答案一"])
        assert pairs == [
            {"question": "问题1", "answer": "答案一"},
            {"question": "问题2", "answer": "(未回答)"},
        ]

    def test_ignores_extra_lines(self):
        pairs = pair_answers(["问题1"], ["答案一", "多余行"])
        assert pairs == [{"question": "问题1", "answer": "答案一"}]


class TestRun:
    def test_run_synthesizes_error_result_on_exception(self):
        # dev() 抛异常（如 LLM 未配置）时 worker 必须落 "done" 事件，生成器才能终止
        from src.aqueduct.core import Aqueduct
        from src.aqueduct.ui import session as session_mod

        session = UiSession()

        class _Boom:
            def dev(self, *args, **kwargs):
                raise RuntimeError("LLM 未配置")

        monkey = pytest.MonkeyPatch()
        monkey.setattr(session_mod, "Aqueduct", _Boom)
        try:
            session.run("需求文本")
        finally:
            monkey.undo()

        ev, payload = session.events.get(timeout=5)
        assert ev == "done"
        assert payload.halted is True
        assert any("LLM 未配置" in e for e in payload.errors)
        # Aqueduct 未被本测试真实调用（仅验证异常合成路径）
        assert Aqueduct is not None


class TestRenderProgress:
    def test_render_progress_marks_phases(self):
        session = UiSession()
        session.on_progress("design", 2, 7, {})
        md = session.render_progress()
        # 之前 ✅、当前 ▶️、之后 ⬜
        assert "需求理解" in md and "✅" in md
        assert "设计方案" in md and "▶️" in md
        assert "报告交付" in md and "⬜" in md


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
