"""EstimatorTool 误报修复单元测试。

2026-10-15 实录（同需求受控对比）：真实 3 源表 SQL 被报"来源表 30 张"
（RE_TABLE_NAME 把 alias.column 配对当表名），full outer join + inner join
链被报"JOIN 多于 ON 疑似笛卡尔积"（全局裸计数，未感知 join 变体与注释）。
误报的成本预警比没有更糟。
"""

from __future__ import annotations

import pytest

from src.aqueduct.tools.estimator import CostEstimator


@pytest.fixture()
def write_sql(tmp_path):
    def _write(content: str):
        f = tmp_path / "Phase4-t.sql"
        f.write_text(content, encoding="utf-8")
        return CostEstimator(f)

    return _write


class TestExtractTables:
    def test_alias_column_pairs_not_counted(self, write_sql):
        est = write_sql(
            """
            insert overwrite table dm.target partition (inc_day='${bizdate}')
            select t.dept_code, w.guid, d.dept_name
            from dm.a t
            left join dm.b w on t.id = w.id
            left join dim.c d on t.dept_code = d.dept_code
            where t.inc_day = '${bizdate}'
            """
        )
        est.load_sql()
        est.extract_tables()
        # 修复前：t.dept_code/w.guid/d.dept_name 等 alias.column 全被当表 → 30 张
        assert sorted(est.tables) == ["dim.c", "dm.a", "dm.b"]

    def test_case_insensitive_dedup(self, write_sql):
        est = write_sql(
            "select * from DM.A t where t.inc_day='1' and exists (select 1 from dm.a s where s.inc_day='1')"
        )
        est.load_sql()
        est.extract_tables()
        assert est.tables == ["DM.A"]

    def test_comments_and_strings_not_counted(self, write_sql):
        est = write_sql(
            """
            -- 参考: ods.raw.user_log 不在本任务范围
            select 'x.y' as tag, t.*
            from dm.a t
            where t.inc_day='1'
            """
        )
        est.load_sql()
        est.extract_tables()
        assert est.tables == ["dm.a"]

    def test_subquery_is_not_a_table(self, write_sql):
        est = write_sql(
            """
            select t.a from (select a from dm.a where inc_day='1') t where t.a is not null
            """
        )
        est.load_sql()
        est.extract_tables()
        assert est.tables == ["dm.a"]


class TestCartesianRisk:
    def test_full_outer_join_chain_no_false_positive(self, write_sql):
        est = write_sql(
            """
            select coalesce(a.k, b.k) as k
            from (select k from dm.a where inc_day='1') a
            full outer join (select k from dm.b where inc_day='1') b on a.k = b.k
            left join (select k from dim.c where inc_day='1') c on coalesce(a.k, b.k) = c.k
            """
        )
        est.load_sql()
        est.check_risks()
        assert not any("笛卡尔积" in r for r in est.risks)

    def test_join_without_on_flagged(self, write_sql):
        est = write_sql(
            """
            select t.a, w.b from dm.a t
            inner join dm.b w
            where t.inc_day='1'
            """
        )
        est.load_sql()
        est.check_risks()
        assert any("笛卡尔积" in r for r in est.risks)

    def test_join_mentioned_in_comment_ignored(self, write_sql):
        est = write_sql(
            """
            -- 历史版本曾 join dm.old on old.id 未走索引
            select t.a from dm.a t where t.inc_day='1'
            """
        )
        est.load_sql()
        est.check_risks()
        assert not any("笛卡尔积" in r for r in est.risks)


class TestReportCount:
    def test_report_shows_real_table_count(self, write_sql):
        est = write_sql(
            """
            select t.dept_code, w.guid
            from dm.a t left join dm.b w on t.id = w.id
            where t.inc_day='1'
            """
        )
        est.load_sql()
        est.extract_tables()
        est.check_risks()
        report = est.generate_report()
        assert "来源表数量**: 2" in report
