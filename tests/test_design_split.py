"""Phase 6 Design.md 生成拆分测试（PERF-4：模板填充本地化 + 洞察生成 LLM 化）。

现状（2026-09-07 greenfield 实测）：doc_gen prompt 18,862 字符 / 9,303 in-tokens，
响应 10,370 字符 / 6,273 out-tokens，耗时 505s——单次调用占全管道约 8 分钟。
其中 4,531 字符（44%）输出是 DDL/SQL/血缘图的逐字转写，设计方案章节是
design_scheme 的复述——LLM 在做人肉 copy-paste，还带转写失真风险。

拆分契约：
- LLM（doc_gen）只写两章洞察：`## 需求背景` + `## 待确认问题清单`
- 本地拼装（零 LLM、零转写失真）：H1 + 设计方案(design_scheme 去首行 H1)
  + 表结构(DDL) + 核心 SQL + 血缘图(mermaid)，章节头永远在场
- doc_gen prompt 不再包含 ddl_content/sql_content/lineage_mermaid（性能契约）
- 结构门禁复用：ensure_structure 作用于拼装后全文，缺章重试 = 重生成洞察+重拼装；
  4 个本地章节确定性通过，唯一可缺的是需求背景
"""

from __future__ import annotations

from unittest.mock import patch

from src.aqueduct.engine.contract import validate_structure
from src.aqueduct.engine.nodes.report import (
    _assemble_design_doc,
    _extract_insight_chapters,
    node_report,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_INSIGHTS = """## 需求背景

统计每日各城市订单量，支撑运营日报。DQC 覆盖主键唯一性与分区完整性。

## 待确认问题清单

无
"""

_INSIGHTS_NO_BG = """## 待确认问题清单

无
"""

_INSIGHTS_FENCED = f"```markdown\n{_INSIGHTS}```\n"

# 契约有效的知识沉淀假响应（对齐 test_productivity_board fixtures——
# 否则知识路径正常降级会污染 errors，误触发设计路径的断言）
_VALID_KN = (
    "# 知识沉淀 — split_test\n\n### 一、业务域知识\n实体。\n\n### 二、表结构经验\n经验。\n\n"
    "### 三、SQL开发经验\n模式。\n\n### 四、指标口径\n口径。\n\n### 五、待确认事项\n无。\n"
)


def _make_state() -> dict:
    return {
        "requirement": "需求文档",
        "requirement_summary": "需求摘要",
        "design_scheme": "# Phase2 设计方案\n\n## 取数逻辑\n\n- 数据来源: dwd.order_detail",
        "ddl_content": "CREATE TABLE zz_ddl_marker (id bigint)",
        "sql_content": "SELECT 'zz_sql_marker'",
        "review_result": "审查通过",
        "dqc_result": "-- DQC SQL",
        "validation_result": {"issues": []},
        "lineage_result": {"sources": ["t"], "mermaid": "graph LR\n  ZZ_MERMAID --> T"},
        "domain_context": "域上下文",
        "metadata": {"requirement_name": "split_test"},
        "errors": [],
        "artifacts": [],
    }


def _run_node_report(state: dict, fake_call_llm, saved: dict) -> None:
    """node_report 测试通用入口（patch 组对齐 test_report_parallel 的写法）。"""
    with (
        patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
        patch("src.aqueduct.engine.nodes.report.call_llm", side_effect=fake_call_llm),
        patch(
            "src.aqueduct.engine.nodes.report.save_artifact",
            side_effect=lambda s, f, c: (saved.__setitem__(f, c), f)[1],
        ),
        patch("src.aqueduct.engine.nodes.report.get_tool"),
    ):
        node_report(state)


# ---------------------------------------------------------------------------
# _extract_insight_chapters
# ---------------------------------------------------------------------------


class TestExtractInsightChapters:
    """从 LLM 洞察响应中拆出（需求背景, 待确认问题清单）两章正文。"""

    def test_extracts_both_chapters(self):
        background, questions = _extract_insight_chapters(_INSIGHTS)
        assert "统计每日各城市订单量" in background
        assert "无" in questions

    def test_missing_questions_chapter_returns_empty(self):
        _background, questions = _extract_insight_chapters(_INSIGHTS_NO_BG)
        assert "待确认" not in questions
        assert questions == ""

    def test_missing_both_returns_empty_pair(self):
        assert _extract_insight_chapters("前言说明，无任何章节") == ("", "")

    def test_strips_markdown_fence_around_whole_output(self):
        background, questions = _extract_insight_chapters(_INSIGHTS_FENCED)
        assert "统计每日各城市订单量" in background
        assert "无" in questions

    def test_tolerates_h3_headers(self):
        text = "### 需求背景\n\n背景正文。\n\n### 待确认问题清单\n\n无\n"
        background, questions = _extract_insight_chapters(text)
        assert "背景正文" in background
        assert "无" in questions

    def test_extra_chapters_are_dropped(self):
        """LLM 违规多写的章节（如表结构）被丢弃——本地填充说了算。"""
        text = _INSIGHTS + "\n## 表结构(DDL)\n\n不该出现的转写。\n"
        _, questions = _extract_insight_chapters(text)
        assert "不该出现的转写" not in questions


# ---------------------------------------------------------------------------
# _assemble_design_doc
# ---------------------------------------------------------------------------


def _assemble(state: dict, background: str = "背景正文。", questions: str = "无") -> str:
    lineage = state.get("lineage_result") or {}
    return _assemble_design_doc(
        requirement_name=state["metadata"]["requirement_name"],
        design_scheme=state.get("design_scheme", ""),
        ddl_content=state.get("ddl_content", ""),
        sql_content=state.get("sql_content", ""),
        lineage_mermaid=lineage.get("mermaid", "") if isinstance(lineage, dict) else "",
        background=background,
        open_questions=questions,
    )


class TestAssembleDesignDoc:
    """本地拼装：6 章头永远在场，代码块逐字嵌入。"""

    def test_contains_all_six_sections_in_order(self):
        doc = _assemble(_make_state())
        positions = [
            doc.index("## 需求背景"),
            doc.index("## 设计方案"),
            doc.index("## 表结构(DDL)"),
            doc.index("## 核心 SQL"),
            doc.index("## 血缘图"),
            doc.index("## 待确认问题清单"),
        ]
        assert positions == sorted(positions)

    def test_ddl_and_sql_verbatim_in_sql_fences(self):
        state = _make_state()
        doc = _assemble(state)
        assert "```sql\nCREATE TABLE zz_ddl_marker (id bigint)\n```" in doc
        assert "```sql\nSELECT 'zz_sql_marker'\n```" in doc

    def test_mermaid_verbatim_in_mermaid_fence(self):
        doc = _assemble(_make_state())
        assert "```mermaid\ngraph LR\n  ZZ_MERMAID --> T\n```" in doc

    def test_design_scheme_leading_h1_stripped(self):
        doc = _assemble(_make_state())
        assert "# Phase2 设计方案" not in doc
        assert "## 取数逻辑" in doc

    def test_empty_ddl_gets_note_but_header_present(self):
        state = _make_state()
        state["ddl_content"] = ""
        doc = _assemble(state)
        assert "## 表结构(DDL)" in doc
        assert "（DDL 未生成）" in doc

    def test_empty_mermaid_gets_note_but_header_present(self):
        """血缘图为空时靠标题过契约（contract 血缘图 pattern 接受标题或 mermaid 块）。"""
        state = _make_state()
        state["lineage_result"] = {"sources": ["t"], "mermaid": ""}
        doc = _assemble(state)
        assert "## 血缘图" in doc
        assert "（血缘分析未完成）" in doc

    def test_empty_background_gets_fallback_note(self):
        doc = _assemble(_make_state(), background="")
        assert "## 需求背景" in doc
        assert "生成失败" in doc

    def test_empty_questions_gets_fallback_note(self):
        doc = _assemble(_make_state(), questions="")
        assert "## 待确认问题清单" in doc
        assert "生成失败" in doc

    def test_assembled_doc_passes_structure_contract(self):
        """4 个本地章节 + 2 个洞察章节 → 五章契约确定性通过。"""
        doc = _assemble(_make_state(), background="", questions="")
        missing = validate_structure("Phase6-Design.md", doc)
        # 待确认问题清单不在契约内；需求背景头由拼装兜底，永不下线
        assert missing == []


# ---------------------------------------------------------------------------
# node_report 集成（doc_gen 只产洞察 + 本地拼装 + 门禁复用）
# ---------------------------------------------------------------------------


class TestNodeReportDesignSplit:
    """Phase 6 拆分后的节点级行为。"""

    def test_doc_gen_prompt_excludes_transcribed_content(self):
        """性能契约：DDL/SQL/血缘图不进 doc_gen prompt（本地填充，省 token 免转写）。"""
        state = _make_state()
        prompts: list[str] = []

        def fake_call_llm(s, task_type, prompt):
            if task_type == "doc_gen":
                prompts.append(prompt)
            return _INSIGHTS if task_type == "doc_gen" else _VALID_KN

        saved: dict[str, str] = {}
        _run_node_report(state, fake_call_llm, saved)

        assert prompts, "doc_gen 未被调用"
        assert "zz_ddl_marker" not in prompts[0]
        assert "zz_sql_marker" not in prompts[0]
        assert "ZZ_MERMAID" not in prompts[0]

    def test_doc_gen_prompt_contains_design_scheme_and_dqc(self):
        """洞察素材在场：设计方案 + DQC 结果（需求背景/待确认的输入）。"""
        state = _make_state()
        prompts: list[str] = []

        def fake_call_llm(s, task_type, prompt):
            if task_type == "doc_gen":
                prompts.append(prompt)
            return _INSIGHTS if task_type == "doc_gen" else _VALID_KN

        saved: dict[str, str] = {}
        _run_node_report(state, fake_call_llm, saved)

        assert "取数逻辑" in prompts[0]
        assert "DQC SQL" in prompts[0]

    def test_design_md_contains_insights_and_verbatim_artifacts(self):
        """洞察来自 LLM，DDL/SQL/血缘逐字来自 state——保真度按构造成立。"""
        state = _make_state()
        saved: dict[str, str] = {}

        def fake_call_llm(s, task_type, prompt):
            return _INSIGHTS if task_type == "doc_gen" else _VALID_KN

        _run_node_report(state, fake_call_llm, saved)

        doc = saved["Phase6-Design.md"]
        assert "统计每日各城市订单量" in doc
        assert "CREATE TABLE zz_ddl_marker (id bigint)" in doc
        assert "SELECT 'zz_sql_marker'" in doc
        assert "ZZ_MERMAID --> T" in doc

    def test_missing_background_triggers_one_targeted_regen(self):
        """需求背景缺失 → 1 次定向重试（重试 prompt 带缺章警示）→ 补全且无 errors。"""
        state = _make_state()
        calls: list[tuple[str, str]] = []

        def fake_call_llm(s, task_type, prompt):
            calls.append((task_type, prompt))
            if task_type == "doc_gen":
                return _INSIGHTS_NO_BG if len(calls) == 1 else _INSIGHTS
            return _VALID_KN

        saved: dict[str, str] = {}
        _run_node_report(state, fake_call_llm, saved)

        doc_gen_prompts = [p for t, p in calls if t == "doc_gen"]
        assert len(doc_gen_prompts) == 2
        assert "结构警示" in doc_gen_prompts[1]
        assert "统计每日各城市订单量" in saved["Phase6-Design.md"]
        assert not any("结构缺章" in e for e in state["errors"])

    def test_regen_still_missing_degrades_with_local_sections_intact(self):
        """两次都缺需求背景 → 横幅降级 + errors；4 个本地章节不受影响。"""
        state = _make_state()
        saved: dict[str, str] = {}

        def fake_call_llm(s, task_type, prompt):
            return _INSIGHTS_NO_BG if task_type == "doc_gen" else _VALID_KN

        saved: dict[str, str] = {}
        _run_node_report(state, fake_call_llm, saved)

        doc = saved["Phase6-Design.md"]
        assert "结构门禁告警" in doc
        assert any("结构缺章" in e and "需求背景" in e for e in state["errors"])
        for section in ["## 设计方案", "## 表结构(DDL)", "## 核心 SQL", "## 血缘图"]:
            assert section in doc
