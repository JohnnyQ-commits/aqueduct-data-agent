"""P1-2: 真实跑数门禁——试跑从 best-effort 升级为强制门禁。

现状两问题：
1. ETL SQL 是 `INSERT OVERWRITE ... SELECT` 形态，_extract_select_statements
   只认 SELECT 开头的语句 → 真实管道试跑 100% 跳过（从未生效）
2. 试跑失败只记 warning，不进修复循环

门禁语义：试跑失败（语法错误/字段不对齐）→ Critical issues → 修复循环 →
回跳 review 现场复检（与 P0-1 linter 同构）→ 修复上限后仍失败 → 终止。
execution 未启用 / 数据平台不可用 / 无可试跑语句 → 跳过（零误报）。
"""

from __future__ import annotations

from unittest.mock import patch

# ---------- 测试素材 ----------

# v5 真实 ETL SQL 形态：INSERT OVERWRITE ... PARTITION(...) SELECT
_ETL_SQL = (
    "-- 需求头部注释\n"
    "insert overwrite table dw_demo.dws_order_daily_stat_di partition (inc_day = '${bizdate}')\n"
    "select\n"
    "    t.order_count,\n"
    "    t.customer_count\n"
    "from (\n"
    "    select city, count(*) as order_count, count(distinct customer_id) as customer_count\n"
    "    from dw_demo.dwd_order_info_di\n"
    "    where inc_day = '${bizdate}'\n"
    "    group by city\n"
    ") t\n"
)

_PLAIN_SELECT = "select city, count(*) as cnt from dwd.order_detail group by city"

_WITH_SELECT = (
    "with base as (select * from dwd.order_detail where inc_day = '20260101')\nselect * from base"
)

_INSERT_VALUES = "insert into t values (1, 'a'), (2, 'b')"


def _make_state(sql_content: str = _ETL_SQL) -> dict:
    return {
        "requirement": "需求文档",
        "requirement_summary": "统计每日订单量",
        "mode": "dev",
        "metadata": {"requirement_name": "trial_gate_test"},
        "errors": [],
        "artifacts": [],
        "sql_content": sql_content,
        "domain_context": "",
        "validation_result": {},
        "fix_iterations": 0,
    }


def _ok_settings(**overrides):
    """真实 bool 的 settings mock（execution_enabled 必须 is True，MagicMock 会被护栏拦截）。"""
    s = type(
        "S",
        (),
        {
            "execution_enabled": True,
            "max_fix_iterations": 3,
            **overrides,
        },
    )()
    return s


def _executor_tool(health_ok: bool = True, exec_results: list[bool] | None = None):
    """构造 executor 工具 mock。exec_results 按次序返回成败（越界取最后一个）。"""
    results = exec_results if exec_results is not None else [True]
    calls = {"n": 0}

    class _T:
        def health_check(self):
            return type("H", (), {"success": health_ok})()

        def execute(self, **kwargs):
            # 真实 executor 单方法多 action——mock 必须按 action 分发，
            # 否则 health_check 吃掉 exec_results[0]，门禁被误跳过
            if kwargs.get("action") == "health_check":
                return type("H", (), {"success": health_ok, "error": None})()
            ok = results[min(calls["n"], len(results) - 1)]
            calls["n"] += 1
            return type(
                "R",
                (),
                {
                    "success": ok,
                    "error": None if ok else "ParseException: 语法错误 near 'form'",
                    "data": {"rows": []},
                },
            )()

    return _T()


# ---------- _extract_select_statements：INSERT 头剥离 ----------


class TestExtractSelectStatements:
    """ETL 形态 SQL 的 SELECT 查询体提取（P1-2 修复：试跑对 INSERT...SELECT 生效）。"""

    def test_insert_overwrite_stripped(self):
        from src.aqueduct.engine.nodes.sql import _extract_select_statements

        stmts = _extract_select_statements(_ETL_SQL)
        assert len(stmts) == 1
        assert stmts[0].lstrip().lower().startswith("select")
        assert "insert overwrite" not in stmts[0].lower()

    def test_insert_into_stripped(self):
        from src.aqueduct.engine.nodes.sql import _extract_select_statements

        stmts = _extract_select_statements("insert into dw.t select a, b from src")
        assert len(stmts) == 1
        assert stmts[0].lstrip().lower().startswith("select")

    def test_plain_select_unchanged(self):
        from src.aqueduct.engine.nodes.sql import _extract_select_statements

        assert _extract_select_statements(_PLAIN_SELECT) == [_PLAIN_SELECT]

    def test_with_select_unchanged(self):
        from src.aqueduct.engine.nodes.sql import _extract_select_statements

        stmts = _extract_select_statements(_WITH_SELECT)
        assert len(stmts) == 1
        assert stmts[0].lstrip().lower().startswith("with")

    def test_insert_values_skipped(self):
        """INSERT VALUES 无查询体，不产出可试跑语句。"""
        from src.aqueduct.engine.nodes.sql import _extract_select_statements

        assert _extract_select_statements(_INSERT_VALUES) == []


# ---------- _run_trial_selects：试跑执行核心 ----------


class TestRunTrialSelects:
    """SELECT 查询体 LIMIT 10 试跑执行。"""

    def test_limit_appended(self):
        """无 LIMIT 的 SELECT 追加 LIMIT 10。"""
        from src.aqueduct.engine.nodes import sql as sql_mod

        captured: list[str] = []

        class _T:
            def health_check(self):
                return type("H", (), {"success": True})()

            def execute(self, **kwargs):
                captured.append(kwargs.get("sql", ""))
                return type("R", (), {"success": True, "error": None})()

        with patch("src.aqueduct.tools.registry.get_tool", return_value=_T()):
            result = sql_mod._run_trial_selects(_ETL_SQL)

        assert result["passed"] == 1
        assert result["errors"] == []
        assert "LIMIT 10" in captured[0]
        assert captured[0].lstrip().lower().startswith("select")

    def test_existing_limit_not_duplicated(self):
        """已带 LIMIT 10 的语句不重复追加（原 split()[-1:] 逻辑对 `limit 10` 结尾会重复追加）。"""
        from src.aqueduct.engine.nodes import sql as sql_mod

        captured: list[str] = []

        class _T:
            def health_check(self):
                return type("H", (), {"success": True})()

            def execute(self, **kwargs):
                captured.append(kwargs.get("sql", ""))
                return type("R", (), {"success": True, "error": None})()

        sql = "select * from t where inc_day = '20260101' limit 10"
        with patch("src.aqueduct.tools.registry.get_tool", return_value=_T()):
            sql_mod._run_trial_selects(sql)

        assert captured[0].lower().count("limit") == 1

    def test_failure_recorded(self):
        """执行失败 → errors 记录带语句序号。"""
        from src.aqueduct.engine.nodes import sql as sql_mod

        tool = _executor_tool(exec_results=[False])
        with patch("src.aqueduct.tools.registry.get_tool", return_value=tool):
            result = sql_mod._run_trial_selects(_ETL_SQL)

        assert result["passed"] == 0
        assert len(result["errors"]) == 1
        assert "SELECT #1" in result["errors"][0]
        assert "语法错误" in result["errors"][0]

    def test_max_three_statements(self):
        """超过 3 条 SELECT 只试跑前 3 条。"""
        from src.aqueduct.engine.nodes import sql as sql_mod

        captured: list[str] = []

        class _T:
            def health_check(self):
                return type("H", (), {"success": True})()

            def execute(self, **kwargs):
                captured.append(kwargs.get("sql", ""))
                return type("R", (), {"success": True, "error": None})()

        sql = "\n".join(f"select {i} from t{i};" for i in range(5))
        with patch("src.aqueduct.tools.registry.get_tool", return_value=_T()):
            result = sql_mod._run_trial_selects(sql)

        assert result["total"] == 5
        assert result["tested"] == 3
        assert len(captured) == 3


# ---------- _trial_run_issues：review 侧门禁注入 ----------


class TestTrialRunIssues:
    """试跑门禁注入：防护跳过（零误报）与 Critical 注入。"""

    def test_execution_disabled_skips(self):
        from src.aqueduct.engine.nodes import review as review_mod

        state = _make_state()
        with patch(
            "src.aqueduct.config.settings.get_settings",
            return_value=_ok_settings(execution_enabled=False),
        ):
            assert review_mod._trial_run_issues(state) == []

    def test_non_bool_settings_skips(self):
        """settings 为 MagicMock（单测未显式开启）时跳过——防止既有测试真连数据平台。"""
        from src.aqueduct.engine.nodes import review as review_mod

        state = _make_state()
        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.execution_enabled = object()  # 模拟 MagicMock 属性：非 bool
            assert review_mod._trial_run_issues(state) == []

    def test_short_sql_skips(self):
        from src.aqueduct.engine.nodes import review as review_mod

        state = _make_state(sql_content="SELECT 1")
        with patch("src.aqueduct.config.settings.get_settings", return_value=_ok_settings()):
            assert review_mod._trial_run_issues(state) == []

    def test_health_check_fail_skips(self):
        """数据平台不可用 → 跳过（连接故障不应误报为 SQL 问题）。"""
        from src.aqueduct.engine.nodes import review as review_mod

        state = _make_state()
        tool = _executor_tool(health_ok=False)
        with (
            patch("src.aqueduct.config.settings.get_settings", return_value=_ok_settings()),
            patch("src.aqueduct.tools.registry.get_tool", return_value=tool),
        ):
            assert review_mod._trial_run_issues(state) == []

    def test_failure_yields_critical_issues(self):
        """试跑失败 → Critical issues 注入 + state 更新为最新结果。"""
        from src.aqueduct.engine.nodes import review as review_mod

        state = _make_state()
        tool = _executor_tool(exec_results=[False])
        with (
            patch("src.aqueduct.config.settings.get_settings", return_value=_ok_settings()),
            patch("src.aqueduct.tools.registry.get_tool", return_value=tool),
        ):
            issues = review_mod._trial_run_issues(state)

        assert len(issues) == 1
        assert issues[0]["severity"] == "Critical"
        assert "[试跑]" in issues[0]["message"]
        assert state["trial_run_result"]["passed"] == 0

    def test_pass_yields_no_issues(self):
        from src.aqueduct.engine.nodes import review as review_mod

        state = _make_state()
        tool = _executor_tool(exec_results=[True])
        with (
            patch("src.aqueduct.config.settings.get_settings", return_value=_ok_settings()),
            patch("src.aqueduct.tools.registry.get_tool", return_value=tool),
        ):
            assert review_mod._trial_run_issues(state) == []
        assert state["trial_run_result"]["passed"] == 1


# ---------- node_review 集成：门禁触发修复循环 ----------


class TestNodeReviewTrialGate:
    """试跑门禁接入 node_review 的修复循环。"""

    @staticmethod
    def _run_review(state, tool):
        from src.aqueduct.engine.nodes.review import node_review

        with (
            patch("src.aqueduct.engine.nodes.review.get_skill") as mock_skill,
            patch(
                "src.aqueduct.engine.nodes.review.call_llm",
                return_value="# 审查报告\n无问题",
            ),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value=""),
            # 投机 DQC 线程在 with 退出后仍会跑（patch 已解除→真连 LLM），
            # 试跑门禁测试不关心 DQC，直接禁用
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch(
                "src.aqueduct.config.settings.get_settings",
                return_value=_ok_settings(),
            ),
            patch("src.aqueduct.tools.registry.get_tool", return_value=tool),
        ):
            mock_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": "test"}}
            )()
            node_review(state)
        return state

    def test_trial_failure_triggers_fix_loop(self):
        """试跑失败 + LLM 审查干净 → 仍触发修复循环（门禁强制）。"""
        state = _make_state()
        tool = _executor_tool(exec_results=[False])

        self._run_review(state, tool)

        assert state["_needs_fix_loop"] is True
        messages = [i["message"] for i in state["_review_issues"]]
        assert any("[试跑]" in m for m in messages)

    def test_trial_recovers_after_fix(self):
        """修复回跳复检：executor 先失败后成功 → 第二轮 review 通过。"""
        state = _make_state()
        tool = _executor_tool(exec_results=[False, True])

        self._run_review(state, tool)  # 第一轮：试跑失败
        assert state["_needs_fix_loop"] is True

        self._run_review(state, tool)  # 第二轮（模拟修复后回跳）：试跑通过
        assert state["_needs_fix_loop"] is False
        assert state["trial_run_result"]["passed"] == 1

    def test_clean_trial_no_fix_loop(self):
        """试跑通过 + LLM 审查干净 → 不触发修复循环。"""
        state = _make_state()
        tool = _executor_tool(exec_results=[True])

        self._run_review(state, tool)

        assert state["_needs_fix_loop"] is False
        assert state.get("_review_issues") is None or state["_review_issues"] == []
