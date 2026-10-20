"""Phase 4.5 审查设计上下文注入测试（TODO-5：design_scheme + ddl_content 直达审查）。

背景（CLI vs Skill 质量差距根因分析）：CLI 管道的 Phase 4.5 审查只看得到
requirement_summary（Phase 1 的浓缩摘要）与 SQL 本身，审查器无法核对：
(a) SQL 是否实现了设计方案的取数逻辑/字段映射（design_scheme，Phase 2 产物）
(b) SELECT 字段与目标表 DDL 的字段名/类型/分区是否对齐（ddl_content，Phase 3 产物）
插件模式审查可见全部交付物，CLI 审查盲查——这是 CLI 产出比插件浅的因素之一。

契约（三处接线，同 TODO-1「node input + skill 解析 + 模板变量」模式）：
- review.py 两个 SkillContext 构造点（_build_chunk_prompt 分块 / _single_review
  单块）传入 design_scheme + ddl_content
- code_review.py 从 input/state 解析（input 优先，state 兜底），
  缺失渲染「未获取」占位（sql_develop 的 table_schemas 先例）
- code_review.tpl.md 输入区新增「设计方案」「表结构(DDL)」两个模板变量
"""

from __future__ import annotations

from src.aqueduct.engine.nodes.review import _build_chunk_prompt, _build_dimension_prompt
from src.aqueduct.skills.base import SkillContext
from src.aqueduct.skills.code_review import CodeReviewSkill

_DESIGN = "## 取数逻辑\n订单主表按城市聚合，zz_design_marker 仅统计有效订单"
_DDL = "CREATE TABLE zz_ddl_marker (id bigint, city string) PARTITIONED BY (inc_day string)"
_SQL = "SELECT city, count(1) FROM dw_demo.dwd_order_info_di WHERE inc_day = '${bizdate}' GROUP BY city"


def _make_state() -> dict:
    """Phase 4.5 时点的最小 state（Phase 2/3/4 产物均已就位）。"""
    return {
        "requirement_summary": "统计每日各城市订单量",
        "sql_content": _SQL,
        "domain_context": "",
        "validation_result": {"issues": []},
        "design_scheme": _DESIGN,
        "ddl_content": _DDL,
    }


class TestReviewSkillDesignContext:
    """skill 层：code_review 解析并渲染 design_scheme + ddl_content。"""

    def test_prompt_contains_design_scheme_and_ddl(self):
        skill = CodeReviewSkill()
        context = SkillContext(
            input={
                "requirement_desc": "统计每日各城市订单量",
                "sql_content": _SQL,
                "design_scheme": _DESIGN,
                "ddl_content": _DDL,
                "validation_result": {"issues": []},
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "zz_design_marker" in prompt, "设计方案应直达审查 prompt"
        assert "zz_ddl_marker" in prompt, "目标表 DDL 应直达审查 prompt"

    def test_skill_falls_back_to_state(self):
        """input 未传时从 state 兜底（input → state → default 链）。"""
        skill = CodeReviewSkill()
        context = SkillContext(
            input={"requirement_desc": "统计每日各城市订单量", "sql_content": _SQL},
            state={"design_scheme": _DESIGN, "ddl_content": _DDL},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "zz_design_marker" in prompt
        assert "zz_ddl_marker" in prompt

    def test_missing_context_renders_placeholder(self):
        """两处都没有 → 渲染「未获取」占位，不得泄漏 $变量 或留空白。"""
        skill = CodeReviewSkill()
        context = SkillContext(
            input={"requirement_desc": "统计每日各城市订单量", "sql_content": _SQL},
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "$design_scheme" not in prompt, "模板变量不得泄漏"
        assert "$ddl_content" not in prompt
        assert prompt.count("未获取") >= 2, "缺失的 design_scheme/ddl_content 各渲染一处占位"


class TestReviewNodeDesignContext:
    """node 层：两条审查路径（分块/单块）均传设计上下文。"""

    def test_chunk_prompt_includes_design_context(self):
        """分块审查路径：_build_chunk_prompt 构建的 prompt 含设计上下文。"""
        state = _make_state()
        prompt = _build_chunk_prompt(state, _SQL, chunk_index=1, total_chunks=2)
        assert "[审查第 1/2 个 SQL 语句块]" in prompt
        assert "zz_design_marker" in prompt
        assert "zz_ddl_marker" in prompt

    def test_single_review_passes_design_context(self):
        """单块审查路径（PERF-11 维度拆分）：维度 prompt 含设计上下文。"""
        state = _make_state()
        from src.aqueduct.engine.nodes.review import _REVIEW_DIMENSIONS

        for dim in _REVIEW_DIMENSIONS:
            prompt = _build_dimension_prompt(state, dim)
            assert prompt is not None
            assert "zz_design_marker" in prompt, f"维度 {dim['key']} 应含设计方案"
            assert "zz_ddl_marker" in prompt, f"维度 {dim['key']} 应含目标表 DDL"
            assert dim["name"] in prompt


_TWO_STMT_SQL = (
    "-- 文件头注释\n"
    "insert overwrite table dm.target partition (inc_day = '${bizdate}')\n"
    "select dept_code, count(*) as cnt from dm.src\n"
    "where inc_day = '${bizdate}' group by dept_code;\n"
    "\n"
    "-- 中间注释\n"
    "insert overwrite table dm.tmp_stage partition (inc_day = '${bizdate}')\n"
    "select dept_code, sum(cnt) as total from dm.mid\n"
    "where inc_day = '${bizdate}' group by dept_code;\n"
)


class TestChunkPromptScriptSkeleton:
    """run 5 实录：分块审查看不到其他块，把「写入语句在另一块」猜成
    「整脚本缺少 INSERT OVERWRITE」报 Critical（审查者自己都标注「需确认
    是否位于第 2 块」）——假阳性 Critical 终止了管道。修法：每块审查
    prompt 注入完整脚本骨架（各块起始行 + 首条语句），跨块缺失/不一致
    类发现先对照骨架再报。"""

    def test_skeleton_lists_all_blocks_with_first_statement(self):
        """骨架逐块列出起始行号与首条有效语句——跨块 INSERT 可见。"""
        from src.aqueduct.engine.nodes.review import (
            _script_skeleton,
            _split_sql_blocks_with_offsets,
        )

        blocks = _split_sql_blocks_with_offsets(_TWO_STMT_SQL)
        skeleton = _script_skeleton(_TWO_STMT_SQL, blocks, current_index=1)
        assert "dm.target" in skeleton, "块 1 的写入表应在骨架中"
        assert "dm.tmp_stage" in skeleton, "块 2 的写入表应可见（跨块引用的关键）"
        assert "共 2 个语句块" in skeleton

    def test_skeleton_marks_current_block(self):
        """当前块标注 [本块]，其余块不标——审查者知道自己在第几块。"""
        from src.aqueduct.engine.nodes.review import (
            _script_skeleton,
            _split_sql_blocks_with_offsets,
        )

        blocks = _split_sql_blocks_with_offsets(_TWO_STMT_SQL)
        skeleton = _script_skeleton(_TWO_STMT_SQL, blocks, current_index=1)
        assert skeleton.count("[本块]") == 1
        # 标注落在块 1 的行上（块 1 首条语句是 dm.target）
        own_line = next(ln for ln in skeleton.splitlines() if "dm.target" in ln)
        assert "[本块]" in own_line

    def test_skeleton_skips_comment_lines_for_first_statement(self):
        """首条有效语句跳过注释行——文件头注释不冒充语句。"""
        from src.aqueduct.engine.nodes.review import (
            _script_skeleton,
            _split_sql_blocks_with_offsets,
        )

        blocks = _split_sql_blocks_with_offsets(_TWO_STMT_SQL)
        skeleton = _script_skeleton(_TWO_STMT_SQL, blocks, current_index=1)
        assert "文件头注释" not in skeleton, "注释行不应作为块的语句摘要"

    def test_chunk_prompt_includes_skeleton(self):
        """分块 prompt 注入骨架：审查块 1 时也能看到块 2 的写入语句。"""
        from src.aqueduct.engine.nodes.review import (
            _script_skeleton,
            _split_sql_blocks_with_offsets,
        )

        state = _make_state()
        blocks = _split_sql_blocks_with_offsets(_TWO_STMT_SQL)
        prompt = _build_chunk_prompt(
            state,
            blocks[0][0],
            chunk_index=1,
            total_chunks=2,
            start_line=blocks[0][1],
            skeleton=_script_skeleton(_TWO_STMT_SQL, blocks, current_index=1),
        )
        assert "dm.tmp_stage" in prompt, "块 1 的审查 prompt 应含块 2 的骨架信息"

    def test_single_chunk_prompt_has_no_skeleton(self):
        """单块审查（total_chunks=1）不注入骨架——无跨块引用场景。"""
        prompt = _build_chunk_prompt(_make_state(), _SQL, chunk_index=1, total_chunks=1)
        assert "完整脚本骨架" not in prompt
