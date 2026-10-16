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

    def __init__(self, latest="20261014", row_cnt=40482023, fail_sqls=()):
        self.latest = latest
        self.row_cnt = row_cnt
        self.fail_sqls = list(fail_sqls)
        self.executed: list[str] = []

    def execute(self, **kwargs):
        sql = kwargs.get("sql", "")
        self.executed.append(sql)
        rows = self._rows_for(sql)
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
        if "group by" in sql and "hl_scene_type" in sql:
            return [["集收", 20499080], ["散收", 19982943]]
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
