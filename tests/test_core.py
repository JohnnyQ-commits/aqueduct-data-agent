"""_run_pipeline 和 _run_fix_loop 单元测试。

覆盖核心管道执行器的关键行为，避免触碰 LLM。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.aqueduct.core import _run_fix_loop, _run_pipeline
from src.aqueduct.exceptions import WorkflowHaltError

# ============================================================
# _run_pipeline 测试
# ============================================================


class TestRunPipeline:
    """_run_pipeline 行为测试。"""

    @staticmethod
    def _make_state(name: str = "test_req") -> dict:
        return {
            "requirement": "test requirement",
            "mode": "dev",
            "metadata": {"requirement_name": name},
            "errors": [],
            "artifacts": [],
        }

    @staticmethod
    def _make_phases(*names: str) -> list[tuple[str, MagicMock]]:
        """创建一组 mock 节点函数，每个返回 state 不变。"""
        phases = []
        for name in names:
            fn = MagicMock(side_effect=lambda s: s, name=f"node_{name}")
            phases.append((name, fn))
        return phases

    def test_pipeline_runs_all_phases(self):
        """所有阶段按顺序执行。"""
        state = self._make_state()
        phases = self._make_phases("req", "design", "sql", "review")

        result = _run_pipeline(state, phases)

        assert result.success
        assert not result.halted
        for _name, fn in phases:
            fn.assert_called_once()

    def test_pipeline_stops_on_halt_error(self):
        """节点抛 WorkflowHaltError 时管道终止。"""
        state = self._make_state()
        phases = self._make_phases("req", "design", "sql", "review")
        # 第二个节点抛终止错误
        phases[1][1].side_effect = WorkflowHaltError("需求不明确")

        result = _run_pipeline(state, phases)

        assert not result.success
        assert result.halted
        # 第一个节点执行了，第二个没有（因为抛异常终止）
        phases[0][1].assert_called_once()
        # 后续节点未执行
        phases[2][1].assert_not_called()

    def test_pipeline_collects_errors(self):
        """非致命异常记录到 errors 但不终止管道。"""
        state = self._make_state()
        phases = self._make_phases("req", "design", "sql")
        phases[1][1].side_effect = ValueError("some error")

        _run_pipeline(state, phases)

        assert len(state["errors"]) == 1
        assert "some error" in state["errors"][0]
        # 第三个节点仍然执行了
        phases[2][1].assert_called_once()

    def test_pipeline_progress_callback(self):
        """on_progress 回调按序触发。"""
        state = self._make_state()
        phases = self._make_phases("a", "b", "c")
        progress_calls: list[str] = []

        def on_progress(name: str, idx: int, total: int, _state) -> None:
            progress_calls.append(name)

        _run_pipeline(state, phases, on_progress=on_progress)

        assert progress_calls == ["a", "b", "c"]

    def test_pipeline_interactive_confirm_continue(self):
        """interactive=True 且 confirm 返回 True 时继续执行。"""
        state = self._make_state()
        phases = self._make_phases("req", "sql")

        _run_pipeline(
            state,
            phases,
            interactive=True,
            confirm_after="req",
            on_confirm=lambda _s: True,
        )

        phases[0][1].assert_called_once()
        phases[1][1].assert_called_once()

    def test_pipeline_interactive_confirm_stop(self):
        """interactive=True 且 confirm 返回 False 时停止。"""
        state = self._make_state()
        phases = self._make_phases("req", "sql")

        result = _run_pipeline(
            state,
            phases,
            interactive=True,
            confirm_after="req",
            on_confirm=lambda _s: False,
        )

        phases[0][1].assert_called_once()
        # 第二个节点未执行
        phases[1][1].assert_not_called()
        assert result.halted

    def test_pipeline_halt_error_in_errors_list(self):
        """errors 列表中的消息包含终止标记时也终止管道。"""
        state = self._make_state()
        phases = self._make_phases("req", "design")

        def failing_node(s):
            s.setdefault("errors", []).append("[终止] 致命错误")
            return s

        phases[0] = ("req", failing_node)

        result = _run_pipeline(state, phases)

        assert result.halted
        # 第二个节点未执行
        phases[1][1].assert_not_called()


# ============================================================
# _run_fix_loop 测试
# ============================================================


class TestRunFixLoop:
    """_run_fix_loop 修复循环测试。"""

    @staticmethod
    def _make_state_with_issues() -> dict:
        return {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": "SELECT 1 FROM dual",
            "_review_issues": [
                {"severity": "Error", "message": "missing WHERE clause"},
            ],
            "_needs_fix_loop": True,
            "fix_iterations": 0,
        }

    @patch("src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}")
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm", return_value="SELECT 1 FROM dual WHERE 1=1")
    def test_fix_loop_fixes_sql(self, _mock_llm, _mock_extract, _mock_valid, _mock_save):
        """LLM 返回有效修复 SQL 时，state 被更新。"""
        state = self._make_state_with_issues()

        with patch(
            "src.aqueduct.config.settings.get_settings"
        ) as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert result["sql_content"] == "SELECT 1 FROM dual WHERE 1=1"
        assert result["fix_iterations"] == 1
        assert result.get("_needs_fix_loop") is False

    @patch("src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}")
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm", return_value="SELECT 1 FROM dual WHERE 1=1")
    def test_fix_loop_max_iterations(self, _mock_llm, _mock_extract, _mock_valid, _mock_save):
        """达到最大迭代次数时不再修复。"""
        state = self._make_state_with_issues()
        state["fix_iterations"] = 2

        with patch(
            "src.aqueduct.config.settings.get_settings"
        ) as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        # 没有修改 SQL
        assert result["sql_content"] == "SELECT 1 FROM dual"

    @patch("src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}")
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=False)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm", return_value="not valid sql at all")
    def test_fix_loop_invalid_llm_output(self, _mock_llm, _mock_extract, _mock_valid, _mock_save):
        """LLM 返回无效 SQL 时保留原 SQL。"""
        state = self._make_state_with_issues()
        original_sql = state["sql_content"]

        with patch(
            "src.aqueduct.config.settings.get_settings"
        ) as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert result["sql_content"] == original_sql
