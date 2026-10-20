"""探查分区护栏适配 + 试跑调度参数哨兵适配单元测试。

2026-10-16 UI 验证跑实录：
- `select max(inc_day) from 表`（无分区过滤）撞平台护栏
  PartitionOverLimitException（max 400 partition）→ 4 表探查全失败 →
  枚举占位 ${scatter_scene_types} → 试跑 ParseException → Critical halt。
  防御链闭环了，但靶心（探查识破陷阱）没打中。
- 目标表（还不存在）也被白探一条 Table not found。
- 调度参数占位 SQL 在试跑通道永远无法通过（参数未绑定）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.aqueduct.engine.nodes.requirement import _profile_source_tables

SCHEMA_TEXT = (
    "表: dm_tc_waybillinfo.dwd_pickup_waybill_info_dtl_di\n"
    "字段 (3 个):\n"
    "  - waybill_no (varchar) — 运单号\n"
    "  - hl_scene_type (string) — 场景类型\n"
    "  - inc_day (string) — 分区键"
)

REQUIREMENT = "统计收件运单数，散单判定以 hl_scene_type 实际枚举为准。"
TABLE = "dm_tc_waybillinfo.dwd_pickup_waybill_info_dtl_di"


class GuardedExecutor:
    """模拟平台分区护栏：无分区过滤的查询被拒，带过滤/show partitions 正常。"""

    def __init__(self, latest="20261015"):
        self.latest = latest
        self.executed: list[str] = []

    def execute(self, **kwargs):
        sql = kwargs.get("sql", "")
        self.executed.append(sql)
        low = sql.lower()
        if "show partitions" in low:
            rows = [[f"inc_day={self.latest}"]]
        elif "where inc_day" in low:
            rows = self._rows_for(sql)
        else:
            return SimpleNamespace(
                success=False,
                error="FAILED: Hive Internal Error: PartitionOverLimitException"
                "(can only query max 400 partition,but current query partitions is 1390)",
                data={},
            )
        return SimpleNamespace(success=True, data={"rows": rows, "row_count": len(rows)})

    def _rows_for(self, sql):
        if "max(inc_day)" in sql:
            return [[self.latest]]
        if "group by" in sql:
            return [["集收", 20499080], ["散收", 19982943]]
        if "count(distinct" in sql:
            import re as _re

            cols = _re.findall(r"as (\w+)_null", sql)
            row = [40482023]
            for _c in cols:
                row += [0, 2]
            return [row]
        if "count(*)" in sql:
            return [[40482023]]
        return None


def _patch_env(monkeypatch, executor, adapter=None):
    from src.aqueduct import platform as platform_pkg
    from src.aqueduct.tools import registry as registry_pkg

    if adapter is None:

        class _A:
            name = "fake"

            def has_capability(self, cap):
                return cap == "sql_execute"

            def capabilities(self):
                return frozenset({"sql_execute"})

            def health_check(self):
                return {"status": "ok"}

        adapter = _A()
    monkeypatch.setattr(platform_pkg, "get_platform_adapter", lambda: adapter)
    monkeypatch.setattr(registry_pkg, "get_tool", lambda name: executor)


class TestPartitionGuardFallback:
    def test_over_limit_falls_back_to_year_window(self, monkeypatch):
        ex = GuardedExecutor()
        _patch_env(monkeypatch, ex)
        state = {"requirement": REQUIREMENT, "table_schemas": {TABLE: SCHEMA_TEXT}}
        text = _profile_source_tables(state, [TABLE])[TABLE]
        assert "20261015" in text and "40482023" in text
        assert "集收" in text and "散收" in text
        # 实际走了年份窗口回退（不发 show partitions）
        assert not any("show partitions" in s.lower() for s in ex.executed)
        assert any("max(inc_day)" in s and "20260101" in s for s in ex.executed)
        # 后续探查都带分区过滤
        assert any("where inc_day='20261015'" in s for s in ex.executed)

    def test_show_partitions_also_fails_degrades(self, monkeypatch):
        class DeadExecutor(GuardedExecutor):
            def execute(self, **kwargs):
                sql = kwargs.get("sql", "")
                self.executed.append(sql)
                return SimpleNamespace(success=False, error="一切都不通", data={})

        ex = DeadExecutor()
        _patch_env(monkeypatch, ex)
        state = {"requirement": REQUIREMENT, "table_schemas": {TABLE: SCHEMA_TEXT}}
        text = _profile_source_tables(state, [TABLE])[TABLE]
        assert "探查失败" in text or "无 inc_day" in text


class TestTargetTableExcluded:
    def test_target_table_not_probed(self, monkeypatch):
        ex = GuardedExecutor()
        _patch_env(monkeypatch, ex)
        state = {
            "requirement": REQUIREMENT,
            "table_schemas": {TABLE: SCHEMA_TEXT},
            "target_table": "dm_tc_waybillinfo.dws_dept_waybill_daily_di",
        }
        tables = [TABLE, "dm_tc_waybillinfo.dws_dept_waybill_daily_di"]
        profiles = _profile_source_tables(state, tables)
        assert "dm_tc_waybillinfo.dws_dept_waybill_daily_di" not in profiles


class TestTrialParamSentinel:
    def test_sched_params_substituted_before_trial(self):
        """试跑前 ${param} 替换为哨兵串，参数化 SQL 不再必然 ParseException。"""
        from src.aqueduct.engine.nodes import sql as sql_mod

        sql_content = (
            "insert overwrite table dm.t partition (inc_day='${bizdate}')\n"
            "select dept_code, count(distinct case when hl_scene_type in "
            "(${scatter_scene_types}) then waybill_no end) as scatter_cnt\n"
            "from dm.a where inc_day='${bizdate}' group by dept_code"
        )
        captured = []

        class CapExecutor:
            def execute(self, **kwargs):
                captured.append(kwargs.get("sql", ""))
                return SimpleNamespace(success=True, data={"rows": [[1]], "row_count": 1})

        import src.aqueduct.tools.registry as registry_pkg

        orig = registry_pkg.get_tool
        registry_pkg.get_tool = lambda name: CapExecutor()
        try:
            trial = sql_mod._run_trial_selects(sql_content)
        finally:
            registry_pkg.get_tool = orig
        assert trial["total"] == 1  # INSERT 头剥掉、SELECT 体照常提取
        assert trial["passed"] == 1  # 哨兵替换后语法通过
        # 参数哨兵：提交的 SQL 不含未绑定占位符
        for s in captured:
            assert "${" not in s
            assert "__AQ_TRIAL_SENTINEL__" in s

    def test_quoted_placeholder_not_double_quoted(self):
        """实录 18:35 halt（run 2）：占位符在 SQL 中本已带引号（'${bizdate}'），
        替换值再包引号 → ''__AQ_TRIAL_SENTINEL__'' 必然 ParseException。
        带引号占位替换后必须仍是合法单引号字面量。"""
        from src.aqueduct.engine.nodes import sql as sql_mod

        sql_content = (
            "insert overwrite table dm.t partition (inc_day = ${bizdate})\n"
            "select dept_code, count(*) as cnt\n"
            "from dm.a where inc_day = '${bizdate}' group by dept_code"
        )
        captured = []

        class CapExecutor:
            def execute(self, **kwargs):
                captured.append(kwargs.get("sql", ""))
                return SimpleNamespace(success=True, data={"rows": [[1]], "row_count": 1})

        import src.aqueduct.tools.registry as registry_pkg

        orig = registry_pkg.get_tool
        registry_pkg.get_tool = lambda name: CapExecutor()
        try:
            trial = sql_mod._run_trial_selects(sql_content)
        finally:
            registry_pkg.get_tool = orig
        assert trial["total"] == 1 and trial["passed"] == 1
        # 带引号占位：'${bizdate}' → '__AQ_TRIAL_SENTINEL__'（不得出现双引号叠置）
        where_sql = next(s for s in captured if "where" in s)
        assert "= '__AQ_TRIAL_SENTINEL__'" in where_sql
        assert "''" not in where_sql
        # 裸占位（IN 列表等值位）也替换为合法字面量
        assert "__AQ_TRIAL_SENTINEL__" in where_sql


class TestTargetTableBackticks:
    def test_target_with_backticks_excluded(self, monkeypatch):
        """需求文档 markdown 内联代码使 target_table 带反引号——归一化后仍剔除。"""
        ex = GuardedExecutor()
        _patch_env(monkeypatch, ex)
        state = {
            "requirement": REQUIREMENT,
            "table_schemas": {TABLE: SCHEMA_TEXT},
            "target_table": "`dm_tc_waybillinfo.dws_dept_waybill_daily_di`",
        }
        profiles = _profile_source_tables(
            state, [TABLE, "dm_tc_waybillinfo.dws_dept_waybill_daily_di"]
        )
        assert "dm_tc_waybillinfo.dws_dept_waybill_daily_di" not in profiles


class TestLatestPartitionWindowed:
    """show partitions 返回被平台截断 → max 是陈旧分区（20230104 实录）。
    改用年份窗口谓词探 max(inc_day)：分区裁剪后扫描分区数 < 400 不撞护栏，
    且拿到的是真实最新分区。"""

    def test_year_window_returns_true_latest(self, monkeypatch):
        ex = GuardedExecutor(latest="20261015")
        _patch_env(monkeypatch, ex)
        state = {"requirement": REQUIREMENT, "table_schemas": {TABLE: SCHEMA_TEXT}}
        text = _profile_source_tables(state, [TABLE])[TABLE]
        assert "20261015" in text
        assert not any("show partitions" in s.lower() for s in ex.executed)

    def test_current_year_empty_falls_back_previous_year(self, monkeypatch):
        ex = GuardedExecutor(latest="20261015")
        orig = ex._rows_for

        def rows_for(sql):
            # 只拦当年窗口（下界 20260101）；2025 窗口上界也是 '20260101'，
            # 必须用 >= 锚定下界，否则两个年份都被误判为空
            if "max(inc_day)" in sql and ">= '20260101'" in sql:
                return [[None]]  # 当年无数据
            return orig(sql)

        ex._rows_for = rows_for
        _patch_env(monkeypatch, ex)
        state = {"requirement": REQUIREMENT, "table_schemas": {TABLE: SCHEMA_TEXT}}
        text = _profile_source_tables(state, [TABLE])[TABLE]
        assert "20261015" in text
        assert any("20250101" in s for s in ex.executed)


class TestTrialEngineChain:
    """试跑跟生产引擎走（2026-10-20 用户拍板）：生产常态 spark、大数据量才
    hive——试跑链 spark→hive，spark 被拒先转换重试再降 hive；超时立即中止。
    presto 不进试跑链（生产不用它跑 ETL）。"""

    SQL_CONTENT = (
        "insert overwrite table dm.t partition (inc_day='${bizdate}')\n"
        "select dept_code, count(*) as cnt\n"
        "from dm.a where inc_day='${bizdate}' group by dept_code"
    )

    def _run(self, execute_fn):
        import src.aqueduct.tools.registry as registry_pkg
        from src.aqueduct.engine.nodes import sql as sql_mod

        orig = registry_pkg.get_tool
        registry_pkg.get_tool = lambda name: type("Ex", (), {"execute": staticmethod(execute_fn)})()
        try:
            return sql_mod._run_trial_selects(self.SQL_CONTENT)
        finally:
            registry_pkg.get_tool = orig

    def test_trial_executes_on_spark_first(self):
        captured = []

        def execute(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(success=True, data={"rows": [[1]], "row_count": 1})

        trial = self._run(execute)
        assert trial["passed"] == 1
        assert captured and all(kw.get("engine") == "spark" for kw in captured)

    def test_spark_rejection_falls_to_hive(self):
        from itertools import groupby

        calls = []

        def execute(**kwargs):
            calls.append(kwargs)
            if kwargs.get("engine") == "spark":
                return SimpleNamespace(success=False, error="spark 语法拒绝", data={})
            return SimpleNamespace(success=True, data={"rows": [[1]], "row_count": 1})

        trial = self._run(execute)
        assert trial["passed"] == 1
        engines = [kw.get("engine") for kw in calls]
        blocks = [k for k, _ in groupby(engines)]
        assert blocks == ["spark", "hive"]
        assert engines[-1] == "hive"

    def test_spark_timeout_aborts_chain_counts_as_timeout(self):
        calls = []

        def execute(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(success=False, error="任务超时 (5 min)", data={})

        trial = self._run(execute)
        assert len(trial["timeouts"]) == 1  # 超时计入 passed（6b 语义不变）
        assert all(kw.get("engine") == "spark" for kw in calls)  # 不再白等 hive
