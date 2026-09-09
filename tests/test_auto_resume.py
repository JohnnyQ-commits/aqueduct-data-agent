"""失败自动断点续跑测试（auto-resume）。

背景：网关停摆窗（fixbatch-check 实录：sql_gen 三连 900s 超时降级）让整跑
失败收尾，用户必须手动 --resume。环境性失败具备"重跑即愈"特征——P1-3 降级
冻结已保证 checkpoint 前缀停在最后一个干净 Phase，自动带 resume 重启只重跑
降级部分（~12min vs 全量 ~45min）。

防确定性缺陷空转：新尝试必须让干净前缀增长（phases_completed 变长），
无进展（同一 Phase 再降级）立即停止——真缺陷重跑 N 次结果相同，多烧的
只有一次失败 Phase 的成本，不会无限循环。
不覆盖：halt（用户终止/致命错误，尊重停机意图）；首 Phase 降级（无前缀
可复用，自动重试=全量盲试，交还用户决策）。
"""

from __future__ import annotations

import json

import pytest

from src.aqueduct.config.settings import get_settings
from src.aqueduct.core import Aqueduct
from src.aqueduct.exceptions import WorkflowHaltError
from src.aqueduct.utils.change_analyzer import MANIFEST_FILENAME

_FAKE_PHASE_NAMES = ["requirement", "design", "ddl", "sql", "review", "dqc", "report"]


@pytest.fixture
def fresh_settings():
    """每个测试前后清空 settings 缓存，确保环境变量修改生效。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================
# Settings — AQUEDUCT_AUTO_RESUME_ATTEMPTS
# ============================================================


class TestAutoResumeSetting:
    """自动续跑次数配置。"""

    def test_default_is_one(self, fresh_settings, monkeypatch):
        """默认 1：一次自动续跑——停摆窗内二次失败即停，有界成本换自愈。"""
        monkeypatch.delenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", raising=False)
        assert get_settings().auto_resume_attempts == 1

    def test_env_override(self, fresh_settings, monkeypatch):
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "3")
        assert get_settings().auto_resume_attempts == 3

    def test_zero_disables(self, fresh_settings, monkeypatch):
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "0")
        assert get_settings().auto_resume_attempts == 0


# ============================================================
# dev() 自动续跑行为 — 假 Phase，无 LLM
# ============================================================


class TestAutoResume:
    """dev 管道失败后自动 resume 重启。"""

    @staticmethod
    def _make_setup(tmp_path, monkeypatch):
        """假 Phase 管道 + 可控行为开关。

        behavior["degrade_n"][name] = k → name 的前 k 次执行降级（append errors）
        behavior["halt"][name] = True → name 每次执行抛 WorkflowHaltError
        """
        req = tmp_path / "测试需求.md"
        req.write_text("# 测试需求\n\n订单日统计", encoding="utf-8")

        executed: list[str] = []
        counts: dict[str, int] = {}
        behavior: dict = {"degrade_n": {}, "halt": {}}

        def make_node(name):
            def _node(state):
                executed.append(name)
                counts[name] = counts.get(name, 0) + 1
                if counts[name] <= behavior["degrade_n"].get(name, 0):
                    state.setdefault("errors", []).append(f"{name}: 网关超时降级")
                if behavior["halt"].get(name):
                    raise WorkflowHaltError(f"{name} 失败")
                return state

            return _node

        fake_phases = [(n, make_node(n)) for n in _FAKE_PHASE_NAMES]
        monkeypatch.setattr("src.aqueduct.core._DEV_PHASES", fake_phases)
        return req, executed, behavior

    def test_failed_run_auto_resumes_and_succeeds(self, tmp_path, monkeypatch, fresh_settings):
        """sql 首次降级 → 自动 resume 跳过干净前缀只重跑 sql 起，二次成功。"""
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "1")
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch)
        behavior["degrade_n"] = {"sql": 1}
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out))

        assert result.success
        # 干净前缀只执行一次（resume 跳过）；降级点起各执行两次
        assert executed.count("requirement") == 1
        assert executed.count("design") == 1
        assert executed.count("ddl") == 1
        assert executed.count("sql") == 2
        assert executed.count("review") == 2
        assert executed.count("report") == 2
        # 最终 manifest 记录全部 7 Phase 干净完成
        manifest = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert manifest["phases_completed"] == _FAKE_PHASE_NAMES

    def test_no_progress_stops_retrying(self, tmp_path, monkeypatch, fresh_settings):
        """确定性降级（sql 每次都失败）→ 第二次尝试前缀不增长即停，预算剩余也不烧。"""
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "2")
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch)
        behavior["degrade_n"] = {"sql": 99}
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out))

        assert result.success is False
        assert executed.count("requirement") == 1
        assert executed.count("sql") == 2  # 两次尝试各一次，无进展即停
        assert executed.count("report") == 2

    def test_zero_attempts_disables(self, tmp_path, monkeypatch, fresh_settings):
        """attempts=0 → 失败也不自动续跑（旧行为，锚定开关有效性）。"""
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "0")
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch)
        behavior["degrade_n"] = {"sql": 1}
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out))

        assert result.success is False
        assert executed.count("sql") == 1

    def test_halted_run_not_resumed(self, tmp_path, monkeypatch, fresh_settings):
        """halt（用户终止/致命错误）不被自动续跑推翻——尊重停机意图。"""
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "1")
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch)
        behavior["halt"] = {"sql": True}
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out))

        assert result.halted
        assert executed.count("sql") == 1
        assert "review" not in executed  # halt 中断后续 Phase

    def test_first_phase_failure_no_retry(self, tmp_path, monkeypatch, fresh_settings):
        """首 Phase 降级 → 无 checkpoint 前缀可复用 → 不自动重试（全量盲试成本不可接受）。"""
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "1")
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch)
        behavior["degrade_n"] = {"requirement": 1}
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out))

        assert result.success is False
        assert executed.count("requirement") == 1
        assert executed.count("sql") == 1

    def test_success_run_single_attempt(self, tmp_path, monkeypatch, fresh_settings):
        """干净成功跑不触发任何自动续跑。"""
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "1")
        req, executed, _ = self._make_setup(tmp_path, monkeypatch)
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out))

        assert result.success
        assert executed.count("requirement") == 1
        assert executed.count("report") == 1

    def test_chained_resume_progresses_prefix(self, tmp_path, monkeypatch, fresh_settings):
        """逐轮推进直至成功：sql 二次干净、review 第三次干净——前缀逐轮增长。"""
        monkeypatch.setenv("AQUEDUCT_AUTO_RESUME_ATTEMPTS", "2")
        req, executed, behavior = self._make_setup(tmp_path, monkeypatch)
        behavior["degrade_n"] = {"sql": 1, "review": 2}
        out = tmp_path / "out"

        result = Aqueduct().dev(str(req), output_dir=str(out))

        assert result.success
        # 尝试 1 全量（sql 降级）；尝试 2 从 sql（sql 干净、review 降级）；尝试 3 从 review
        assert executed.count("requirement") == 1
        assert executed.count("sql") == 2
        assert executed.count("review") == 3
        assert executed.count("dqc") == 3
        assert executed.count("report") == 3
