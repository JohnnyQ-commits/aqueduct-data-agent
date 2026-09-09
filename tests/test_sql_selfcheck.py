"""TODO-8 Phase 4 生成自检循环测试。

linter Critical 留到审查侧的成本链：review(~13min) 发现 → sql_fix →
re-review(~13min)——一次本地零 token 可判定的违规要付两轮审查。生成端
在 _auto_validate 后就地自修正（一次 sql_fix），试跑/血缘/成本与审查
都基于修复后的 SQL。

守护：仅 ERROR 级触发（与 review 侧 ERROR→Critical 映射同口径）；
修复必须让 ERROR 数严格下降才接受，否则回退原 SQL；轮数上限
AQUEDUCT_SQL_SELF_FIX_ROUNDS（默认 1，0=关闭）；修不动保持原状——
审查侧 P1-2 现场复检门禁照常兜底，语义不变。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.aqueduct.config.settings import get_settings
from src.aqueduct.engine.nodes.sql import _self_check_fix, node_sql


@pytest.fixture
def fresh_settings():
    """每个测试前后清空 settings 缓存，确保环境变量修改生效。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _vr(*messages: str) -> dict:
    """构造 validation_result：每条 message 一个 ERROR issue。"""
    return {
        "issues": [
            {"level": "ERROR", "message": m, "line": 10 + i} for i, m in enumerate(messages)
        ],
    }


class TestSelfCheckSetting:
    """sql_self_fix_rounds 配置。"""

    def test_default_is_one(self, fresh_settings, monkeypatch):
        monkeypatch.delenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", raising=False)
        assert get_settings().sql_self_fix_rounds == 1

    def test_env_override(self, fresh_settings, monkeypatch):
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "2")
        assert get_settings().sql_self_fix_rounds == 2

    def test_zero_disables(self, fresh_settings, monkeypatch):
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "0")
        assert get_settings().sql_self_fix_rounds == 0


class TestSelfCheckFix:
    """_self_check_fix：linter ERROR 就地自修正。"""

    @staticmethod
    def _make_state(tmp_path: Path, vr: dict) -> dict:
        canonical = tmp_path / "Phase4-test.sql"
        canonical.write_text("select 1 as original", encoding="utf-8")
        return {
            "requirement": "需求",
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
            "sql_content": "select 1 as original",
            "sql_file": "output/test/Phase4-test.sql",
            "validation_result": vr,
        }

    @staticmethod
    def _patches(tmp_path: Path, validate_queue: list[dict]):
        """返回 patch 上下文集合：可控复检 + 规范路径解析 + 审计副本记录。"""
        saved: list[str] = []
        validate_calls: list[str] = []

        def fake_validate(st, _path):
            validate_calls.append("validate")
            if validate_queue:
                st["validation_result"] = validate_queue.pop(0)

        return saved, validate_calls, fake_validate

    def test_no_error_no_fix(self, tmp_path, fresh_settings, monkeypatch):
        """无 ERROR（仅 WARN/无 issue）→ 不触发修复。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "1")
        state = self._make_state(
            tmp_path, {"issues": [{"level": "WARN", "message": "w", "line": 1}]}
        )
        saved, _, fake_validate = self._patches(tmp_path, [])

        with (
            patch("src.aqueduct.engine.nodes.sql.call_llm") as llm,
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
        ):
            _self_check_fix(state)

        llm.assert_not_called()
        assert state["sql_content"] == "select 1 as original"
        assert saved == []

    def test_error_triggers_fix_accepted(self, tmp_path, fresh_settings, monkeypatch):
        """1 个 ERROR → sql_fix 修复 → 复检清零 → 接受（state+规范文件+审计副本）。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "1")
        state = self._make_state(tmp_path, _vr("除法未判零"))
        saved, validate_calls, fake_validate = self._patches(tmp_path, [{"issues": []}])
        canonical = tmp_path / "Phase4-test.sql"

        with (
            patch(
                "src.aqueduct.engine.nodes.sql.call_llm", return_value="```sql\nselect 1 fixed\n```"
            ) as llm,
            patch("src.aqueduct.engine.nodes.sql.extract_sql_block", return_value="select 1 fixed"),
            patch("src.aqueduct.engine.nodes.sql.is_valid_sql", return_value=True),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
            patch("src.aqueduct.engine.nodes.sql._resolve_sql_path", return_value=canonical),
        ):
            _self_check_fix(state)

        assert llm.call_count == 1
        # prompt 携带违规信息与原 SQL
        prompt = llm.call_args[0][2]
        assert "除法未判零" in prompt
        assert "select 1 as original" in prompt
        # 接受：state 与规范文件更新，审计副本落盘
        assert state["sql_content"] == "select 1 fixed"
        assert canonical.read_text(encoding="utf-8") == "select 1 fixed"
        assert saved == ["Phase4-test_selffix1.sql"]
        # 修复后复检一次
        assert validate_calls == ["validate"]

    def test_fix_rejected_when_not_improved(self, tmp_path, fresh_settings, monkeypatch):
        """修复后 ERROR 未降反升 → 回退原 SQL，规范文件与复检结果还原。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "1")
        state = self._make_state(tmp_path, _vr("e1", "e2"))
        # 复检队列：修复后更差（3 个）→ 回退还原（原 2 个）
        saved, validate_calls, fake_validate = self._patches(
            tmp_path, [_vr("e1", "e2", "e3"), _vr("e1", "e2")]
        )
        canonical = tmp_path / "Phase4-test.sql"

        with (
            patch("src.aqueduct.engine.nodes.sql.call_llm", return_value="```sql\nbad\n```"),
            patch("src.aqueduct.engine.nodes.sql.extract_sql_block", return_value="select bad"),
            patch("src.aqueduct.engine.nodes.sql.is_valid_sql", return_value=True),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
            patch("src.aqueduct.engine.nodes.sql._resolve_sql_path", return_value=canonical),
        ):
            _self_check_fix(state)

        assert state["sql_content"] == "select 1 as original"
        assert canonical.read_text(encoding="utf-8") == "select 1 as original"
        # 复检两次：修复后一次 + 回退还原一次
        assert len(validate_calls) == 2

    def test_llm_failure_keeps_original(self, tmp_path, fresh_settings, monkeypatch):
        """sql_fix LLM 调用失败 → 吞异常保持原 SQL（自检降级不炸管道）。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "1")
        state = self._make_state(tmp_path, _vr("e1"))
        saved, validate_calls, fake_validate = self._patches(tmp_path, [])
        canonical = tmp_path / "Phase4-test.sql"

        with (
            patch(
                "src.aqueduct.engine.nodes.sql.call_llm",
                side_effect=RuntimeError("LLM 超时"),
            ),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
            patch("src.aqueduct.engine.nodes.sql._resolve_sql_path", return_value=canonical),
        ):
            _self_check_fix(state)  # 不抛

        assert state["sql_content"] == "select 1 as original"
        assert saved == []
        assert validate_calls == []  # LLM 失败不触发复检

    def test_invalid_fix_output_keeps_original(self, tmp_path, fresh_settings, monkeypatch):
        """修复输出非有效 SQL → 保持原 SQL，不写盘不复检。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "1")
        state = self._make_state(tmp_path, _vr("e1"))
        saved, validate_calls, fake_validate = self._patches(tmp_path, [])
        canonical = tmp_path / "Phase4-test.sql"

        with (
            patch("src.aqueduct.engine.nodes.sql.call_llm", return_value="垃圾输出"),
            patch("src.aqueduct.engine.nodes.sql.extract_sql_block", return_value="垃圾输出"),
            patch("src.aqueduct.engine.nodes.sql.is_valid_sql", return_value=False),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
            patch("src.aqueduct.engine.nodes.sql._resolve_sql_path", return_value=canonical),
        ):
            _self_check_fix(state)

        assert state["sql_content"] == "select 1 as original"
        assert canonical.read_text(encoding="utf-8") == "select 1 as original"
        assert saved == []
        assert validate_calls == []

    def test_rounds_zero_disables(self, tmp_path, fresh_settings, monkeypatch):
        """rounds=0 → 有 ERROR 也不触发（开关锚定）。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "0")
        state = self._make_state(tmp_path, _vr("e1"))
        saved, _, fake_validate = self._patches(tmp_path, [])

        with (
            patch("src.aqueduct.engine.nodes.sql.call_llm") as llm,
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
        ):
            _self_check_fix(state)

        llm.assert_not_called()
        assert saved == []

    def test_two_rounds_progress_to_clean(self, tmp_path, fresh_settings, monkeypatch):
        """rounds=2：2 ERROR → 修复一轮降 1 → 再修复清零，两份审计副本。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "2")
        state = self._make_state(tmp_path, _vr("e1", "e2"))
        saved, validate_calls, fake_validate = self._patches(tmp_path, [_vr("e2"), {"issues": []}])
        canonical = tmp_path / "Phase4-test.sql"
        fixed_outputs = iter(["select fixed1", "select fixed2"])

        with (
            patch("src.aqueduct.engine.nodes.sql.call_llm", return_value="```sql\nx\n```") as llm,
            patch(
                "src.aqueduct.engine.nodes.sql.extract_sql_block",
                side_effect=lambda _r: next(fixed_outputs),
            ),
            patch("src.aqueduct.engine.nodes.sql.is_valid_sql", return_value=True),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
            patch("src.aqueduct.engine.nodes.sql._resolve_sql_path", return_value=canonical),
        ):
            _self_check_fix(state)

        assert llm.call_count == 2
        assert state["sql_content"] == "select fixed2"
        assert canonical.read_text(encoding="utf-8") == "select fixed2"
        assert saved == ["Phase4-test_selffix1.sql", "Phase4-test_selffix2.sql"]
        assert validate_calls == ["validate", "validate"]  # 每轮接受后复检一次


class TestNodeSqlWiring:
    """node_sql 接线：自检在 validate 之后、试跑/血缘/成本之前（基于修复后 SQL）。"""

    def test_self_check_order(self, tmp_path):
        order: list[str] = []

        state = {
            "requirement": "需求文档" * 20,
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
            "sql_content": "",
            "sql_file": "output/test/Phase4-test.sql",
        }

        skill = MagicMock()
        skill.execute.return_value = MagicMock(success=True, data={"prompt": "p"})

        def mark(name):
            return MagicMock(side_effect=lambda *a, **k: order.append(name) or None)

        with (
            patch("src.aqueduct.engine.nodes.sql.get_skill", return_value=skill),
            patch("src.aqueduct.engine.nodes.sql.call_llm", return_value="```sql\nselect 1\n```"),
            patch("src.aqueduct.engine.nodes.sql.extract_sql_block", return_value="select 1"),
            patch("src.aqueduct.engine.nodes.sql.is_valid_sql", return_value=True),
            patch("src.aqueduct.engine.nodes.sql.save_artifact", return_value="output/test/x.sql"),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", mark("validate")),
            patch("src.aqueduct.engine.nodes.sql._self_check_fix", mark("self_check")),
            patch("src.aqueduct.engine.nodes.sql._auto_trial_run", mark("trial")),
            patch("src.aqueduct.engine.nodes.sql._auto_lineage_async", mark("lineage")),
            patch("src.aqueduct.engine.nodes.sql._auto_cost_estimate", mark("cost")),
        ):
            node_sql(state)

        assert order == ["validate", "self_check", "trial", "lineage", "cost"]
