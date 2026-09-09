"""P1-3 断点续跑（PERF-6）测试 — manifest 逐 Phase checkpoint + resume 跳过已完成前缀。

背景：v5 灾难场景——Phase 5 失败时前 4 个 Phase 的 108 分钟产出随进程一起丢失，
重跑只能从零开始。断点续跑：每个 Phase 成功完成后把可序列化 state 快照写进
.pipeline_manifest.json；重跑时 resume=True 跳过已完成前缀，从断点继续。

与 OPT-7 的分工：
- OPT-7（已有）：需求未变 → 自动跳过 Phase 1-3 的 LLM 调用（隐式，无 flag）
- P1-3（本模块）：管道中断/失败后 → 显式 resume=True 从断点继续（Phase 粒度）
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.aqueduct.core import Aqueduct, _completed_prefix_len, _run_pipeline
from src.aqueduct.exceptions import WorkflowHaltError
from src.aqueduct.utils.change_analyzer import MANIFEST_FILENAME, ChangeAnalyzer

# ============================================================
# snapshot_state / restore_state — 快照序列化
# ============================================================


class TestSnapshotState:
    """state 快照：排除不可序列化的运行时对象，保留其余全部字段。"""

    def test_excludes_runtime_objects(self):
        """缓存/线程池/Future/ModelRouter 等运行时对象不进快照（进程退出后无意义）。

        _llm_router 回归来源：call_llm 首调即写入 state，ModelRouter 不可
        JSON 序列化——2026-09-09 eval 实证：真跑 manifest 无 state_snapshot，
        每阶段 checkpoint 保存全部静默失败，断点续跑形同虚设。
        """
        state = {
            "requirement": "需求",
            "sql_content": "SELECT 1",
            "_table_schema_cache": object(),
            "_lineage_future": object(),
            "_lineage_executor": object(),
            "_dqc_spec_future": object(),
            "_dqc_spec_executor": object(),
            "_kn_spec_future": object(),
            "_kn_spec_executor": object(),
            "_llm_router": object(),
        }
        snap = ChangeAnalyzer.snapshot_state(state)
        for key in (
            "_table_schema_cache",
            "_lineage_future",
            "_lineage_executor",
            "_dqc_spec_future",
            "_dqc_spec_executor",
            "_kn_spec_future",
            "_kn_spec_executor",
            "_llm_router",
        ):
            assert key not in snap
        assert snap["sql_content"] == "SELECT 1"

    def test_keeps_plain_fields_including_private_states(self):
        """普通字段全保留——含修复循环等 _ 前缀的可序列化状态。"""
        state = {
            "requirement": "需求",
            "mode": "dev",
            "errors": ["design: 降级警告"],
            "artifacts": ["output/x/Phase4-x.sql"],
            "metadata": {"requirement_name": "x", "design_done": "true"},
            "sql_content": "SELECT 1",
            "fix_iterations": 2,
            "table_schemas": {"dw.t": {"columns": ["a"]}},
            "trial_run_result": {"errors": [], "tested": 3, "passed": 3},
            "_review_issues": [{"severity": "Critical", "message": "问题"}],
            "_needs_fix_loop": False,
            "_dqc_spec_input_hash": 12345,
        }
        snap = ChangeAnalyzer.snapshot_state(state)
        assert snap == state

    def test_snapshot_is_json_serializable(self):
        """快照必须可整体 JSON 序列化（含中文 ensure_ascii=False 路径）。"""
        state = {
            "requirement": "中文需求",
            "metadata": {"requirement_name": "订单"},
            "_table_schema_cache": object(),
        }
        snap = ChangeAnalyzer.snapshot_state(state)
        json.dumps(snap, ensure_ascii=False)


class TestRestoreState:
    """快照恢复：字段写回 state，当前运行的 metadata 键优先（不倒退）。"""

    def test_restores_snapshot_fields(self):
        state = {
            "requirement": "r",
            "mode": "dev",
            "errors": [],
            "artifacts": [],
            "metadata": {"requirement_name": "n"},
        }
        snapshot = {
            "sql_content": "SELECT 1",
            "fix_iterations": 1,
            "errors": ["design: 降级警告"],
            "metadata": {"design_done": "true", "ddl_done": "true"},
        }
        ChangeAnalyzer.restore_state(state, snapshot)
        assert state["sql_content"] == "SELECT 1"
        assert state["fix_iterations"] == 1
        assert state["errors"] == ["design: 降级警告"]
        # 快照 metadata 与当前 metadata 合并，而非整体覆盖
        assert state["metadata"]["requirement_name"] == "n"
        assert state["metadata"]["design_done"] == "true"

    def test_current_metadata_wins_on_conflict(self):
        """resume 到不同 output_dir 时，当前运行的 requirement_name/output_dir 不被旧值覆盖。"""
        state = {
            "requirement": "r",
            "mode": "dev",
            "errors": [],
            "artifacts": [],
            "metadata": {"requirement_name": "new", "output_dir": "new_dir"},
        }
        snapshot = {
            "metadata": {"requirement_name": "old", "output_dir": "old_dir", "design_done": "true"},
        }
        ChangeAnalyzer.restore_state(state, snapshot)
        assert state["metadata"]["requirement_name"] == "new"
        assert state["metadata"]["output_dir"] == "new_dir"
        # 非冲突键照常恢复
        assert state["metadata"]["design_done"] == "true"


# ============================================================
# save_checkpoint / load_checkpoint — manifest 持久化
# ============================================================


class TestSaveLoadCheckpoint:
    """checkpoint 读写往返与边界条件。"""

    @staticmethod
    def _state() -> dict:
        return {
            "requirement": "测试需求",
            "mode": "dev",
            "errors": [],
            "artifacts": ["output/r/Phase4-r.sql"],
            "metadata": {"requirement_name": "r"},
            "requirement_summary": "摘要",
            "design_scheme": "方案",
            "ddl_content": "CREATE TABLE t;",
            "sql_content": "SELECT 1",
        }

    def test_roundtrip(self, tmp_path):
        """保存后按相同需求加载，往返一致。"""
        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        analyzer.save_checkpoint(
            "测试需求",
            ["requirement", "design", "ddl", "sql"],
            self._state(),
        )

        loaded = ChangeAnalyzer(output_dir=tmp_path).load_checkpoint("测试需求")
        assert loaded is not None
        assert loaded["phases_completed"] == ["requirement", "design", "ddl", "sql"]
        assert loaded["state_snapshot"]["sql_content"] == "SELECT 1"
        assert loaded["state_snapshot"]["design_scheme"] == "方案"

    def test_hash_mismatch_returns_none(self, tmp_path):
        """需求已变更 → checkpoint 不适用，返回 None。"""
        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        analyzer.save_checkpoint("需求A", ["requirement"], self._state())

        assert ChangeAnalyzer(output_dir=tmp_path).load_checkpoint("需求B") is None

    def test_no_manifest_returns_none(self, tmp_path):
        """无 manifest → None。"""
        assert ChangeAnalyzer(output_dir=tmp_path).load_checkpoint("需求") is None

    def test_legacy_manifest_without_checkpoint_returns_none(self, tmp_path):
        """v0.6 旧版 manifest（仅 phase1_outputs，无 checkpoint）→ None，不炸。"""
        manifest = {
            "requirement_hash": ChangeAnalyzer.compute_requirement_hash("测试需求"),
            "updated_at": "2026-09-07T12:00:00",
            "phase1_outputs": {"requirement_summary": "摘要"},
        }
        (tmp_path / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

        assert ChangeAnalyzer(output_dir=tmp_path).load_checkpoint("测试需求") is None

    def test_corrupt_manifest_returns_none(self, tmp_path):
        """损坏的 JSON → None，不抛异常。"""
        (tmp_path / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")

        assert ChangeAnalyzer(output_dir=tmp_path).load_checkpoint("需求") is None

    def test_save_checkpoint_keeps_phase1_outputs_for_opt7(self, tmp_path):
        """checkpoint 写入后 phase1_outputs 同步派生，OPT-7 增量跳过不失效。"""
        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        analyzer.save_checkpoint("测试需求", ["requirement", "design", "ddl"], self._state())

        raw = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert raw["phase1_outputs"]["requirement_summary"] == "摘要"
        assert raw["phase1_outputs"]["design_scheme"] == "方案"
        assert raw["phase1_outputs"]["ddl_content"] == "CREATE TABLE t;"
        # OPT-7 判定应可用
        assert ChangeAnalyzer(output_dir=tmp_path).should_skip_phase1("测试需求")


# ============================================================
# _completed_prefix_len — 已完成前缀计算
# ============================================================


class TestCompletedPrefixLen:
    """phases_completed 只取与当前 Phase 序列一致的真前缀。"""

    def test_full_match(self):
        assert _completed_prefix_len(["a", "b", "c"], ["a", "b", "c", "d"]) == 3

    def test_empty_completed(self):
        assert _completed_prefix_len([], ["a", "b"]) == 0

    def test_corrupt_start(self):
        """首项就对不上（损坏/异构 manifest）→ 0，全量重跑。"""
        assert _completed_prefix_len(["sql", "review"], ["requirement", "design"]) == 0

    def test_unknown_tail_truncated(self):
        """尾部混入未知 Phase 名 → 截断到已知前缀。"""
        assert _completed_prefix_len(["a", "b", "zzz"], ["a", "b", "c"]) == 2


# ============================================================
# _run_pipeline on_phase_complete — checkpoint 触发时机
# ============================================================


class TestPipelineCheckpointHook:
    """on_phase_complete 在 Phase 真正完成后触发；失败/回环中的 Phase 不触发。"""

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
        phases = []
        for name in names:
            fn = MagicMock(side_effect=lambda s: s, name=f"node_{name}")
            phases.append((name, fn))
        return phases

    def test_called_after_each_completed_phase(self):
        state = self._make_state()
        phases = self._make_phases("a", "b", "c")
        calls: list[str] = []

        result = _run_pipeline(state, phases, on_phase_complete=lambda n, s: calls.append(n))

        assert result.success
        assert calls == ["a", "b", "c"]

    def test_halted_phase_not_checkpointed(self):
        """抛 WorkflowHaltError 的 Phase 及其后的 Phase 不触发 checkpoint。"""
        state = self._make_state()
        phases = self._make_phases("a", "b", "c")
        phases[1][1].side_effect = WorkflowHaltError("失败")
        calls: list[str] = []

        result = _run_pipeline(state, phases, on_phase_complete=lambda n, s: calls.append(n))

        assert result.halted
        assert calls == ["a"]

    def test_halt_marker_phase_not_checkpointed(self):
        """errors 尾项带终止标记的 Phase（halt-marker 路径）不触发 checkpoint。"""
        state = self._make_state()

        def failing_node(s):
            s.setdefault("errors", []).append("[终止] 致命错误")
            return s

        calls: list[str] = []
        phases: list[tuple[str, object]] = [
            ("a", failing_node),
            ("b", MagicMock(side_effect=lambda s: s)),
        ]

        result = _run_pipeline(state, phases, on_phase_complete=lambda n, s: calls.append(n))

        assert result.halted
        assert calls == []

    def test_fix_loop_review_checkpointed_once(self, monkeypatch):
        """审查→修复回环：review 重审后只 checkpoint 一次（回环中间不落盘）。"""
        state = self._make_state()

        def review_node(s):
            # 仅初审（第一次执行）要求修复回环；复审（fake_fix_loop 清标志后）放行
            if not s.get("_review_first_pass_done"):
                s["_review_first_pass_done"] = True
                s["_needs_fix_loop"] = True
            return s

        def fake_fix_loop(s):
            s["_needs_fix_loop"] = False
            return s

        monkeypatch.setattr("src.aqueduct.core._run_fix_loop", fake_fix_loop)

        calls: list[str] = []
        phases: list[tuple[str, object]] = [
            ("a", MagicMock(side_effect=lambda s: s)),
            ("review", review_node),
            ("c", MagicMock(side_effect=lambda s: s)),
        ]

        result = _run_pipeline(state, phases, on_phase_complete=lambda n, s: calls.append(n))

        assert result.success
        # review 执行了 2 次（初审+复审），checkpoint 只落 1 次
        assert calls == ["a", "review", "c"]

    def test_confirm_stop_still_checkpoints_phase(self):
        """交互确认拒绝停止时，已完成的 Phase 仍写入 checkpoint（下次可续跑）。"""
        state = self._make_state()
        phases = self._make_phases("req", "sql")
        calls: list[str] = []

        result = _run_pipeline(
            state,
            phases,
            interactive=True,
            confirm_after="req",
            on_confirm=lambda _s: False,
            on_phase_complete=lambda n, s: calls.append(n),
        )

        assert result.halted
        assert calls == ["req"]

    def test_hook_exception_does_not_kill_pipeline(self):
        """checkpoint 写入异常（如磁盘满）只告警，不阻塞管道。"""
        state = self._make_state()
        phases = self._make_phases("a", "b")

        def bad_hook(_name, _state):
            raise OSError("disk full")

        result = _run_pipeline(state, phases, on_phase_complete=bad_hook)

        assert result.success
        for _name, fn in phases:
            fn.assert_called_once()


# ============================================================
# Aqueduct.dev(resume=True) — 断点续跑端到端（假 Phase，无 LLM）
# ============================================================

_FAKE_PHASE_NAMES = ["requirement", "design", "ddl", "sql", "review", "dqc", "report"]


class TestDevResume:
    """dev 管道断点续跑集成行为。"""

    @staticmethod
    def _make_setup(tmp_path, monkeypatch, halt_at=None):
        """构建假 Phase 管道 + 需求文件，返回 (req_path, executed, behavior)。"""
        req = tmp_path / "测试需求.md"
        req.write_text("# 测试需求\n\n订单日统计", encoding="utf-8")

        executed: list[str] = []
        behavior = {"halt_at": halt_at, "design_error": False}

        def make_node(name):
            def _node(state):
                executed.append(name)
                state[f"{name}_done"] = True
                if name == "design" and behavior["design_error"]:
                    state.setdefault("errors", []).append("design: 降级警告，继续")
                if name == behavior["halt_at"]:
                    raise WorkflowHaltError(f"{name} 失败")
                return state

            return _node

        fake_phases = [(n, make_node(n)) for n in _FAKE_PHASE_NAMES]
        monkeypatch.setattr("src.aqueduct.core._DEV_PHASES", fake_phases)
        return req, executed, behavior

    def test_checkpoint_written_after_full_run(self, tmp_path, monkeypatch):
        """全量成功运行后 manifest 记录全部 Phase 与 state 快照。"""
        req, _, _ = self._make_setup(tmp_path, monkeypatch)
        out = tmp_path / "out"

        Aqueduct().dev(str(req), output_dir=str(out))

        manifest = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert manifest["phases_completed"] == _FAKE_PHASE_NAMES
        assert manifest["state_snapshot"]["sql_done"] is True
        assert manifest["state_snapshot"]["report_done"] is True

    def test_resume_skips_completed_phases(self, tmp_path, monkeypatch):
        """中断后续跑：已完成 Phase 不再执行，从断点继续且 state 恢复。"""
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch, halt_at="review")
        out = tmp_path / "out"

        r1 = Aqueduct().dev(str(req), output_dir=str(out))
        assert r1.halted
        assert executed == ["requirement", "design", "ddl", "sql", "review"]

        manifest = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert manifest["phases_completed"] == ["requirement", "design", "ddl", "sql"]

        executed.clear()
        behavior["halt_at"] = None
        r2 = Aqueduct().dev(str(req), output_dir=str(out), resume=True)

        assert executed == ["review", "dqc", "report"]
        assert r2.success
        # sql 阶段第一轮写的字段，经快照恢复后仍可见
        assert r2.state["sql_done"] is True
        assert r2.state["report_done"] is True

    def test_resume_without_manifest_runs_all(self, tmp_path, monkeypatch):
        """resume=True 但无 checkpoint → 全量运行（优雅降级）。"""
        req, executed, _ = self._make_setup(tmp_path, monkeypatch)
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out), resume=True)

        assert result.success
        assert executed == _FAKE_PHASE_NAMES

    def test_resume_after_requirement_change_runs_all(self, tmp_path, monkeypatch):
        """需求已变更 → hash 不匹配 → resume 退化为全量运行。"""
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch, halt_at="review")
        out = tmp_path / "out"

        Aqueduct().dev(str(req), output_dir=str(out))
        assert len(executed) == 5

        # 需求文档变更
        req.write_text("# 测试需求（v2）\n\n退款统计", encoding="utf-8")
        executed.clear()
        behavior["halt_at"] = None

        result = Aqueduct().dev(str(req), output_dir=str(out), resume=True)

        assert result.success
        assert executed == _FAKE_PHASE_NAMES

    def test_resume_completed_run_is_noop(self, tmp_path, monkeypatch):
        """对已全部完成的管道 resume → 零 Phase 执行，直接返回上次结果。"""
        req, executed, _ = self._make_setup(tmp_path, monkeypatch)
        out = tmp_path / "out"

        Aqueduct().dev(str(req), output_dir=str(out))
        executed.clear()

        r2 = Aqueduct().dev(str(req), output_dir=str(out), resume=True)

        assert executed == []
        assert r2.success
        assert r2.state["report_done"] is True
        assert r2.state["artifacts"] == []

    def test_resume_restores_errors_from_completed_phases(self, tmp_path, monkeypatch):
        """已完成 Phase 的降级错误经快照恢复——轨迹不丢失（评估口径依赖）。"""
        req, _executed, behavior = self._make_setup(tmp_path, monkeypatch, halt_at="dqc")
        behavior["design_error"] = True
        out = tmp_path / "out"

        r1 = Aqueduct().dev(str(req), output_dir=str(out))
        assert r1.halted

        behavior["halt_at"] = None
        r2 = Aqueduct().dev(str(req), output_dir=str(out), resume=True)

        assert r2.success is False  # 快照恢复的历史 errors 仍在
        assert "design: 降级警告，继续" in r2.state["errors"]


# ============================================================
# CLI --resume flag
# ============================================================


class TestCliResumeFlag:
    """CLI dev 命令的 --resume 解析与透传。"""

    def test_parser_accepts_resume(self):
        from src.aqueduct.cli.main import create_parser

        args = create_parser().parse_args(["dev", "req.md", "--resume"])
        assert args.resume is True

    def test_resume_defaults_off(self):
        from src.aqueduct.cli.main import create_parser

        args = create_parser().parse_args(["dev", "req.md"])
        assert args.resume is False

    def test_dev_mode_passes_resume_to_agent(self, monkeypatch, tmp_path):
        import importlib

        cli_main = importlib.import_module("src.aqueduct.cli.main")
        from src.aqueduct.cli.main import create_parser

        class _StubResult:
            success = True
            halted = False
            errors: list = []
            artifacts: list = []

        calls: dict = {}

        class _FakeAgent:
            def dev(self, requirement, **kwargs):
                calls["requirement"] = requirement
                calls.update(kwargs)
                return _StubResult()

        monkeypatch.setattr(cli_main, "Aqueduct", _FakeAgent)
        req_file = tmp_path / "需求.md"
        req_file.write_text("# 需求", encoding="utf-8")
        args = create_parser().parse_args(["dev", str(req_file), "--resume"])

        rc = cli_main._dev_mode(args)

        assert rc == 0
        assert calls["requirement"] == str(req_file)
        assert calls["resume"] is True
