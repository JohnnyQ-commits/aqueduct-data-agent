"""Phase 1 数据探查（profile）单元测试。

受控对比定靶（2026-10-16）：CLI 的 MCP 通道只查表结构不跑数据，需求里埋的
guid/枚举两个陷阱全踩。本批为 Phase 1 增加确定性数据探查——对每个源表最新
分区跑固定画像探查（行数 / 候选键 distinct / 需求提及字段枚举分布 / 空值率），
纯代码执行，结果注入需求理解 prompt。

复用试跑同款执行通道：get_tool("executor").execute(action="execute", sql=...)。
门禁复用 Capability.SQL_EXECUTE（NoneAdapter / execution_enabled=False 优雅降级）。
"""

import pytest

from src.aqueduct.engine.nodes.requirement import (
    _profile_source_tables,
    _select_profile_columns,
)

# ---------------------------------------------------------------------------
# 测试素材
# ---------------------------------------------------------------------------

SCHEMA_TEXT = (
    "表: dm_tc_waybillinfo.dwd_pickup_waybill_info_dtl_di\n"
    "注释: 收件明细\n"
    "字段 (6 个):\n"
    "  - waybill_no (varchar) — 运单号\n"
    "  - guid (varchar) — 揽收AOI长编码\n"
    "  - emp_code (varchar) — 揽收小哥工号\n"
    "  - hl_scene_type (string) — 场景类型\n"
    "  - is_cancel (int) — 是否取消\n"
    "  - inc_day (string) — 分区键"
)

REQUIREMENT = (
    "# 网点每日收派件汇总\n"
    "统计各网点每日收件运单数（去重 guid）、收件小哥数、散收件量。\n"
    "散单判定以 hl_scene_type 实际枚举为准。"
)

TABLE = "dm_tc_waybillinfo.dwd_pickup_waybill_info_dtl_di"


class FakeExecutor:
    """按 SQL 特征分发的假 executor（模拟 executor 工具契约）。"""

    def __init__(self, latest="20261014", row_cnt=40482023, fail_sqls=(), error_map=None):
        self.latest = latest
        self.row_cnt = row_cnt
        self.fail_sqls = list(fail_sqls)
        self.error_map = dict(error_map or {})
        self.executed: list[str] = []
        self.call_kwargs: list[dict] = []

    def execute(self, **kwargs):
        sql = kwargs.get("sql", "")
        self.executed.append(sql)
        self.call_kwargs.append(kwargs)
        rows = self._rows_for(sql)
        if sql in self.error_map:
            import types

            return types.SimpleNamespace(success=False, error=self.error_map[sql], data={})
        if sql in self.fail_sqls or rows is None:
            import types

            return types.SimpleNamespace(success=False, error="编译失败", data={})
        import types

        return types.SimpleNamespace(success=True, data={"rows": rows, "row_count": len(rows)})

    def _rows_for(self, sql):
        if "max(inc_day)" in sql:
            return [[self.latest]]
        if "count(*)" in sql and "group by" not in sql and "count(distinct" not in sql:
            return [[self.row_cnt]]
        if "group by" in sql:
            if "hl_scene_type" in sql:
                return [["集收", 20499080], ["散收", 19982943]]
            # 其他 group by（如 guid 候选键探查）：回 1 行，避免 fixture 缺口
            # 触发 presto→hive 回落干扰引擎断言
            return [["散收", 19982943]]
        if "count(distinct" in sql:
            # 画像查询：total + 每列 (null, dist) 一一对应。
            # waybill_no 全唯一；guid distinct 远小于行数；其余 2 值
            import re as _re

            cols = _re.findall(r"as (\w+)_null", sql)
            row = [self.row_cnt]
            for c in cols:
                if c == "waybill_no":
                    row += [0, self.row_cnt]
                elif c == "guid":
                    row += [0, 118000]
                else:
                    row += [0, 2]
            return [row]
        return None


@pytest.fixture()
def _state():
    return {
        "requirement": REQUIREMENT,
        "table_schemas": {TABLE: SCHEMA_TEXT},
    }


def _patch_env(monkeypatch, adapter, executor):
    from src.aqueduct import platform as platform_pkg
    from src.aqueduct.tools import registry as registry_pkg

    monkeypatch.setattr(platform_pkg, "get_platform_adapter", lambda: adapter)
    monkeypatch.setattr(registry_pkg, "get_tool", lambda name: executor)


# ---------------------------------------------------------------------------
# 列选择启发式
# ---------------------------------------------------------------------------


class TestSelectProfileColumns:
    def test_mentioned_string_columns_become_enum_cols(self):
        enum_cols, _profile_cols = _select_profile_columns(SCHEMA_TEXT, REQUIREMENT)
        # 需求提及 hl_scene_type（string）→ 枚举探查
        assert "hl_scene_type" in enum_cols
        # int 列即使提及也不做枚举探查（is_cancel 未提及，waybill_no 非 string）
        assert "is_cancel" not in enum_cols

    def test_mentioned_and_key_like_columns_in_profile(self):
        _enum_cols, profile_cols = _select_profile_columns(SCHEMA_TEXT, REQUIREMENT)
        # 需求提及 guid / hl_scene_type → 入画像
        assert "guid" in profile_cols
        assert "hl_scene_type" in profile_cols
        # *_no/*_code 键形列 → 入画像
        assert "waybill_no" in profile_cols
        assert "emp_code" in profile_cols
        # 未提及且非键形 → 不入（is_cancel）
        assert "is_cancel" not in profile_cols

    def test_profile_columns_capped_at_8(self):
        many = "\n".join(f"  - col_{i:02d}_id (bigint)" for i in range(20))
        _, profile_cols = _select_profile_columns(
            f"表: db.t\n字段 (20 个):\n{many}", "统计 col_00_id"
        )
        assert len(profile_cols) <= 8

    def test_enum_columns_capped_at_3(self):
        many = "\n".join(f"  - scene_{i} (string)" for i in range(6))
        enum_cols, _ = _select_profile_columns(
            f"表: db.t\n字段 (6 个):\n{many}", " ".join(f"scene_{i}" for i in range(6))
        )
        assert len(enum_cols) <= 3


# ---------------------------------------------------------------------------
# 探查主流程
# ---------------------------------------------------------------------------


class TestProfileSourceTables:
    def test_no_sql_execute_capability_skips(self, _state, monkeypatch):
        from src.aqueduct.platform.none import NoneAdapter

        _patch_env(monkeypatch, NoneAdapter(), FakeExecutor())
        assert _profile_source_tables(_state, [TABLE]) == {}

    def test_happy_path_probes_and_formats(self, _state, monkeypatch):
        ex = FakeExecutor()
        _patch_env(monkeypatch, _FakeAdapter(), ex)
        profiles = _profile_source_tables(_state, [TABLE])
        text = profiles[TABLE]
        # 分区与行数
        assert "20261014" in text and "40482023" in text
        # 候选键唯一性画像（guid 陷阱：distinct < 行数）
        assert "waybill_no" in text and "guid" in text
        assert "全唯一" in text
        assert "存在重复" in text
        # 枚举分布（散单枚举陷阱）
        assert "集收" in text and "散收" in text
        # SQL 带分区过滤
        assert any("where inc_day='20261014'" in s for s in ex.executed)
        assert any("group by hl_scene_type" in s for s in ex.executed)
        assert any("count(distinct guid)" in s for s in ex.executed)

    def test_no_inc_day_column_marks_skip(self, _state, monkeypatch):
        ex = FakeExecutor()
        ex.fail_sqls = [s for s in []]  # 占位；用 _rows_for 返回 None 模拟无列
        # max(inc_day) 直接编译失败（表无该列）
        orig = ex._rows_for

        def rows_for(sql):
            if "max(inc_day)" in sql:
                return None
            return orig(sql)

        ex._rows_for = rows_for
        _patch_env(monkeypatch, _FakeAdapter(), ex)
        text = _profile_source_tables(_state, [TABLE])[TABLE]
        assert "无 inc_day" in text or "探查失败" in text
        # 不应继续发分组/count 探查
        assert not any("group by" in s for s in ex.executed)

    def test_single_probe_failure_degrades_not_raises(self, _state, monkeypatch):
        ex = FakeExecutor()
        # 枚举探查失败 → 该行标注失败，其余画像仍在
        ex.fail_sqls = [s for s in ex.executed]  # 空
        orig = ex._rows_for

        def rows_for(sql):
            if "group by" in sql:
                return None
            return orig(sql)

        ex._rows_for = rows_for
        _patch_env(monkeypatch, _FakeAdapter(), ex)
        text = _profile_source_tables(_state, [TABLE])[TABLE]
        assert "20261014" in text  # 行数探查仍在
        assert "waybill_no" in text  # 画像仍在

    def test_table_level_failure_degrades_to_marker(self, monkeypatch):
        class BoomExecutor:
            def execute(self, **kwargs):
                raise RuntimeError("连接失败")

        state = {"requirement": REQUIREMENT, "table_schemas": {TABLE: SCHEMA_TEXT}}
        _patch_env(monkeypatch, _FakeAdapter(), BoomExecutor())
        text = _profile_source_tables(state, [TABLE])[TABLE]
        assert "探查失败" in text

    def test_more_than_8_tables_truncated(self, _state, monkeypatch):
        ex = FakeExecutor()
        _patch_env(monkeypatch, _FakeAdapter(), ex)
        tables = [f"db.tbl_{i}" for i in range(10)]
        profiles = _profile_source_tables(_state, tables)
        assert len(profiles) == 8


class _FakeAdapter:
    """只声明 sql_execute 能力的假 adapter。"""

    name = "fake"

    def has_capability(self, cap: str) -> bool:
        return cap == "sql_execute"

    def capabilities(self):
        return frozenset({"sql_execute"})

    def health_check(self):
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# 注入链路：skill 渲染
# ---------------------------------------------------------------------------


class TestSkillInjection:
    def test_skill_renders_table_profiles(self):
        from src.aqueduct.skills.base import SkillContext
        from src.aqueduct.skills.requirement_and_design import RequirementAndDesignSkill

        state = {"requirement": REQUIREMENT, "domain_context": "", "target_table": "db.t"}
        skill = RequirementAndDesignSkill()
        ctx = SkillContext(
            input={
                "requirement_doc": REQUIREMENT,
                "table_schemas": {TABLE: SCHEMA_TEXT},
                "table_profiles": {TABLE: "表: x\n行数: 1\n- guid: distinct=2（< 行数，存在重复）"},
            },
            state=state,
        )
        result = skill.execute(ctx)
        assert result.success
        prompt = result.data["prompt"]
        assert "guid" in prompt and "存在重复" in prompt

    def test_skill_without_profiles_renders_placeholder(self):
        from src.aqueduct.skills.base import SkillContext
        from src.aqueduct.skills.requirement_and_design import RequirementAndDesignSkill

        state = {"requirement": REQUIREMENT, "domain_context": "", "target_table": "db.t"}
        skill = RequirementAndDesignSkill()
        ctx = SkillContext(
            input={"requirement_doc": REQUIREMENT, "table_schemas": {}},
            state=state,
        )
        prompt = skill.execute(ctx).data["prompt"]
        assert "未探查" in prompt


class TestTemplateFormatContract:
    """Phase1 摘要格式契约：表格化骨架 + 每题建议方案（瀑布 bullet 墙改造）。

    根因（2026-10-16 拍板）：模板骨架本身就是瀑布格式，模型忠实填空；
    skill 模式格式好是 harness+人工反馈的产物。改造走"骨架模板为主"路线
    （第十五刀先例：skeleton-fill 救格式失败，规则三轮救不动）。
    """

    def test_summary_section_is_table_skeleton(self):
        from pathlib import Path

        tpl = Path("src/aqueduct/skills/prompt/requirement_and_design.tpl.md").read_text(
            encoding="utf-8"
        )
        assert "| 维度 |" in tpl  # 摘要主体表格化
        assert "| 探查项 |" in tpl  # 探查发现表格化

    def test_questions_require_recommended_option(self):
        from pathlib import Path

        tpl = Path("src/aqueduct/skills/prompt/requirement_and_design.tpl.md").read_text(
            encoding="utf-8"
        )
        assert "建议 A（推荐）" in tpl  # 每题带推荐方案
        assert "禁止用无选项的开放式问题" in tpl

    def test_parse_questions_survives_ab_lines(self):
        """新骨架下问题解析契约不破：编号行捕获、A/B 子行不污染。"""
        from src.aqueduct.utils.qa import parse_questions

        summary = (
            "## 需求理解摘要\n\n| 维度 | 说明 |\n|------|------|\n| 目标表 | db.t |\n\n"
            "### 数据探查发现\n\n| 探查项 | 实测结果 |\n|--------|--------|\n"
            "### 待确认问题\n"
            "1. **散单枚举**：hl_scene_type 枚举未指定\n"
            "   - 建议 A（推荐）：按实测 集收/散收 执行，理由：平台实查\n"
            "   - 建议 B：按需求占位 ('01','02')\n"
        )
        qs = parse_questions(summary)
        assert len(qs) == 1
        assert "散单枚举" in qs[0]
        assert "建议" not in qs[0]


class TestEnumColumnPriority:
    """枚举列选择启发式：*_type/*_scene 类字段优先，时间戳字段不白探（实录：
    signin_tm 被探枚举而 hl_scene_type 被 cap 截掉）。"""

    SCHEMA2 = (
        "表: db.t\n"
        "字段 (5 个):\n"
        "  - signin_tm (string) — 签到时间\n"
        "  - dept_type_code (string) — 部门类型\n"
        "  - hl_scene_type (string) — 场景类型\n"
        "  - pay_status (string) — 支付状态\n"
        "  - inc_day (string) — 分区键"
    )

    def test_enumish_names_prioritized_over_timestamps(self):
        req = "统计 signin_tm dept_type_code hl_scene_type pay_status 的分布"
        enum_cols, _ = _select_profile_columns(self.SCHEMA2, req)
        # signin_tm 是时间戳不该进枚举探查；3 个枚举形列全进
        assert "signin_tm" not in enum_cols
        assert set(enum_cols) == {"dept_type_code", "hl_scene_type", "pay_status"}

    def test_plain_mentioned_string_still_eligible_when_no_enumish(self):
        req = "统计 signin_tm 的取值"
        enum_cols, _ = _select_profile_columns(self.SCHEMA2, req)
        assert enum_cols == ["signin_tm"]


class TestPhase1ContractAcceptsTable:
    """结构契约校验器认新表格骨架（2026-10-16 实录：表格格式被误报缺目标表）。"""

    def test_table_row_satisfies_target_rule(self):
        from src.aqueduct.engine.contract import validate_structure

        content = (
            "## 需求理解摘要\n\n"
            "| 维度 | 说明 |\n|------|------|\n"
            "| 目标表 | db.t |\n\n"
            "### 待确认问题\n1. x\n"
        )
        assert validate_structure("Phase1-需求理解摘要.md", content) == []


# ---------------------------------------------------------------------------
# 探查超时适配（run 3 实录 2026-10-19）
#
# _hive_wait 硬编码 5 分钟，MR 慢队列下明细表探查全灭 → 枚举目标 ③ 落空。
# 三件套：探查专用 10 分钟超时 / 当日结果缓存 / 超时未探查显式标注。
# ---------------------------------------------------------------------------


class TestProbeTimeoutAndCache:
    def test_probes_use_extended_wait_attempts(self, _state, monkeypatch):
        """探查通道用 120 次轮询（10 分钟），不是默认 60（5 分钟）。"""
        ex = FakeExecutor()
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        _profile_source_tables(_state, [TABLE])

        assert ex.call_kwargs, "探查未执行"
        assert all(kw.get("wait_attempts") == 120 for kw in ex.call_kwargs)

    def test_timeout_probe_gets_explicit_annotation(self, _state, monkeypatch):
        """探查超时：显式标注「探查超时/超时未探查」，不冒充「无 inc_day 分区键」。"""
        ex = FakeExecutor(
            error_map={"select max(inc_day) as latest_partition from " + TABLE: "任务超时 (10 min)"}
        )
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        profiles = _profile_source_tables(_state, [TABLE])

        text = profiles[TABLE]
        assert "探查超时" in text
        assert "超时未探查" in text
        assert "无 inc_day 分区键" not in text

    def test_successful_probe_cached_no_reexecute(self, _state, monkeypatch):
        """同日缓存：第二次跑同一探查 SQL 直接命中缓存，不再执行。"""
        ex = FakeExecutor()
        # 缓存只收成功结果（设计如此）——让全部探查成功，保证跑满 5 条 SQL
        orig = ex._rows_for

        def rows_for(sql):
            if "group by" in sql:
                return [["散收", 19982943]]
            return orig(sql)

        ex._rows_for = rows_for
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        _profile_source_tables(_state, [TABLE])
        first_count = len(ex.executed)
        assert first_count > 0

        _profile_source_tables(_state, [TABLE])
        assert len(ex.executed) == first_count, "缓存命中后不应重新执行探查 SQL"

    def test_timeout_result_not_cached(self, _state, monkeypatch):
        """超时结果不入缓存——重跑（如修复环恢复）允许用更长超时重试。"""
        timeout_sql = "select max(inc_day) as latest_partition from " + TABLE
        ex = FakeExecutor(error_map={timeout_sql: "任务超时 (10 min)"})
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        _profile_source_tables(_state, [TABLE])
        assert len(ex.executed) == 1  # 分区探查超时即止，后续探查不发

        # 恢复执行能力后重跑：分区探查应重试（而非读到缓存的超时结果）
        ex.error_map.clear()
        _profile_source_tables(_state, [TABLE])
        assert ex.executed.count(timeout_sql) == 2
        profiles = _profile_source_tables(_state, [TABLE])  # 第三跑全走缓存
        assert "探查分区" in profiles[TABLE]

    def test_timeout_via_guard_fallback_gets_annotation(self, _state, monkeypatch):
        """run4 实录：护栏拒→年窗回退后超时，超时信息被回退函数吞掉，
        冒充「无 inc_day 分区键」。回退路径的超时同样必须显式标注。"""
        import types

        ex = FakeExecutor()

        def execute(**kwargs):
            sql = kwargs.get("sql", "")
            ex.executed.append(sql)
            ex.call_kwargs.append(kwargs)
            if "where" not in sql:
                return types.SimpleNamespace(
                    success=False,
                    error="任务执行失败: FAILED: Hive Internal Error: "
                    "PartitionOverLimitException(can only query max 400 partition)",
                    data={},
                )
            return types.SimpleNamespace(success=False, error="任务超时 (10 min)", data={})

        ex.execute = execute
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        profiles = _profile_source_tables(_state, [TABLE])
        text = profiles[TABLE]
        assert "探查超时" in text, f"回退路径超时必须显式标注，实际：{text[:120]}"
        assert "超时未探查" in text


class TestPrestoProbeFastLane:
    """探查快车道（run 4 实录）：MR 探查 10 分钟×4 全灭，Presto 同 SQL 16 秒。
    探查通道引擎链 presto→spark→hive；不兼容优先转换语法（同引擎重试转换句）
    再降级下一引擎；超时立即中止整条链。试跑通道不动（保真）。"""

    def test_probes_default_to_presto_engine(self, _state, monkeypatch):
        ex = FakeExecutor()
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        _profile_source_tables(_state, [TABLE])

        assert ex.call_kwargs, "探查未执行"
        assert all(kw.get("engine") == "presto" for kw in ex.call_kwargs)

    def test_rejection_falls_through_chain_to_hive(self, _state, monkeypatch):
        """presto/spark 拒绝（不兼容语法）→ 链式降级，hive 引擎收尾成功。"""
        from itertools import groupby

        ex = FakeExecutor()
        orig = ex.execute
        calls: list[dict] = []

        def execute(**kwargs):
            calls.append(kwargs)
            if kwargs.get("engine") != "hive":
                import types

                return types.SimpleNamespace(
                    success=False, error="Invalid function 10011: nullif", data={}
                )
            return orig(**kwargs)

        ex.execute = execute
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        profiles = _profile_source_tables(_state, [TABLE])

        text = profiles[TABLE]
        # hive 收尾成功：分区/行数照常注入
        assert "20261014" in text and "40482023" in text
        # 每条 SQL 的引擎序列按「连续同引擎块」压缩后恰为 presto→spark→hive
        # （转换句与原句同引擎，压块后不依赖转换是否产出不同文本）
        for sql in set(ex.executed):
            seq = [c.get("engine") for c in calls if c.get("sql") == sql]
            blocks = [k for k, _ in groupby(seq)]
            assert blocks == ["presto", "spark", "hive"], f"{sql[:60]} 引擎块={blocks}"

    def test_timeout_aborts_engine_chain(self, _state, monkeypatch):
        """首引擎超时立即中止整条链——后续引擎只会更慢，不白等。"""
        timeout_sql = "select max(inc_day) as latest_partition from " + TABLE
        ex = FakeExecutor(error_map={timeout_sql: "任务超时 (10 min)"})
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        _profile_source_tables(_state, [TABLE])

        assert ex.executed.count(timeout_sql) == 1  # presto 原句超时即止
        assert not any("spark" in kw for kw in [])  # 占位断言由上行覆盖

    def test_timeout_failure_reported_once_not_doubled(self, _state, monkeypatch):
        timeout_sql = "select max(inc_day) as latest_partition from " + TABLE
        ex = FakeExecutor(error_map={timeout_sql: "任务超时 (10 min)"})
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        profiles = _profile_source_tables(_state, [TABLE])

        text = profiles[TABLE]
        assert "探查超时" in text
        assert text.count("任务超时") <= 1

    def test_presto_success_not_rerun_on_chain(self, _state, monkeypatch):
        """presto 成功即用，不继续链（每条 SQL 只执行一次）。"""
        ex = FakeExecutor()
        _patch_env(monkeypatch, _FakeAdapter(), ex)

        _profile_source_tables(_state, [TABLE])

        assert len(ex.executed) == len(set(ex.executed))
