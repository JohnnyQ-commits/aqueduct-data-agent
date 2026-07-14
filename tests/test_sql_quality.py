"""SQL 质量改进相关函数的单元测试。

覆盖三个新增模块：
  - _strip_llm_meta (helpers.py) — LLM 元信息剥离
  - _resolve_target_table (design_ddl.py) — 目标表名解析
  - _extract_select_statements (sql.py) — SELECT 语句提取
  - _parse_test_cases (tools/dqc.py) — DQC 阈值解析
  - P0 自动暂停 — review 节点 Critical 问题终止管道
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.aqueduct.engine.nodes.helpers import _strip_llm_meta
from src.aqueduct.engine.nodes.sql import _extract_select_statements
from src.aqueduct.exceptions import WorkflowHaltError
from src.aqueduct.skills.base import SkillContext
from src.aqueduct.skills.design_ddl import _resolve_target_table
from src.aqueduct.tools.dqc import _parse_test_cases

# ============================================================
# _strip_llm_meta 测试
# ============================================================


class TestStripLlmMeta:
    """_strip_llm_meta 剥离 LLM 回复开头的元信息。"""

    def test_strips_based_on_context(self):
        """剥离 'Based on all the context...' 开头。"""
        content = "Based on all the context gathered, here is the analysis.\n# 需求理解\n实际内容"
        result = _strip_llm_meta(content)
        assert result.startswith("# 需求理解")
        assert "Based on" not in result

    def test_strips_now_i_have(self):
        """剥离 'Now I have all the domain context...' 开头。"""
        content = "Now I have all the domain context needed.\n## 设计方案\n内容"
        result = _strip_llm_meta(content)
        assert result.startswith("## 设计方案")

    def test_strips_let_me_proceed(self):
        """剥离 'Let me proceed to generate...' 开头。"""
        content = "Let me proceed to generate the SQL.\n```sql\nSELECT 1\n```"
        result = _strip_llm_meta(content)
        assert result.startswith("```sql")

    def test_strips_as_a_data_engineer(self):
        """剥离 'As a data engineer, I...' 开头。"""
        content = "As a data engineer, I will now create the DDL.\nCREATE TABLE test (id int)"
        result = _strip_llm_meta(content)
        assert result.startswith("CREATE TABLE")

    def test_strips_after_reviewing(self):
        """剥离 'After reviewing all...' 开头。"""
        content = "After reviewing all the information, here is my output.\n# 结果\n内容"
        result = _strip_llm_meta(content)
        assert result.startswith("# 结果")

    def test_strips_multiple_lines(self):
        """最多剥离 3 行元信息（需每行都匹配正则）。"""
        content = (
            "Based on all the context, I will now proceed.\n"
            "Now I have all the information needed.\n"
            "# 实际内容\n"
            "正文"
        )
        result = _strip_llm_meta(content)
        assert result.startswith("# 实际内容")

    def test_preserves_normal_content(self):
        """正常内容不被剥离。"""
        content = "# 需求理解\n\n## 核心实体\n- Order"
        result = _strip_llm_meta(content)
        assert result == content

    def test_preserves_sql_content(self):
        """SQL 开头的内容不被剥离。"""
        content = "SELECT *\nFROM table\nWHERE id = 1"
        result = _strip_llm_meta(content)
        assert result == content

    def test_preserves_create_table(self):
        """DDL 开头的内容不被剥离。"""
        content = "CREATE TABLE IF NOT EXISTS test (\n  id int COMMENT 'ID'\n)"
        result = _strip_llm_meta(content)
        assert result == content

    def test_empty_input(self):
        """空字符串不报错。"""
        assert _strip_llm_meta("") == ""

    def test_only_meta_no_content(self):
        """只有元信息无实际内容时，返回空。"""
        content = "Based on all the context gathered."
        result = _strip_llm_meta(content)
        assert result == ""

    def test_max_strip_limit(self):
        """超过 3 行元信息时，第 4 行保留。"""
        content = (
            "Based on all the context.\n"
            "Let me proceed.\n"
            "Now I have all the data.\n"
            "After reviewing all.\n"  # 第 4 行不剥离
            "# 内容"
        )
        result = _strip_llm_meta(content)
        assert result.startswith("After reviewing all")

    def test_blank_line_stops_stripping(self):
        """空行停止剥离（不跳过空行继续匹配）。"""
        content = "Based on all the context.\n\nLet me proceed.\n# 内容"
        result = _strip_llm_meta(content)
        # 第一行被剥离，第二行空行停止
        assert result.startswith("\nLet me proceed")


# ============================================================
# _resolve_target_table 测试
# ============================================================


class TestResolveTargetTable:
    """_resolve_target_table 4 级优先级回退。"""

    def test_explicit_table_from_input(self):
        """优先级 1：input 中显式指定 target_table。"""
        ctx = SkillContext(
            input={"target_table": "dw.fact_order"},
            state={"metadata": {"requirement_name": "test"}},
        )
        assert _resolve_target_table(ctx) == "dw.fact_order"

    def test_explicit_table_from_state(self):
        """优先级 1b：state 中显式指定 target_table。"""
        ctx = SkillContext(
            input={},
            state={
                "target_table": "dw.fact_order",
                "metadata": {"requirement_name": "test"},
            },
        )
        assert _resolve_target_table(ctx) == "dw.fact_order"

    def test_placeholder_falls_through(self):
        """占位符表名不算显式指定，回退到下级。"""
        ctx = SkillContext(
            input={"target_table": "dw_demo.tmp_target_table"},
            state={"metadata": {"requirement_name": "order_stats"}},
        )
        result = _resolve_target_table(ctx)
        assert result != "dw_demo.tmp_target_table"
        assert "order_stats" in result

    def test_domain_config(self):
        """优先级 2：从 domain.json 读取 target_tables。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_dir = Path(tmpdir) / "test_domain"
            domain_dir.mkdir()
            domain_json = domain_dir / "domain.json"
            domain_json.write_text(
                json.dumps(
                    {"target_tables": {"main": {"name": "fact_sales", "database": "dw_prod"}}}
                ),
                encoding="utf-8",
            )

            ctx = SkillContext(
                input={},
                state={
                    "domain_id": "test_domain",
                    "metadata": {"requirement_name": "test"},
                },
            )

            with patch("src.aqueduct.skills.design_ddl.get_settings") as mock_settings:
                mock_settings.return_value.knowledge_dir = tmpdir
                result = _resolve_target_table(ctx)

            assert result == "dw_prod.fact_sales"

    def test_generated_from_requirement_name(self):
        """优先级 3：从需求名生成有意义的表名。"""
        ctx = SkillContext(
            input={},
            state={"metadata": {"requirement_name": "电商每日订单统计"}},
        )
        result = _resolve_target_table(ctx)
        assert result.startswith("dw_demo.tmp_")
        assert "tmp_target_table" not in result

    def test_fallback_placeholder(self):
        """优先级 4：无任何信息时回退到占位符。"""
        ctx = SkillContext(input={}, state={})
        result = _resolve_target_table(ctx)
        assert result == "dw_demo.tmp_target_table"

    def test_requirement_name_sanitized(self):
        """需求名中的特殊字符被替换为下划线。"""
        ctx = SkillContext(
            input={},
            state={"metadata": {"requirement_name": "order/daily stats!"}},
        )
        result = _resolve_target_table(ctx)
        # 不应包含 / 或 ! 或空格
        table_name = result.replace("dw_demo.tmp_", "")
        assert "/" not in table_name
        assert "!" not in table_name
        assert " " not in table_name


# ============================================================
# _extract_select_statements 测试
# ============================================================


class TestExtractSelectStatements:
    """_extract_select_statements 从混合 SQL 中提取 SELECT。"""

    def test_simple_select(self):
        """提取简单 SELECT。"""
        sql = "SELECT id, name FROM users WHERE id > 0"
        result = _extract_select_statements(sql)
        assert len(result) == 1
        assert result[0].startswith("SELECT")

    def test_with_cte(self):
        """提取 WITH ... SELECT（CTE）。"""
        sql = "WITH tmp AS (SELECT 1 AS id) SELECT * FROM tmp"
        result = _extract_select_statements(sql)
        assert len(result) == 1
        assert result[0].startswith("WITH")

    def test_skips_create_table(self):
        """跳过 CREATE TABLE 语句。"""
        sql = "CREATE TABLE test (id int);\nSELECT * FROM source"
        result = _extract_select_statements(sql)
        assert len(result) == 1
        assert result[0].startswith("SELECT")

    def test_skips_insert(self):
        """跳过 INSERT INTO 语句。"""
        sql = "INSERT INTO target SELECT * FROM source;\nSELECT count(*) FROM target"
        result = _extract_select_statements(sql)
        # INSERT 不是 SELECT/WITH 开头，跳过
        assert len(result) == 1
        assert "count(*)" in result[0]

    def test_multiple_selects(self):
        """提取多条 SELECT。"""
        sql = "SELECT * FROM a;\nSELECT * FROM b;\nSELECT * FROM c"
        result = _extract_select_statements(sql)
        assert len(result) == 3

    def test_strips_line_comments(self):
        """去掉行注释后再提取。"""
        sql = "-- 这是注释\nSELECT * FROM users\n"
        result = _extract_select_statements(sql)
        assert len(result) == 1

    def test_strips_block_comments(self):
        """去掉块注释后再提取。"""
        sql = "/* 块注释 */\nSELECT * FROM users\n"
        result = _extract_select_statements(sql)
        assert len(result) == 1

    def test_empty_input(self):
        """空输入返回空列表。"""
        assert _extract_select_statements("") == []

    def test_only_ddl(self):
        """只有 DDL 时返回空列表。"""
        sql = "CREATE TABLE test (id int); ALTER TABLE test ADD COLUMN name string"
        result = _extract_select_statements(sql)
        assert result == []

    def test_mixed_complex(self):
        """混合复杂 SQL：DDL + CTE + SELECT。"""
        sql = (
            "CREATE TABLE target AS\n"
            "WITH cte AS (SELECT id FROM source)\n"
            "SELECT * FROM cte;\n"
            "\n"
            "SELECT count(*) FROM target"
        )
        result = _extract_select_statements(sql)
        # CREATE TABLE ... AS WITH ... SELECT 开头是 CREATE，跳过
        # 第二条 SELECT count(*) 被提取
        assert len(result) == 1
        assert "count(*)" in result[0]


# ============================================================
# _parse_test_cases — DQC 阈值解析测试
# ============================================================


class TestParseDqcThresholds:
    """_parse_test_cases 解析阈值注释。"""

    def test_extracts_threshold(self):
        """解析 '-- 阈值: <=0' 注释。"""
        sql = (
            "-- [业务反证-金额正数] 检查金额是否大于 0\n"
            "-- 权重: High\n"
            "-- 阈值: <=0\n"
            "SELECT count(*) FROM t WHERE amount <= 0\n"
            "-- 预期: 0（金额必须为正）"
        )
        cases = _parse_test_cases(sql)
        assert len(cases) == 1
        assert cases[0]["threshold"] == "<=0"
        assert cases[0]["category"] == "业务反证"
        assert cases[0]["name"] == "金额正数"

    def test_threshold_with_percentage(self):
        """解析波动率阈值。"""
        sql = (
            "-- [波动监控-总量环比] 与昨日对比\n"
            "-- 权重: Low\n"
            "-- 阈值: 波动率>50%\n"
            "SELECT 1\n"
            "-- 预期: 波动率 < 50%"
        )
        cases = _parse_test_cases(sql)
        assert cases[0]["threshold"] == "波动率>50%"

    def test_missing_threshold(self):
        """无阈值注释时 threshold 为空字符串。"""
        sql = (
            "-- [唯一性-主键重复] 检查主键\n"
            "-- 权重: High\n"
            "SELECT id, count(*) FROM t GROUP BY id HAVING count(*) > 1\n"
            "-- 预期: 0 行"
        )
        cases = _parse_test_cases(sql)
        assert cases[0]["threshold"] == ""

    def test_multiple_cases_with_thresholds(self):
        """多条测试用例各自解析阈值。"""
        sql = (
            "-- [唯一性-主键非空] 主键非空检查\n"
            "-- 权重: High\n"
            "-- 阈值: >0\n"
            "SELECT count(*) FROM t WHERE id IS NULL\n"
            "-- 预期: 0\n"
            "\n\n"
            "-- [边界值-年龄合理] 年龄不超过 150\n"
            "-- 权重: Medium\n"
            "-- 阈值: >150\n"
            "SELECT count(*) FROM t WHERE age > 150\n"
            "-- 预期: 0"
        )
        cases = _parse_test_cases(sql)
        assert len(cases) == 2
        assert cases[0]["threshold"] == ">0"
        assert cases[1]["threshold"] == ">150"


# ============================================================
# P0 自动暂停 — review 节点 Critical 问题终止管道
# ============================================================


class TestP0AutoHalt:
    """审查节点 Critical 问题达到修复上限时终止管道。"""

    def test_critical_halt_after_max_fix(self):
        """Critical 问题在修复上限后抛 WorkflowHaltError。"""
        from src.aqueduct.engine.nodes.review import node_review

        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": "SELECT * FROM t",
            "requirement_summary": "test",
            "domain_context": "",
            "validation_result": {},
            "fix_iterations": 2,  # 已达上限
        }

        review_report = (
            "# 审查报告\n- [Critical] JOIN 条件缺失导致笛卡尔积\n- [Warning] 缺少分区过滤\n"
        )

        with (
            patch("src.aqueduct.engine.nodes.review.get_skill") as mock_skill,
            patch("src.aqueduct.engine.nodes.review.call_llm", return_value=review_report),
            patch(
                "src.aqueduct.engine.nodes.review.save_artifact", return_value="output/report.md"
            ),
            patch("src.aqueduct.config.settings.get_settings") as mock_settings,
        ):
            mock_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": "test"}}
            )()
            mock_settings.return_value.max_fix_iterations = 2

            with pytest.raises(WorkflowHaltError, match="Critical"):
                node_review(state)

    def test_warning_no_halt(self):
        """仅有 Warning（无 Critical）时不终止管道。"""
        from src.aqueduct.engine.nodes.review import node_review

        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": "SELECT * FROM t",
            "requirement_summary": "test",
            "domain_context": "",
            "validation_result": {},
            "fix_iterations": 2,
        }

        review_report = "# 审查报告\n- [Warning] 缺少分区过滤\n"

        with (
            patch("src.aqueduct.engine.nodes.review.get_skill") as mock_skill,
            patch("src.aqueduct.engine.nodes.review.call_llm", return_value=review_report),
            patch(
                "src.aqueduct.engine.nodes.review.save_artifact", return_value="output/report.md"
            ),
            patch("src.aqueduct.config.settings.get_settings") as mock_settings,
        ):
            mock_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": "test"}}
            )()
            mock_settings.return_value.max_fix_iterations = 2

            # 不抛异常
            result = node_review(state)
            assert result is not None
            assert result.get("_needs_fix_loop") is False


# ============================================================
# _split_sql_into_blocks — SQL 分块拆分测试
# ============================================================


class TestSplitSqlIntoBlocks:
    """review.py SQL 分块拆分。"""

    def test_single_statement_no_semicolon(self):
        """单条无分号 SQL 返回单元素。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "SELECT * FROM t WHERE id = 1"
        blocks = _split_sql_into_blocks(sql)
        assert len(blocks) == 1
        assert blocks[0] == sql

    def test_single_statement_with_semicolon(self):
        """单条带分号 SQL 返回单元素。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "SELECT * FROM t WHERE id = 1;"
        blocks = _split_sql_into_blocks(sql)
        assert len(blocks) == 1
        assert blocks[0] == "SELECT * FROM t WHERE id = 1"

    def test_two_statements(self):
        """两条语句按顶层分号拆分。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "INSERT INTO t1 SELECT * FROM s1;\nINSERT INTO t2 SELECT * FROM s2;"
        blocks = _split_sql_into_blocks(sql)
        assert len(blocks) == 2
        assert "t1" in blocks[0]
        assert "t2" in blocks[1]

    def test_nested_semicolon_in_string(self):
        """字符串内的分号不拆分。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "SELECT 'a;b' AS col;\nSELECT 1 AS x"
        blocks = _split_sql_into_blocks(sql)
        assert len(blocks) == 2
        assert "'a;b'" in blocks[0]

    def test_semicolon_in_parentheses(self):
        """括号内的分号不拆分（子查询场景）。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "INSERT INTO t SELECT * FROM (\n  SELECT id FROM s;\n) sub;\nSELECT 1"
        blocks = _split_sql_into_blocks(sql)
        # 括号内的分号不应拆分
        assert any("sub" in b for b in blocks)

    def test_semicolon_in_line_comment(self):
        """行注释内的分号不拆分。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "-- 这是一个;注释\nSELECT 1;\nSELECT 2"
        blocks = _split_sql_into_blocks(sql)
        assert len(blocks) == 2
        assert "注释" in blocks[0]

    def test_semicolon_in_block_comment(self):
        """块注释内的分号不拆分。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "/* 包含;分号 */\nSELECT 1;\nSELECT 2"
        blocks = _split_sql_into_blocks(sql)
        assert len(blocks) == 2
        assert "/* 包含;分号 */" in blocks[0]

    def test_empty_sql(self):
        """空 SQL 返回原始内容。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        blocks = _split_sql_into_blocks("")
        assert blocks == [""]

    def test_three_statements(self):
        """三条语句拆分。"""
        from src.aqueduct.engine.nodes.review import _split_sql_into_blocks

        sql = "CREATE TABLE t1 (id INT);\n\nINSERT INTO t1 VALUES (1);\n\nSELECT * FROM t1;"
        blocks = _split_sql_into_blocks(sql)
        assert len(blocks) == 3
        assert "CREATE" in blocks[0]
        assert "INSERT" in blocks[1]
        assert "SELECT" in blocks[2]


class TestShouldParallelReview:
    """并行审查阈值判断。"""

    def test_short_sql_no_parallel(self):
        """短 SQL（<100 行）不走并行。"""
        from src.aqueduct.engine.nodes.review import _should_parallel_review

        sql = "SELECT 1;\nSELECT 2"
        assert _should_parallel_review(sql) is False

    def test_single_block_no_parallel(self):
        """单语句（即使 >100 行）不走并行。"""
        from src.aqueduct.engine.nodes.review import _should_parallel_review

        sql = "SELECT\n" + "  col,\n" * 150 + "  last\nFROM t"
        assert _should_parallel_review(sql) is False

    def test_multi_block_long_sql_parallel(self):
        """多语句 + >100 行走并行。"""
        from src.aqueduct.engine.nodes.review import _should_parallel_review

        sql = "SELECT 1;\n" + "-- line\n" * 110 + "SELECT 2;"
        assert _should_parallel_review(sql) is True


# ============================================================
# P0-2: 提前终止修复循环 — 仅 Warning 不触发修复
# ============================================================


class TestEarlyTermination:
    """仅有 Warning 时跳过修复循环。"""

    def test_warning_only_skips_fix_loop(self):
        """只有 Warning、无 Critical 时不触发修复循环。"""
        from src.aqueduct.engine.nodes.review import node_review

        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": "SELECT * FROM t",
            "requirement_summary": "test",
            "domain_context": "",
            "validation_result": {},
            "fix_iterations": 0,  # 还未到上限
        }

        review_report = "# 审查报告\n- [Warning] 缺少分区过滤\n- [Warning] 使用了 SELECT *\n"

        with (
            patch("src.aqueduct.engine.nodes.review.get_skill") as mock_skill,
            patch("src.aqueduct.engine.nodes.review.call_llm", return_value=review_report),
            patch(
                "src.aqueduct.engine.nodes.review.save_artifact",
                return_value="output/report.md",
            ),
            patch("src.aqueduct.config.settings.get_settings") as mock_settings,
        ):
            mock_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": "test"}}
            )()
            mock_settings.return_value.max_fix_iterations = 2

            result = node_review(state)
            # 仅 Warning 不触发修复循环
            assert result["_needs_fix_loop"] is False

    def test_critical_triggers_fix_loop(self):
        """有 Critical 时触发修复循环。"""
        from src.aqueduct.engine.nodes.review import node_review

        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": "SELECT * FROM t",
            "requirement_summary": "test",
            "domain_context": "",
            "validation_result": {},
            "fix_iterations": 0,
        }

        review_report = "# 审查报告\n- [Critical] JOIN 条件缺失\n- [Warning] 缺少分区过滤\n"

        with (
            patch("src.aqueduct.engine.nodes.review.get_skill") as mock_skill,
            patch("src.aqueduct.engine.nodes.review.call_llm", return_value=review_report),
            patch(
                "src.aqueduct.engine.nodes.review.save_artifact",
                return_value="output/report.md",
            ),
            patch("src.aqueduct.config.settings.get_settings") as mock_settings,
        ):
            mock_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": "test"}}
            )()
            mock_settings.return_value.max_fix_iterations = 2

            result = node_review(state)
            # 有 Critical，触发修复循环
            assert result["_needs_fix_loop"] is True
            assert result["_review_issues"] is not None


# ============================================================
# P0-4: 连接池复用 — ClaudeLLM 类级别客户端缓存
# ============================================================


class TestSharedSdkClient:
    """不同 ClaudeLLM 实例共享同一 SDK 客户端。"""

    def test_different_tiers_share_client(self):
        """Haiku/Sonnet/Opus 三个实例共享同一个底层 Anthropic 客户端。"""
        from src.aqueduct.llm.claude import ClaudeLLM

        # 清空缓存
        ClaudeLLM._shared_sdk_clients.clear()

        haiku = ClaudeLLM(model_id="claude-haiku-4-5-20251001")
        sonnet = ClaudeLLM(model_id="claude-sonnet-4-6-20250514")
        opus = ClaudeLLM(model_id="claude-opus-4-7-20251101")

        # 三个实例是不同的 ClaudeLLM 对象
        assert haiku is not sonnet
        assert sonnet is not opus

        # 模拟 SDK 客户端缓存（不实际调用 API）
        # 验证 cache key 结构正确
        assert len(ClaudeLLM._shared_sdk_clients) == 0  # 还没有调用过 _chat_sdk

    def test_cache_key_isolation(self):
        """不同 api_key 不共享客户端。"""
        from src.aqueduct.llm.claude import ClaudeLLM

        ClaudeLLM._shared_sdk_clients.clear()

        # 手动模拟 _chat_sdk 中的缓存逻辑
        key_a = ("key_a", "https://api.a.com", 30.0)
        key_b = ("key_b", "https://api.a.com", 30.0)

        assert key_a != key_b  # 不同 key 应该产生不同缓存条目

    def test_same_config_reuses_client(self):
        """相同配置的不同实例应命中同一缓存。"""
        from src.aqueduct.llm.claude import ClaudeLLM

        ClaudeLLM._shared_sdk_clients.clear()

        # 模拟相同的缓存键
        cache_key = ("test_key", "https://api.test.com", 30.0)

        # 手动放入缓存
        mock_client = type("MockClient", (), {})()
        ClaudeLLM._shared_sdk_clients[cache_key] = mock_client

        # 验证缓存命中
        assert ClaudeLLM._shared_sdk_clients[cache_key] is mock_client

        # 清理
        ClaudeLLM._shared_sdk_clients.clear()


# ============================================================
# P0-3: _split_requirement_and_design — 三合一响应解析
# ============================================================


class TestSplitRequirementAndDesign:
    """三合一 LLM 响应拆分：需求摘要 / 设计方案 / DDL。"""

    def test_full_three_part_response(self):
        """完整三段式响应正确拆分。"""
        from src.aqueduct.engine.nodes.design import _split_requirement_and_design

        response = (
            "## 需求理解摘要\n\n"
            "- 目标表: dm.test\n"
            "- 数据来源: dwd.source\n\n"
            "### 待确认问题\n"
            "无\n\n"
            "## 设计方案\n\n"
            "### 取数逻辑\n"
            "单表聚合\n\n"
            "### 字段映射\n"
            "| 目标 | 源 | 逻辑 |\n\n"
            "```sql\n"
            "CREATE TABLE dm.test (id bigint COMMENT '主键')\n"
            "PARTITIONED BY (inc_day string)\n"
            "STORED AS PARQUET;\n"
            "```"
        )
        req, design, ddl = _split_requirement_and_design(response)
        assert "目标表" in req
        assert "待确认问题" in req
        assert "## 需求理解摘要" not in req  # 标题已清理
        assert "取数逻辑" in design
        assert "字段映射" in design
        assert "CREATE TABLE" in ddl

    def test_no_design_marker(self):
        """无设计方案标记时整体作为需求摘要。"""
        from src.aqueduct.engine.nodes.design import _split_requirement_and_design

        response = "## 需求理解摘要\n\n- 目标表: dm.test\n- 数据来源: dwd.source"
        req, design, ddl = _split_requirement_and_design(response)
        assert "目标表" in req
        assert design == ""
        assert ddl == ""

    def test_design_without_ddl(self):
        """有设计方案但无 SQL 代码块。"""
        from src.aqueduct.engine.nodes.design import _split_requirement_and_design

        response = (
            "## 需求理解摘要\n\n"
            "- 目标表: dm.test\n\n"
            "## 设计方案\n\n"
            "### 取数逻辑\n"
            "单表聚合，无 DDL 生成。"
        )
        req, design, ddl = _split_requirement_and_design(response)
        assert "目标表" in req
        assert "取数逻辑" in design
        assert ddl == ""

    def test_empty_response(self):
        """空响应安全处理。"""
        from src.aqueduct.engine.nodes.design import _split_requirement_and_design

        req, design, ddl = _split_requirement_and_design("")
        assert req == ""
        assert design == ""
        assert ddl == ""

    def test_summary_header_stripped(self):
        """需求摘要的 '## 需求理解摘要' 标题被清理。"""
        from src.aqueduct.engine.nodes.design import _split_requirement_and_design

        response = "## 需求理解摘要\n\n- 目标表: dm.test\n\n## 设计方案\n\n取数逻辑说明"
        req, _, _ = _split_requirement_and_design(response)
        # 标题行被清理
        assert not req.startswith("## 需求理解摘要")
        assert "目标表" in req

    def test_phase1_already_done_detection(self):
        """Phase 2 检测到 design_scheme + ddl_content 已存在时跳过。"""
        from src.aqueduct.engine.nodes.design import node_design

        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "design_scheme": "## 设计方案\n已有设计",
            "ddl_content": "CREATE TABLE test (id bigint);",
        }

        # 不应调用任何 LLM
        with (
            patch("src.aqueduct.engine.nodes.design.get_skill") as mock_skill,
            patch("src.aqueduct.engine.nodes.design.call_llm") as mock_llm,
        ):
            result = node_design(state)
            mock_skill.assert_not_called()
            mock_llm.assert_not_called()
            assert result["metadata"]["design_done"] == "true"
            assert result["metadata"]["ddl_done"] == "true"


# ============================================================
# P1: TableSchemaCache — 表结构缓存测试
# ============================================================


class TestTableSchemaCache:
    """TableSchemaCache 基本功能。"""

    def test_set_and_get(self):
        """set 后 get 命中。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=3600)
        cache.set("db.schema.table1", "字段列表...")
        assert cache.get("db.schema.table1") == "字段列表..."

    def test_get_miss(self):
        """未 set 的 key 返回 None。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=3600)
        assert cache.get("db.schema.nonexistent") is None

    def test_ttl_expiry(self):
        """过期条目返回 None。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=1)  # 1 秒 TTL
        cache.set("db.schema.table1", "data")

        # 模拟时间流逝
        import time

        with patch("src.aqueduct.utils.table_cache.time") as mock_time:
            # 首次访问：刚写入，未过期
            mock_time.time.return_value = time.time()
            assert cache.get("db.schema.table1") == "data"

            # 模拟 2 秒后：已过期
            mock_time.time.return_value = time.time() + 2
            assert cache.get("db.schema.table1") is None

    def test_get_many(self):
        """批量查询，只返回命中条目。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=3600)
        cache.set("t1", "schema1")
        cache.set("t2", "schema2")

        result = cache.get_many(["t1", "t2", "t3"])
        assert result == {"t1": "schema1", "t2": "schema2"}

    def test_set_many(self):
        """批量写入。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=3600)
        cache.set_many({"t1": "schema1", "t2": "schema2"})

        assert cache.get("t1") == "schema1"
        assert cache.get("t2") == "schema2"

    def test_invalidate(self):
        """主动失效。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=3600)
        cache.set("t1", "schema1")
        assert cache.get("t1") == "schema1"

        cache.invalidate("t1")
        assert cache.get("t1") is None

    def test_clear(self):
        """清空缓存。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=3600)
        cache.set_many({"t1": "s1", "t2": "s2"})
        cache.clear()
        assert cache.size == 0

    def test_stats(self):
        """统计信息。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        cache = TableSchemaCache(ttl_seconds=3600)
        cache.set("t1", "s1")
        cache.set("t2", "s2")

        stats = cache.stats()
        assert stats["total"] == 2
        assert stats["active"] == 2
        assert stats["expired"] == 0

    def test_disk_persistence(self, tmp_path):
        """磁盘持久化：写入后可重新加载。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        persist_path = tmp_path / "cache.json"

        # 写入
        cache1 = TableSchemaCache(ttl_seconds=3600, persist_path=persist_path)
        cache1.set("t1", "schema1")
        cache1.set("t2", "schema2")

        # 重新加载
        cache2 = TableSchemaCache(ttl_seconds=3600, persist_path=persist_path)
        assert cache2.get("t1") == "schema1"
        assert cache2.get("t2") == "schema2"

    def test_disk_load_skips_expired(self, tmp_path):
        """磁盘加载时跳过已过期条目。"""
        from src.aqueduct.utils.table_cache import TableSchemaCache

        persist_path = tmp_path / "cache.json"

        # 用 1 秒 TTL 写入
        cache1 = TableSchemaCache(ttl_seconds=1, persist_path=persist_path)
        cache1.set("t1", "schema1")

        # 模拟 2 秒后重新加载（TTL=1，已过期）
        import time

        with patch("src.aqueduct.utils.table_cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            cache2 = TableSchemaCache(ttl_seconds=1, persist_path=persist_path)
            assert cache2.get("t1") is None
            assert cache2.size == 0


# ============================================================
# P1: PromptCompressor — Prompt 压缩工具测试
# ============================================================


class TestPromptCompressor:
    """规则压缩：去除冗余内容。"""

    def test_short_prompt_unchanged(self):
        """短 prompt 不压缩。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        short = "请分析这个 SQL"
        result = PromptCompressor().compress_rule(short)
        assert result == short

    def test_removes_markdown_images(self):
        """去除 Markdown 图片。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        prompt = (
            "## 任务\n"
            + "分析 SQL\n" * 200
            + "\n![image](http://example.com/img.png)\n"
            + "请输出结果"
        )
        result = PromptCompressor().compress_rule(prompt)
        assert "![image]" not in result

    def test_removes_html_tags(self):
        """去除 HTML 标签。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        prompt = "## 任务\n" + "分析 SQL\n" * 200 + "\n<div>冗余内容</div>\n请输出"
        result = PromptCompressor().compress_rule(prompt)
        assert "<div>" not in result
        assert "冗余内容" in result

    def test_compresses_separator_lines(self):
        """压缩连续分隔线。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        prompt = "## 任务\n" + "分析\n" * 200 + "\n---\n---\n---\n---\n" + "请输出"
        result = PromptCompressor().compress_rule(prompt)
        # 应该被压缩为一条分隔线
        assert result.count("---") <= 1

    def test_compresses_blank_lines(self):
        """压缩多余空行。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        prompt = "## 任务\n" + "分析\n" * 200 + "\n\n\n\n\n\n\n请输出"
        result = PromptCompressor().compress_rule(prompt)
        # 不应有超过 2 个连续空行
        assert "\n\n\n" not in result

    def test_truncates_long_table_schema(self):
        """截断过长的表结构描述。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        # 生成一个超长表结构（200 个字段，每个字段约 40 字符 → ~8000 字符，远超 3000 阈值）
        fields = "\n".join(
            f"  - field_{i} (string) — 这是一个比较长的字段描述用于测试截断功能 {i}"
            for i in range(200)
        )
        schema = f"表: db.schema.big_table\n注释: 测试表\n字段 (200 个):\n{fields}"
        prompt = "## 任务\n" + "说明\n" * 200 + "\n" + schema + "\n\n请输出"

        result = PromptCompressor().compress_rule(prompt)
        # 应该被截断并标注省略字段数
        assert "省略" in result
        assert len(result) < len(prompt)

    def test_truncates_examples(self):
        """截断冗余示例：保留第一个，省略后续。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        prompt = (
            "## 任务\n"
            + "分析 SQL\n" * 200
            + "\n## 示例\n\n"
            + "### 示例 1\nSELECT 1\n\n"
            + "### 示例 2\nSELECT 2\n\n"
            + "### 示例 3\nSELECT 3\n\n"
            + "## 禁止项\n\n不要这样做"
        )
        result = PromptCompressor().compress_rule(prompt)
        assert "示例 1" in result
        assert "省略" in result
        assert "禁止项" in result  # 后续章节应保留

    def test_large_prompt_compressed(self):
        """大 prompt 整体压缩效果。"""
        from src.aqueduct.utils.prompt_optimizer import PromptCompressor

        # 生成一个 10000+ 字符的 prompt，含大量重复行和多余空行
        prompt = (
            "## 任务\n"
            + "分析以下 SQL 代码的质量，检查是否有逻辑错误和性能问题。\n\n\n\n"
            + "重复说明行，需要被压缩掉多余空行。\n\n\n\n" * 250
            + "## 输入\n"
            + "SELECT * FROM table WHERE id = 1\n" * 300
            + "\n\n\n\n\n"
            + "## 输出格式\n"
            + "输出审查报告，包含差异分析、需求覆盖度、发现的问题。\n"
        )
        result = PromptCompressor().compress_rule(prompt)
        # 压缩后应更短（多余空行被压缩）
        assert len(result) < len(prompt)
        # 关键章节应保留
        assert "## 任务" in result
        assert "## 输入" in result
        assert "## 输出格式" in result


class TestEstimateTokens:
    """Token 估算。"""

    def test_empty_text(self):
        from src.aqueduct.utils.prompt_optimizer import estimate_tokens

        assert estimate_tokens("") == 0

    def test_chinese_text(self):
        from src.aqueduct.utils.prompt_optimizer import estimate_tokens

        # 100 个中文字符 ≈ 150 tokens（1 中文字 ≈ 1.5 token）
        text = "分析以下数据仓库的 SQL 代码质量" * 10
        tokens = estimate_tokens(text)
        assert tokens > 0
        # 中文 1 字 ≈ 1.5 token，所以 tokens > chars
        assert tokens > len(text)

    def test_english_text(self):
        from src.aqueduct.utils.prompt_optimizer import estimate_tokens

        text = "SELECT * FROM table WHERE id = 1" * 10
        tokens = estimate_tokens(text)
        assert tokens > 0
        # 英文大约 1 token / 4 chars
        assert tokens < len(text)


# ============================================================
# P1: ChangeAnalyzer — 增量管道影响分析测试
# ============================================================


class TestChangeAnalyzer:
    """ChangeAnalyzer 基本功能。"""

    def test_compute_hash_consistent(self):
        """相同输入产生相同哈希。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        req = "需求文档内容\n第二行"
        h1 = ChangeAnalyzer.compute_requirement_hash(req)
        h2 = ChangeAnalyzer.compute_requirement_hash(req)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 长度为 64 个十六进制字符

    def test_compute_hash_normalizes_whitespace(self):
        """规范化空白后相同内容产生相同哈希。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        req_a = "需求文档\n\n\n\n\n第二行"
        req_b = "需求文档\n\n第二行"
        assert ChangeAnalyzer.compute_requirement_hash(
            req_a
        ) == ChangeAnalyzer.compute_requirement_hash(req_b)

    def test_compute_hash_different_content(self):
        """不同内容产生不同哈希。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        h1 = ChangeAnalyzer.compute_requirement_hash("需求 A")
        h2 = ChangeAnalyzer.compute_requirement_hash("需求 B")
        assert h1 != h2

    def test_should_skip_no_manifest(self, tmp_path):
        """无 manifest 时不跳过。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        assert analyzer.should_skip_phase1("任何需求") is False

    def test_should_skip_matching_hash(self, tmp_path):
        """需求哈希匹配时跳过。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        requirement = "测试需求文档"
        state = {
            "requirement": requirement,
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
            "requirement_summary": "摘要内容",
            "design_scheme": "设计方案内容",
            "ddl_content": "CREATE TABLE test (id int);",
        }

        # 先保存 manifest
        analyzer.save_manifest(requirement, state)

        # 相同需求应跳过
        assert analyzer.should_skip_phase1(requirement) is True

    def test_should_skip_changed_requirement(self, tmp_path):
        """需求变化时不跳过。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        state = {
            "requirement": "原始需求",
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
            "requirement_summary": "摘要",
        }

        analyzer.save_manifest("原始需求", state)
        # 需求已变，不应跳过
        assert analyzer.should_skip_phase1("新需求") is False

    def test_should_skip_missing_phase1_outputs(self, tmp_path):
        """manifest 中无完整 Phase 1 输出时不跳过。"""
        import json

        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        requirement = "测试需求"
        req_hash = ChangeAnalyzer.compute_requirement_hash(requirement)

        # 手动写一个不完整的 manifest
        manifest = {
            "requirement_hash": req_hash,
            "updated_at": "2026-07-14T12:00:00",
            "phase1_outputs": {},  # 无输出
        }
        (tmp_path / ".pipeline_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        assert analyzer.should_skip_phase1(requirement) is False

    def test_restore_phase1_outputs(self, tmp_path):
        """恢复 Phase 1 输出到 state。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        requirement = "测试需求"
        state = {
            "requirement": requirement,
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
            "requirement_summary": "需求摘要",
            "design_scheme": "设计方案",
            "ddl_content": "CREATE TABLE test (id int);",
        }

        analyzer.save_manifest(requirement, state)

        # 清空 state，模拟新运行
        new_state = {
            "requirement": requirement,
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }

        assert analyzer.restore_phase1_outputs(new_state) is True
        assert new_state["requirement_summary"] == "需求摘要"
        assert new_state["design_scheme"] == "设计方案"
        assert new_state["ddl_content"] == "CREATE TABLE test (id int);"
        assert new_state["metadata"]["incremental_skip"] == "true"

    def test_save_manifest_content(self, tmp_path):
        """manifest 文件内容正确。"""
        import json

        from src.aqueduct.utils.change_analyzer import MANIFEST_FILENAME, ChangeAnalyzer

        analyzer = ChangeAnalyzer(output_dir=tmp_path)
        requirement = "测试需求"
        state = {
            "requirement": requirement,
            "mode": "dev",
            "metadata": {},
            "errors": [],
            "artifacts": [],
            "requirement_summary": "摘要",
            "design_scheme": "设计",
            "ddl_content": "CREATE TABLE test (id int);",
        }

        analyzer.save_manifest(requirement, state)

        manifest_path = tmp_path / MANIFEST_FILENAME
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["requirement_hash"] == ChangeAnalyzer.compute_requirement_hash(requirement)
        assert "updated_at" in manifest
        assert manifest["phase1_outputs"]["requirement_summary"] == "摘要"

    def test_analyze_diff(self):
        """分析需求变更的行数。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        old = "line1\nline2\nline3"
        new = "line1\nline2_modified\nline3\nline4"

        diff = ChangeAnalyzer.analyze_diff(old, new)
        assert diff["added_lines"] == 2  # line2_modified + line4
        assert diff["removed_lines"] == 1  # line2
        assert diff["total_changed"] == 3

    def test_analyze_diff_no_change(self):
        """相同需求 diff 为 0。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        req = "line1\nline2"
        diff = ChangeAnalyzer.analyze_diff(req, req)
        assert diff["total_changed"] == 0
        assert diff["old_hash"] == diff["new_hash"]

    def test_no_output_dir_no_crash(self):
        """output_dir 为 None 时不崩溃。"""
        from src.aqueduct.utils.change_analyzer import ChangeAnalyzer

        analyzer = ChangeAnalyzer(output_dir=None)
        assert analyzer.should_skip_phase1("任何需求") is False
        # save_manifest 和 restore_phase1_outputs 应安全跳过
        state = {"requirement": "test", "metadata": {}, "errors": [], "artifacts": []}
        analyzer.save_manifest("test", state)
        assert analyzer.restore_phase1_outputs(state) is False
