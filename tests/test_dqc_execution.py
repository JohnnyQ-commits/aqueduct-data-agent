"""DQC 执行通道预处理测试——run 8 实录 22 failed 解剖。

两类失败：①~12 条 ParseException `near ';' <EOF>`——DQC 模板每条用例是
`SQL` + `;` 行 + `-- 预期:` 注释行，_parse_dqc_sql 收尾 rstrip(";") 只剥
字符串末尾（末尾是 `-- 预期` 注释），中间的 `;` 原样进引擎；②占位符
`'${bizdate}'` 未替换直接进引擎（试跑通道有哨兵替换，DQC 通道没对齐）。

修法（对齐试跑通道语义）：用例收尾时剥语句终结分号 + 调度参数哨兵替换。
"""

from __future__ import annotations

from src.aqueduct.engine.nodes.dqc import _parse_dqc_sql

# run 8 实录形态：SQL + ; 行 + -- 预期 注释行（分号夹在中间）
_RUN8_SHAPE = """\
-- [唯一性-主键重复] 检查 dept_code 在同分区下是否重复
-- 权重: High
-- 阈值: >0
select
    dept_code,
    count(*) as cnt
from dm_tc_waybillinfo.dws_dept_waybill_daily_di
where inc_day = '${bizdate}'
group by dept_code
having count(*) > 1
;
-- 预期: 0 行（主键不应重复，同分区每个网点编码仅一条）

-- [唯一性-主键空值] 检查主键字段 dept_code 是否为空
-- 权重: High
select count(*) as null_pk_cnt
from dm_tc_waybillinfo.dws_dept_waybill_daily_di
where inc_day = '${bizdate}'
  and dept_code is null
;
-- 预期: 0 行
"""


class TestDqcParsePreprocess:
    """_parse_dqc_sql 用例收尾预处理：剥分号 + 占位哨兵。"""

    def test_semicolon_before_trailing_comment_removed(self):
        """; 行夹在 SQL 与 -- 预期 注释之间时仍须剥掉（run 8 ParseException 根因）。"""
        cases = _parse_dqc_sql(_RUN8_SHAPE)
        assert len(cases) == 2
        for tc in cases:
            # 提交给引擎的语句体不得含语句终结分号（注释里的分号不受影响）
            body = "\n".join(ln for ln in tc["sql"].split("\n") if not ln.strip().startswith("--"))
            assert ";" not in body, f"用例 {tc['name']} 语句体残留分号"

    def test_sched_param_replaced_with_sentinel(self):
        """'${bizdate}' 占位替换为哨兵串（对齐试跑通道，防必然 ParseException）。"""
        cases = _parse_dqc_sql(_RUN8_SHAPE)
        assert len(cases) == 2
        for tc in cases:
            assert "${bizdate}" not in tc["sql"]
            assert "__AQ_TRIAL_SENTINEL__" in tc["sql"]

    def test_bare_sched_param_also_replaced(self):
        """裸占位（IN 列表等不带引号形态）同样替换并补引号。"""
        sql = "-- [一致性-枚举] 枚举检查\nselect count(*) from t where c in (${scenes})\n;\n"
        cases = _parse_dqc_sql(sql)
        assert len(cases) == 1
        assert "${scenes}" not in cases[0]["sql"]
        assert "'__AQ_TRIAL_SENTINEL__'" in cases[0]["sql"]

    def test_trailing_semicolon_still_stripped(self):
        """既有行为保持：SQL 直接以分号结尾（无注释尾随）时剥掉。"""
        sql = "-- [唯一性-重复] 检查\nselect dept_code from t group by dept_code having count(*) > 1;\n"
        cases = _parse_dqc_sql(sql)
        assert len(cases) == 1
        assert not cases[0]["sql"].rstrip().endswith(";")

    def test_case_names_and_comment_structure_preserved(self):
        """用例名取自 -- [ 头，SQL 主体与注释结构不破坏。"""
        cases = _parse_dqc_sql(_RUN8_SHAPE)
        assert cases[0]["name"].startswith("-- [唯一性-主键重复]")
        assert "having count(*) > 1" in cases[0]["sql"]
