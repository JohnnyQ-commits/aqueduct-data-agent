"""知识提取投机后台化测试（P2-2 / Phase 6 长杆治理）。

knowledge_extract 的输入（需求/设计方案/DDL/SQL/域知识/表结构）在 Phase 4.5
审查入口即全部就绪，唯一串行原因是修复循环可能改写 SQL——与 PERF-9 投机 DQC
同范式：审查入口启动、Phase 6 消费时输入哈希护栏兜底，151–186s 的调用藏进
458–518s 的审查窗口，Phase 6 只剩 doc_gen。

review_result 不再进知识提取输入：回跳审查时它是上一轮的过期结果，且 review
派生的待确认事项已由 Design.md 待确认问题清单（doc_gen 洞察章）承载。
"""

from __future__ import annotations

import contextlib
from concurrent.futures import Future
from unittest.mock import patch

from src.aqueduct.engine.nodes.report import (
    _generate_knowledge_doc,
    node_report,
    start_knowledge_speculative,
    take_speculative_knowledge,
)

_VALID_SQL = """insert overwrite table dws.dws_ecommerce_daily_stat partition (inc_day)
select
    a.inc_day,
    a.gmv,
    case when a.order_count = 0 then null else a.gmv / a.order_count end as avg_order_amount
from (
    select inc_day, sum(pay_amount) as gmv, count(1) as order_count
    from ods.ods_ecommerce_order
    where inc_day = '2026-09-09'
    group by inc_day
) a;
"""

_VALID_KN = (
    "# 知识沉淀 — 测试\n\n### 一、业务域知识\n实体。\n\n### 二、表结构经验\n经验。\n\n"
    "### 三、SQL 开发经验\n模式。\n\n### 四、指标口径\n口径。\n\n### 五、待确认事项\n无。\n"
)

_VALID_INSIGHTS = "## 需求背景\n\n背景正文。\n\n## 待确认问题清单\n\n无\n"


def _make_state() -> dict:
    return {
        "requirement": "需求文档 REQUIRE_MARK",
        "design_scheme": "设计方案",
        "ddl_content": "CREATE TABLE t (id bigint)",
        "sql_content": _VALID_SQL,
        "review_result": "上一轮审查结果 REVIEW_MARK_XYZ",
        "domain_context": "域上下文",
        "table_schemas": {"ods.ods_ecommerce_order": "id bigint"},
        "dqc_result": {"results": []},
        "validation_result": {"issues": []},
        "lineage_result": {"sources": ["t"], "mermaid": "graph LR"},
        "metadata": {"requirement_name": "kn_spec_test"},
        "errors": [],
        "artifacts": [],
    }


def _cleanup(state: dict) -> None:
    """清掉投机键，防止 executor 线程拖住测试进程。"""
    state.pop("_kn_spec_future", None)
    executor = state.pop("_kn_spec_executor", None)
    state.pop("_kn_spec_input_hash", None)
    if executor:
        executor.shutdown(wait=False)


class TestStartKnowledgeSpeculative:
    """审查入口启动投机知识提取（PERF-9 范式）。"""

    def test_start_stores_future_and_hash(self):
        state = _make_state()
        with patch(
            "src.aqueduct.engine.nodes.report._generate_knowledge_doc", return_value=_VALID_KN
        ):
            start_knowledge_speculative(state)
            assert isinstance(state.get("_kn_spec_future"), Future)
            assert state.get("_kn_spec_executor") is not None
            assert state.get("_kn_spec_input_hash") is not None
            _cleanup(state)

    def test_start_skips_invalid_sql(self):
        """无效/过短 SQL 不启动（同血缘与投机 DQC 守卫，含单测短 SQL 场景）。"""
        state = _make_state()
        state["sql_content"] = "select 1"
        start_knowledge_speculative(state)
        assert "_kn_spec_future" not in state
        assert "_kn_spec_executor" not in state

    def test_start_replaces_previous_round(self):
        """回跳审查重跑时关闭并替换上一轮投机（同 start_dqc_speculative）。"""
        state = _make_state()
        with patch(
            "src.aqueduct.engine.nodes.report._generate_knowledge_doc", return_value=_VALID_KN
        ):
            start_knowledge_speculative(state)
            old_future = state["_kn_spec_future"]
            start_knowledge_speculative(state)
            assert state["_kn_spec_future"] is not old_future
            _cleanup(state)


class TestTakeSpeculativeKnowledge:
    """Phase 6 消费：输入哈希护栏。"""

    def test_take_returns_doc_on_hash_match(self):
        state = _make_state()
        with patch(
            "src.aqueduct.engine.nodes.report._generate_knowledge_doc", return_value=_VALID_KN
        ):
            start_knowledge_speculative(state)
            state["_kn_spec_future"].result(timeout=5)
            assert take_speculative_knowledge(state) == _VALID_KN
        assert "_kn_spec_future" not in state
        assert "_kn_spec_executor" not in state

    def test_take_discards_on_hash_mismatch(self):
        """修复循环改写 SQL → 哈希不一致 → 丢弃过期投机，返回 None。"""
        state = _make_state()
        with patch(
            "src.aqueduct.engine.nodes.report._generate_knowledge_doc", return_value=_VALID_KN
        ):
            start_knowledge_speculative(state)
            state["_kn_spec_future"].result(timeout=5)
            state["sql_content"] = _VALID_SQL.replace("'2026-09-09'", "'2026-09-10'")
            assert take_speculative_knowledge(state) is None
        assert "_kn_spec_future" not in state

    def test_take_none_when_no_future(self):
        state = _make_state()
        assert take_speculative_knowledge(state) is None

    def test_take_returns_none_on_failure(self):
        """后台调用失败（空响应走 fallback 除外，此处为异常）→ None 走正常路径。"""
        state = _make_state()
        with patch(
            "src.aqueduct.engine.nodes.report._generate_knowledge_doc",
            side_effect=RuntimeError("boom"),
        ):
            start_knowledge_speculative(state)
            with contextlib.suppress(RuntimeError):
                # 后台异常在 future 内，take 负责吞掉
                state["_kn_spec_future"].result(timeout=5)
            assert take_speculative_knowledge(state) is None

    def test_take_returns_doc_on_hash_match_pad(self):
        """≥100 字符的投机结果原样复用（<100 会被 _generate_knowledge_doc 判短走 fallback）。"""
        state = _make_state()
        doc = _VALID_KN + "\n\nkn-TAKE-MARK\n"
        with patch("src.aqueduct.engine.nodes.report._generate_knowledge_doc", return_value=doc):
            start_knowledge_speculative(state)
            state["_kn_spec_future"].result(timeout=5)
            assert take_speculative_knowledge(state) == doc


class TestKnowledgePromptInputs:
    """输入瘦身：review_result 不进知识提取 prompt。"""

    def test_prompt_excludes_review_result(self):
        state = _make_state()
        prompts: list[str] = []

        def fake_call_llm(state, task_type, prompt):
            prompts.append(prompt)
            return _VALID_KN + "\n\nkn-PROMPT-MARK\n"

        with patch("src.aqueduct.engine.nodes.report.call_llm", side_effect=fake_call_llm):
            doc = _generate_knowledge_doc(state)

        assert "REVIEW_MARK_XYZ" not in prompts[0]
        assert "$review_result" not in prompts[0]
        # SQL 与需求仍在输入里（瘦身不是砍料）
        assert "ods_ecommerce_order" in prompts[0]
        assert "REQUIRE_MARK" in prompts[0]
        assert "kn-PROMPT-MARK" in doc


class TestNodeReportSpeculative:
    """node_report 集成：命中投机则不再生成，未命中走原并行路径。"""

    def test_node_report_uses_speculative_without_regen(self):
        state = _make_state()
        llm_calls: list[str] = []

        def fake_call_llm(state, task_type, prompt):
            llm_calls.append(task_type)
            if task_type == "doc_gen":
                return _VALID_INSIGHTS
            return _VALID_KN

        with patch(
            "src.aqueduct.engine.nodes.report._generate_knowledge_doc",
            return_value=_VALID_KN + "\nkn-SPEC\n",
        ):
            start_knowledge_speculative(state)
            state["_kn_spec_future"].result(timeout=5)
            # 消费阶段换桩：再触发生成即失败
            with patch(
                "src.aqueduct.engine.nodes.report._generate_knowledge_doc",
                side_effect=AssertionError("投机命中时不应重新生成知识沉淀"),
            ):
                saved: dict[str, str] = {}

                def fake_save(state, filename, content):
                    saved[filename] = content
                    return filename

                with (
                    patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
                    patch("src.aqueduct.engine.nodes.report.call_llm", side_effect=fake_call_llm),
                    patch("src.aqueduct.engine.nodes.report.save_artifact", side_effect=fake_save),
                    patch("src.aqueduct.engine.nodes.report.get_tool"),
                ):
                    node_report(state)

        assert "kn-SPEC" in saved["Phase6-知识沉淀.md"]
        assert llm_calls == ["doc_gen"]

    def test_node_report_falls_back_when_hash_mismatch(self):
        state = _make_state()
        with patch(
            "src.aqueduct.engine.nodes.report._generate_knowledge_doc", return_value=_VALID_KN
        ):
            start_knowledge_speculative(state)
            state["_kn_spec_future"].result(timeout=5)
            # 修复循环改写 SQL → 投机过期
            state["sql_content"] = _VALID_SQL.replace("'2026-09-09'", "'2026-09-10'")

            with patch(
                "src.aqueduct.engine.nodes.report._generate_knowledge_doc",
                return_value=_VALID_KN + "\nkn-REGEN\n",
            ):
                saved: dict[str, str] = {}

                def fake_save(state, filename, content):
                    saved[filename] = content
                    return filename

                with (
                    patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
                    patch(
                        "src.aqueduct.engine.nodes.report.call_llm",
                        side_effect=lambda s, t, p: (
                            _VALID_INSIGHTS if t == "doc_gen" else _VALID_KN
                        ),
                    ),
                    patch("src.aqueduct.engine.nodes.report.save_artifact", side_effect=fake_save),
                    patch("src.aqueduct.engine.nodes.report.get_tool"),
                ):
                    node_report(state)

        assert "kn-REGEN" in saved["Phase6-知识沉淀.md"]
