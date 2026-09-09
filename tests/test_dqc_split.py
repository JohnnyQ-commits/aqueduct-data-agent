"""P0-3: dqc_gen 拆分测试（根治确定性思考螺旋）。

根因（v5 实测）：单次大 prompt 要求同时覆盖 5 类测试 × 严格注释格式 ×
业务逻辑反证，约束密度过高——always-thinking 模型（glm-5.3）9/9 确定性
思考螺旋（烧满 32768 tokens 零正文，重试也无法恢复）。

拆分方案：每类测试一次小调用（prompt 只含本类指令），5 类并行执行，
按固定类别顺序合并为一份 DQC SQL；单类失败降级跳过（附降级注释），
全类失败才抛错。投机（PERF-9）与正常路径统一走拆分生成。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.aqueduct.engine.nodes.dqc import (
    _DQC_CATEGORIES,
    _generate_dqc_split,
    build_dqc_category_prompt,
    node_dqc,
    start_dqc_speculative,
)
from src.aqueduct.engine.nodes.review import node_review
from src.aqueduct.exceptions import LLMError

# 有效 SQL：长度 > 50 且含 INSERT 关键字（满足 is_valid_sql 守卫）
_VALID_SQL = (
    "INSERT OVERWRITE TABLE dw_demo.tmp_order_daily_stat PARTITION (inc_day = '$[0]')\n"
    "SELECT city, count(*) AS order_cnt FROM dwd.order_detail\n"
    "WHERE inc_day = '$[0]' GROUP BY city"
)


def _make_state() -> dict:
    return {
        "requirement": "需求文档",
        "requirement_summary": "统计每日各城市订单量",
        "ddl_content": "CREATE TABLE dw_demo.tmp_order_daily_stat (city string, order_cnt bigint)",
        "sql_content": _VALID_SQL,
        "domain_context": "实体: Order",
        "validation_result": {"issues": []},
        "metadata": {"requirement_name": "dqc_split_test"},
        "errors": [],
        "artifacts": [],
    }


def _failed_health_tool():
    """executor 工具 mock：health_check 直接失败（跳过 DQC 执行，不打网络）。"""
    tool = type(
        "T",
        (),
        {
            "health_check": lambda self: type(
                "H", (), {"success": False, "data": {"message": "测试跳过"}}
            )(),
            "execute_batch": lambda self, sqls: type("B", (), {"success": False, "data": {}})(),
        },
    )()
    return tool


def _category_of(prompt: str) -> str:
    """从 prompt 中识别类别（fake LLM 按 prompt 分派响应）。"""
    for cat in _DQC_CATEGORIES:
        if cat["name"] in prompt:
            return cat["name"]
    return ""


def _fake_llm_by_category(state, task_type, prompt):
    """按 prompt 中的类别名返回带标记的 SQL（可校验合并顺序）。"""
    name = _category_of(prompt)
    return f"```sql\n-- [{name}-标记] 检查\nselect count(*) from t;\n```"


class TestCategoryPrompt:
    """单类 prompt 构建：聚焦本类、剥离其他类的约束（约束密度下降的结构性证据）。"""

    def test_prompt_contains_category_name_and_inputs(self):
        state = _make_state()
        cat = _DQC_CATEGORIES[0]
        prompt = build_dqc_category_prompt(state, cat)
        assert prompt is not None
        assert cat["name"] in prompt
        assert "tmp_order_daily_stat" in prompt  # ddl
        assert "dwd.order_detail" in prompt  # sql

    def test_prompt_excludes_other_categories(self):
        """单类 prompt 不应再要求覆盖其他 4 类——这是拆分降螺旋的核心。"""
        state = _make_state()
        for cat in _DQC_CATEGORIES:
            prompt = build_dqc_category_prompt(state, cat)
            for other in _DQC_CATEGORIES:
                if other["key"] != cat["key"]:
                    assert other["name"] not in prompt, (
                        f"{cat['name']} 的 prompt 不应包含其他类别 {other['name']}"
                    )

    def test_prompt_contains_format_contract(self):
        """注释格式契约（-- [分类-名称] / 权重 / 阈值 / 预期）必须保留——下游解析依赖。"""
        state = _make_state()
        prompt = build_dqc_category_prompt(state, _DQC_CATEGORIES[0])
        assert "-- [分类-名称]" in prompt or "权重" in prompt
        assert "阈值" in prompt


class TestSplitGeneration:
    """拆分生成：5 类并行独立调用 + 固定顺序合并 + 失败降级。"""

    def test_five_calls_one_per_category(self):
        state = _make_state()
        calls: list[str] = []

        def fake_llm(state, task_type, prompt):
            calls.append(task_type)
            return _fake_llm_by_category(state, task_type, prompt)

        with patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm):
            result = _generate_dqc_split(state)

        assert len(calls) == 5
        assert all(t == "dqc_gen" for t in calls), "task_type 保持 dqc_gen（模型路由不变）"
        assert "[DQC降级]" not in result

    def test_merged_in_fixed_category_order(self):
        """合并结果按类别定义顺序排列（并行完成顺序不定，顺序由类别锚定）。"""
        state = _make_state()

        with patch(
            "src.aqueduct.engine.nodes.dqc.call_llm",
            side_effect=_fake_llm_by_category,
        ):
            result = _generate_dqc_split(state)

        positions = [result.index(f"[{c['name']}-标记]") for c in _DQC_CATEGORIES]
        assert positions == sorted(positions), "各类 SQL 应按 _DQC_CATEGORIES 定义顺序合并"

    def test_partial_failure_degrades_with_note(self):
        """单类失败：其余 4 类正常合并，失败类以 [DQC降级] 注释标注，不炸整轮。"""
        state = _make_state()
        fail_name = _DQC_CATEGORIES[2]["name"]

        def fake_llm(state, task_type, prompt):
            if _category_of(prompt) == fail_name:
                raise LLMError("类别生成失败（模拟螺旋重试耗尽）")
            return _fake_llm_by_category(state, task_type, prompt)

        with patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm):
            result = _generate_dqc_split(state)

        assert f"[{fail_name}-标记]" not in result
        assert "[DQC降级]" in result
        assert fail_name in result  # 降级注释中说明失败类别
        # 其余 4 类仍在
        for cat in _DQC_CATEGORIES:
            if cat["name"] != fail_name:
                assert f"[{cat['name']}-标记]" in result

    def test_canned_error_response_retried_then_recovers(self):
        """罐头错误响应（无 -- [ 注释头，冒烟 Round 3 实测 55 字符/13 token）：
        重试一次恢复则正常合并，不降级。"""
        state = _make_state()
        flaky_name = _DQC_CATEGORIES[0]["name"]
        attempts: list[str] = []

        def fake_llm(state, task_type, prompt):
            name = _category_of(prompt)
            if name == flaky_name:
                attempts.append(name)
                if len(attempts) == 1:
                    return "抱歉，服务器繁忙，请稍后重试。"  # 无注释头的罐头响应
            return _fake_llm_by_category(state, task_type, prompt)

        with patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm):
            result = _generate_dqc_split(state)

        assert len(attempts) == 2, "无效格式应触发一次重试"
        assert f"[{flaky_name}-标记]" in result, "重试恢复后该类正常合并"
        assert "[DQC降级]" not in result

    def test_canned_error_response_both_attempts_degrades(self):
        """罐头错误响应重试后仍无效：该类降级（不静默产出垃圾）。"""
        state = _make_state()
        flaky_name = _DQC_CATEGORIES[3]["name"]
        attempts: list[str] = []

        def fake_llm(state, task_type, prompt):
            name = _category_of(prompt)
            if name == flaky_name:
                attempts.append(name)
                return "抱歉，服务器繁忙，请稍后重试。"
            return _fake_llm_by_category(state, task_type, prompt)

        with patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm):
            result = _generate_dqc_split(state)

        assert len(attempts) == 2, "应恰好重试一次"
        assert "[DQC降级]" in result
        assert flaky_name in result
        # 其余 4 类不受影响
        for cat in _DQC_CATEGORIES:
            if cat["name"] != flaky_name:
                assert f"[{cat['name']}-标记]" in result

    def test_all_categories_fail_raises(self):
        """全类失败：抛 LLMError（由 node_dqc 兜底记 errors，与单发模式语义一致）。"""
        state = _make_state()

        def fake_llm(state, task_type, prompt):
            raise LLMError("全类失败（模拟）")

        with (
            patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm),
            pytest.raises(LLMError),
        ):
            _generate_dqc_split(state)


class TestSplitInPipeline:
    """node_dqc / 投机路径接线。"""

    def test_node_dqc_normal_path_uses_split(self):
        state = _make_state()
        calls: list[str] = []

        def fake_llm(state, task_type, prompt):
            calls.append(task_type)
            return _fake_llm_by_category(state, task_type, prompt)

        with (
            patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm),
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
            patch(
                "src.aqueduct.tools.registry.get_tool",
                return_value=_failed_health_tool(),
            ),
        ):
            node_dqc(state)

        assert calls == ["dqc_gen"] * 5
        assert f"[{_DQC_CATEGORIES[0]['name']}-标记]" in state["dqc_result"]
        assert f"[{_DQC_CATEGORIES[4]['name']}-标记]" in state["dqc_result"]

    def test_node_dqc_records_degradation_in_errors(self):
        """降级发生时 node_dqc 在 state.errors 记录（用户可见）。"""
        state = _make_state()
        fail_name = _DQC_CATEGORIES[1]["name"]

        def fake_llm(state, task_type, prompt):
            if _category_of(prompt) == fail_name:
                raise LLMError("模拟失败")
            return _fake_llm_by_category(state, task_type, prompt)

        with (
            patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm),
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
            patch(
                "src.aqueduct.tools.registry.get_tool",
                return_value=_failed_health_tool(),
            ),
        ):
            node_dqc(state)

        degraded = [e for e in state["errors"] if fail_name in e and "DQC" in e]
        assert degraded, f"errors 应记录降级类别: {state['errors']}"

    def test_speculative_future_returns_merged_split(self):
        """投机启动（PERF-9）也走拆分：单 future 返回 5 类合并结果。"""
        state = _make_state()

        with patch(
            "src.aqueduct.engine.nodes.dqc.call_llm",
            side_effect=_fake_llm_by_category,
        ):
            start_dqc_speculative(state)
            merged = state["_dqc_spec_future"].result(timeout=10)

        for cat in _DQC_CATEGORIES:
            assert f"[{cat['name']}-标记]" in merged

    def test_review_triggers_split_speculation(self):
        """node_review 入口的投机启动触发 5 类拆分调用（与审查并行）。"""
        state = _make_state()
        dqc_calls: list[str] = []

        def fake_dqc_llm(state, task_type, prompt):
            dqc_calls.append(task_type)
            return _fake_llm_by_category(state, task_type, prompt)

        with (
            patch("src.aqueduct.engine.nodes.review.get_skill") as mock_review_skill,
            patch(
                "src.aqueduct.engine.nodes.review.call_llm",
                return_value="审查通过，无 Critical 问题",
            ),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value=""),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
            patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_dqc_llm),
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
        ):
            mock_review_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": "test"}}
            )()
            node_review(state)
            # 等待投机完成后再离开 patch 上下文（避免线程逃逸到真实 call_llm）
            state["_dqc_spec_future"].result(timeout=10)

        assert len(dqc_calls) == 5
        assert all(t == "dqc_gen" for t in dqc_calls)
