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

    def test_patch_response_applied_to_original(self, tmp_path, fresh_settings, monkeypatch):
        """run 13 实录（第十一刀 11a）：自检轮 LLM 遵模板输出补丁块 →
        逐块应用到原 SQL（旧 extract 路径判"输出无效"，自检层报废）。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "1")
        state = self._make_state(tmp_path, _vr("除法未判零"))
        saved, validate_calls, fake_validate = self._patches(tmp_path, [{"issues": []}])
        canonical = tmp_path / "Phase4-test.sql"

        patch_response = (
            "修复除法未判零：\n"
            "<<<<<<< SEARCH\n"
            "select 1 as original\n"
            "=======\n"
            "select case when cnt = 0 then null else 1 end as original\n"
            ">>>>>>> REPLACE"
        )
        with (
            patch("src.aqueduct.engine.nodes.sql.call_llm", return_value=patch_response),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
            patch("src.aqueduct.engine.nodes.sql._resolve_sql_path", return_value=canonical),
        ):
            _self_check_fix(state)

        expected = "select case when cnt = 0 then null else 1 end as original"
        assert state["sql_content"] == expected, "补丁块应用到原 SQL，未改动区域逐字节保留"
        assert canonical.read_text(encoding="utf-8") == expected
        assert saved == ["Phase4-test_selffix1.sql"]
        assert validate_calls == ["validate"], "补丁接受后照常复检"

    def test_patch_response_search_not_found_keeps_original(
        self, tmp_path, fresh_settings, monkeypatch
    ):
        """自检轮补丁块 SEARCH 0 次命中 → 保持原 SQL 交审查侧兜底（歧义不猜测）。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "1")
        state = self._make_state(tmp_path, _vr("除法未判零"))
        saved, _, fake_validate = self._patches(tmp_path, [])

        patch_response = (
            "<<<<<<< SEARCH\nselect nonexistent from nowhere\n=======\nselect 1\n>>>>>>> REPLACE"
        )
        with (
            patch("src.aqueduct.engine.nodes.sql.call_llm", return_value=patch_response),
            patch("src.aqueduct.engine.nodes.sql._auto_validate", side_effect=fake_validate),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
        ):
            _self_check_fix(state)

        assert state["sql_content"] == "select 1 as original", "失配保持原 SQL"
        assert saved == [], "不落审计副本（修复未应用）"

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

    def test_failed_round_consumes_budget_and_retries(self, tmp_path, fresh_settings, monkeypatch):
        """run 20 实录（第十五刀靶 B）：修复失败（ERROR 未下降）回退后消耗
        1 轮继续循环——原实现直接 return，1 次失败尝试烧光全部预算
        （AQUEDUCT_SQL_SELF_FIX_ROUNDS=3 的轮 2/3 从未执行）。
        rounds=2：轮 1 修复未改善回退，轮 2 修复生效清零。"""
        monkeypatch.setenv("AQUEDUCT_SQL_SELF_FIX_ROUNDS", "2")
        state = self._make_state(tmp_path, _vr("e1", "e2"))
        # 复检队列：轮1 试接受(3) → 回退还原(2) → 轮2 接受(0)
        saved, validate_calls, fake_validate = self._patches(
            tmp_path, [_vr("e1", "e2", "e3"), _vr("e1", "e2"), {"issues": []}]
        )
        canonical = tmp_path / "Phase4-test.sql"
        fixed_outputs = iter(["select bad1", "select fixed2"])

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

        assert llm.call_count == 2, "失败轮消耗 1 轮预算，轮 2 继续重试"
        assert state["sql_content"] == "select fixed2", "轮 2 修复被接受"
        assert canonical.read_text(encoding="utf-8") == "select fixed2"
        # 轮 1 失败副本（审计存档）+ 轮 2 接受副本
        assert saved == ["Phase4-test_selffix1.sql", "Phase4-test_selffix2.sql"]
        # 复检 3 次：轮1 试接受 + 轮1 回退还原 + 轮2 接受
        assert len(validate_calls) == 3


class TestTrialSelfFix:
    """第十六/十七刀：试跑硬失败（分区超限 / 幻觉表）定向自修。

    第十六刀（run 22 实录）：分区超限自修；第十七刀（run 23 实录）泛化为
    试跑定向自修：(a) 幻觉表（Table not found）同样可自愈——prompt 注入
    table_schemas 真实表清单，强制换表或删逻辑；(b) 接受门禁按错误类别
    分桶（partition/table/other）——分区修好但残留其他类错误=部分改善应
    接受（run 23：全有全无门禁否决了分区已修好的 SQL）；(c) 分区 prompt
    静态点名缺 inc_day 的表引用。修后复跑经分桶门禁且 linter 复检无 ERROR
    才接受；仅一轮，失败保持原状（试跑结果照旧记录）。
    """

    _SQL = (
        "insert overwrite table dw.t partition (inc_day = '${bizdate}')\n"
        "select u.name from ods_rmds.tb_res_user u where u.is_deleted = 0"
    )
    _PARTITION_ERR = (
        "SELECT #1: 任务执行失败: FAILED: Hive Internal Error: "
        "com.sf.bdp.hive.hooks.PartitionOverLimitException"
        "(can only query max 400 partition,but current query partitions is 1455)"
    )
    _FIXED_SQL = (
        "insert overwrite table dw.t partition (inc_day = '${bizdate}')\n"
        "select u.name from ods_rmds.tb_res_user u "
        "where u.is_deleted = 0 and u.inc_day = '20260601'"
    )
    _TABLE_SQL = (
        "insert overwrite table dw.t partition (inc_day = '${bizdate}')\n"
        "select u.name from dw.dw_first_working_detail_tmp20260507 u where u.is_deleted = 0"
    )
    _TABLE_ERR = (
        "FAILED: SemanticException [Error 10001]: "
        "Table not found 'dw_first_working_detail_tmp20260507'"
    )
    _TABLE_FIXED_SQL = (
        "insert overwrite table dw.t partition (inc_day = '${bizdate}')\n"
        "select u.name from ods_rmds.tb_res_user u "
        "where u.is_deleted = 0 and u.inc_day = '20260601'"
    )
    _SQL_MULTI = (
        "insert overwrite table dw.t partition (inc_day = '${bizdate}')\n"
        "select u.name from ods_rmds.tb_res_user u where u.is_deleted = 0"
        ";\n"
        "insert overwrite table dw.t2 partition (inc_day = '${bizdate}')\n"
        "select x.name from dw.dw_first_working_detail_tmp20260507 x where x.is_deleted = 0"
    )
    _PARTIAL_FIXED_SQL = (
        "insert overwrite table dw.t partition (inc_day = '${bizdate}')\n"
        "select u.name from ods_rmds.tb_res_user u "
        "where u.is_deleted = 0 and u.inc_day = '20260601'"
        ";\n"
        "insert overwrite table dw.t2 partition (inc_day = '${bizdate}')\n"
        "select x.name from dw.dw_first_working_detail_tmp20260507 x where x.is_deleted = 0"
    )

    @staticmethod
    def _make_state(tmp_path: Path, sql: str | None = None) -> dict:
        sql = sql if sql is not None else TestTrialSelfFix._SQL
        canonical = tmp_path / "Phase4-test.sql"
        canonical.write_text(sql, encoding="utf-8")
        return {
            "requirement": "需求",
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
            "sql_content": sql,
            "sql_file": "output/test/Phase4-test.sql",
            "table_schemas": {
                "ods_rmds.tb_res_user": "-- 用户表\nname string",
                "dwd.trade_order_di": "-- 交易订单\norder_id string",
            },
            "validation_result": {"issues": []},
        }

    @staticmethod
    def _executor(exec_results: list[bool], error: str):
        return TestTrialSelfFix._executor_seq([(ok, error) for ok in exec_results])

    @staticmethod
    def _executor_seq(results: list[tuple[bool, str]]):
        calls = {"n": 0}

        class _T:
            def execute(self, **kwargs):
                ok, err = results[min(calls["n"], len(results) - 1)]
                calls["n"] += 1
                return type("R", (), {"success": ok, "error": None if ok else err})()

        return _T()

    @staticmethod
    def _run(state, tmp_path, executor, llm_side_effect, validate_queue):
        """跑 _auto_trial_run，返回 (llm_calls, saved, validate_calls)。"""
        canonical = tmp_path / "Phase4-test.sql"
        llm_calls: list[str] = []
        saved: list[str] = []
        validate_calls: list[str] = []

        def fake_llm(_st, task_type, prompt):
            llm_calls.append(prompt)
            if isinstance(llm_side_effect, Exception):
                raise llm_side_effect
            return llm_side_effect

        def fake_validate(st, _path):
            validate_calls.append("validate")
            if validate_queue:
                st["validation_result"] = validate_queue.pop(0)

        adapter = MagicMock()
        adapter.has_capability.return_value = True

        with (
            patch("src.aqueduct.platform.get_platform_adapter", return_value=adapter),
            patch("src.aqueduct.tools.registry.get_tool", return_value=executor),
            patch(
                "src.aqueduct.engine.nodes.sql.call_llm",
                side_effect=fake_llm,
            ),
            patch(
                "src.aqueduct.engine.nodes.sql._auto_validate",
                side_effect=fake_validate,
            ),
            patch(
                "src.aqueduct.engine.nodes.sql.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "",
            ),
            patch(
                "src.aqueduct.engine.nodes.sql._resolve_sql_path",
                return_value=canonical,
            ),
        ):
            from src.aqueduct.engine.nodes.sql import _auto_trial_run

            _auto_trial_run(state, "output/test/Phase4-test.sql")

        return llm_calls, saved, validate_calls

    def test_partition_over_limit_fix_accepted(self, tmp_path):
        """分区超限 → 定向自修 → 复跑通过 + linter 干净 → 接受（回写+审计副本）。"""
        state = self._make_state(tmp_path)
        executor = self._executor([False, True], self._PARTITION_ERR)

        llm_calls, saved, validate_calls = self._run(
            state, tmp_path, executor, f"```sql\n{self._FIXED_SQL}\n```", [{"issues": []}]
        )

        assert llm_calls, "分区超限必须触发定向自修 LLM 调用"
        prompt = llm_calls[0]
        assert "PartitionOverLimit" in prompt  # 错误信息在场
        assert "inc_day" in prompt  # 分区硬规则在场
        assert self._SQL in prompt  # 原 SQL 在场
        assert "400" in prompt  # 平台分区上限背景在场
        # 接受：state 与规范文件更新，审计副本落盘
        assert state["sql_content"] == self._FIXED_SQL
        assert (tmp_path / "Phase4-test.sql").read_text(encoding="utf-8") == self._FIXED_SQL
        assert "Phase4-test_trialfix.sql" in saved
        # 试跑结果更新为修复后的复跑（通过）
        assert state["trial_run_result"]["passed"] == 1
        assert state["trial_run_result"]["errors"] == []
        # 接受前 linter 复检一次
        assert validate_calls == ["validate"]

    def test_partition_fix_rejected_when_retrial_still_fails(self, tmp_path):
        """修复后复跑同类别错误未减少 → 保持原 SQL，试跑结果维持首次记录。"""
        state = self._make_state(tmp_path)
        executor = self._executor([False, False], self._PARTITION_ERR)

        llm_calls, saved, validate_calls = self._run(
            state, tmp_path, executor, f"```sql\n{self._FIXED_SQL}\n```", []
        )

        assert len(llm_calls) == 1  # 仅一轮，不重试
        assert state["sql_content"] == self._SQL
        assert (tmp_path / "Phase4-test.sql").read_text(encoding="utf-8") == self._SQL
        assert "Phase4-test_trialfix.sql" not in saved
        assert validate_calls == []
        assert state["trial_run_result"]["passed"] == 0
        assert state["trial_run_result"]["errors"]  # 原样记录

    def test_non_selffixable_error_no_fix(self, tmp_path):
        """不可自愈类试跑错误（语法等）→ 不触发定向自修（走既有门禁）。"""
        state = self._make_state(tmp_path)
        executor = self._executor([False], "ParseException: 语法错误 near 'form'")

        llm_calls, saved, validate_calls = self._run(state, tmp_path, executor, "x", [])

        assert llm_calls == []
        assert "Phase4-test_trialfix.sql" not in saved
        assert validate_calls == []
        assert state["sql_content"] == self._SQL

    def test_partition_fix_llm_failure_keeps_original(self, tmp_path):
        """LLM 调用失败 → 保持原 SQL，试跑结果照旧记录。"""
        state = self._make_state(tmp_path)
        executor = self._executor([False], self._PARTITION_ERR)

        llm_calls, saved, _validate_calls = self._run(
            state, tmp_path, executor, RuntimeError("网关炸了"), []
        )

        assert len(llm_calls) == 1
        assert state["sql_content"] == self._SQL
        assert "Phase4-test_trialfix.sql" not in saved
        assert state["trial_run_result"]["errors"]

    def test_partition_fix_rolled_back_on_linter_error(self, tmp_path):
        """复跑通过但修复引入 linter ERROR → 回退原 SQL 并还原校验结果。"""
        state = self._make_state(tmp_path)
        executor = self._executor([False, True], self._PARTITION_ERR)

        llm_calls, saved, validate_calls = self._run(
            state,
            tmp_path,
            executor,
            f"```sql\n{self._FIXED_SQL}\n```",
            [{"issues": [{"level": "ERROR", "message": "除法未判零", "line": 3}]}, {"issues": []}],
        )

        assert len(llm_calls) == 1
        assert state["sql_content"] == self._SQL  # 回退
        assert (tmp_path / "Phase4-test.sql").read_text(encoding="utf-8") == self._SQL
        # 审计副本已留痕（与 _self_check_fix 同语义：失败尝试不抹除）
        assert "Phase4-test_trialfix.sql" in saved
        # 复检两次：接受尝试一次 + 回退还原一次
        assert len(validate_calls) == 2
        assert state["trial_run_result"]["passed"] == 0  # 保持首次（失败）记录

    def test_table_not_found_fix_accepted(self, tmp_path):
        """第十七刀(a)：幻觉表 → 定向自修，prompt 注入真实表清单 → 换表后接受。"""
        state = self._make_state(tmp_path, self._TABLE_SQL)
        executor = self._executor([False, True], self._TABLE_ERR)

        llm_calls, saved, validate_calls = self._run(
            state, tmp_path, executor, f"```sql\n{self._TABLE_FIXED_SQL}\n```", [{"issues": []}]
        )

        assert llm_calls, "Table not found 必须触发定向自修 LLM 调用"
        prompt = llm_calls[0]
        assert "Table not found" in prompt  # 错误信息在场
        assert "ods_rmds.tb_res_user" in prompt  # 真实表清单注入
        assert "dwd.trade_order_di" in prompt
        assert self._TABLE_SQL in prompt  # 原 SQL 在场
        # 第十八刀(a) run 24 实录：换表要求必须连带分区规则——run 24 模型换表
        # 成功（table 1→0）但换上的裸表撞分区钩子（partition 0→1），一错换一错
        assert "换上的表必须带 inc_day 分区过滤" in prompt
        # 接受：换表 SQL 回写 + 审计副本落盘
        assert state["sql_content"] == self._TABLE_FIXED_SQL
        assert "Phase4-test_trialfix.sql" in saved
        assert state["trial_run_result"]["passed"] == 1
        assert validate_calls == ["validate"]

    def test_partition_prompt_names_unfiltered_tables(self, tmp_path):
        """第十七刀(c)：分区 prompt 静态点名缺 inc_day 的表引用（构造性锚点）。"""
        state = self._make_state(tmp_path)
        executor = self._executor([False, True], self._PARTITION_ERR)

        llm_calls, _saved, _v = self._run(
            state, tmp_path, executor, f"```sql\n{self._FIXED_SQL}\n```", [{"issues": []}]
        )

        prompt = llm_calls[0]
        assert "未检测到 inc_day 分区过滤" in prompt
        # 原 SQL 里出现一次，锚点点名再出现一次
        assert prompt.count("ods_rmds.tb_res_user") >= 2

    def test_partial_improvement_accepted(self, tmp_path):
        """第十七刀(b) run 23 实录：分区修好但残留 table 类错误 = 部分改善，接受。"""
        state = self._make_state(tmp_path, self._SQL_MULTI)
        executor = self._executor_seq(
            [
                (False, self._PARTITION_ERR),  # 首次试跑 SELECT #1：分区超限
                (False, self._TABLE_ERR),  # 首次试跑 SELECT #2：幻觉表
                (True, ""),  # 复跑 SELECT #1：已修好
                (False, self._TABLE_ERR),  # 复跑 SELECT #2：仍幻觉表
            ]
        )

        llm_calls, saved, validate_calls = self._run(
            state, tmp_path, executor, f"```sql\n{self._PARTIAL_FIXED_SQL}\n```", [{"issues": []}]
        )

        assert len(llm_calls) == 1
        # 部分改善应被接受（原门禁全有全无会否决）
        assert state["sql_content"] == self._PARTIAL_FIXED_SQL
        assert "Phase4-test_trialfix.sql" in saved
        # 试跑结果=复跑的部分通过记录
        assert state["trial_run_result"]["passed"] == 1
        assert len(state["trial_run_result"]["errors"]) == 1
        assert "Table not found" in state["trial_run_result"]["errors"][0]
        assert validate_calls == ["validate"]

    def test_category_worse_rejected(self, tmp_path):
        """分区修好但引入新类别错误（幻觉表）→ not_worse 不满足 → 拒绝保持原状。"""
        state = self._make_state(tmp_path)
        worse_sql = self._TABLE_SQL  # 去掉分区过滤 + 换成幻觉表
        executor = self._executor_seq([(False, self._PARTITION_ERR), (False, self._TABLE_ERR)])

        llm_calls, saved, validate_calls = self._run(
            state, tmp_path, executor, f"```sql\n{worse_sql}\n```", []
        )

        assert len(llm_calls) == 1
        assert state["sql_content"] == self._SQL
        assert "Phase4-test_trialfix.sql" not in saved
        assert validate_calls == []
        assert state["trial_run_result"]["passed"] == 0

    def test_minified_output_rejected(self, tmp_path):
        """修复输出长度 < 原 SQL 一半（丢逻辑嫌疑）→ 直接拒绝，不进入复跑。"""
        state = self._make_state(tmp_path)
        executor = self._executor([False], self._PARTITION_ERR)

        llm_calls, saved, validate_calls = self._run(
            state,
            tmp_path,
            executor,
            "```sql\ninsert overwrite table dw.t partition (inc_day = 'x')\nselect 1\n```",
            [],
        )

        assert len(llm_calls) == 1
        assert state["sql_content"] == self._SQL
        assert "Phase4-test_trialfix.sql" not in saved
        assert validate_calls == []
        assert state["trial_run_result"]["passed"] == 0  # 首次（失败）记录原样


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
