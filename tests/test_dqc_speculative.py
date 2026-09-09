"""Phase 4.5/5 投机 DQC 并行测试（PERF-9）。

DQC 的输入（ddl_content/sql_content/domain_context）不依赖审查结果，
唯一串行原因是修复循环可能改写 SQL——用 (sql, ddl) 哈希护栏兜住：
node_dqc 消费时哈希一致才复用投机结果，否则丢弃走正常重新生成。
"""

from __future__ import annotations

import threading
from unittest.mock import patch

from src.aqueduct.engine.nodes.dqc import (
    node_dqc,
    start_dqc_speculative,
    take_speculative_dqc,
)
from src.aqueduct.engine.nodes.review import node_review

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
        "metadata": {"requirement_name": "dqc_spec_test"},
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


# P0-3: 假响应必须带 -- [ 用例注释头（_generate_dqc_split 的格式契约，
# 无注释头的响应会被当作无效触发重试/降级）
def _resp(tag: str) -> str:
    return f"-- [唯一性-{tag}] select 1 as v from t where inc_day = '${{bizdate}}';\n{tag}"


class TestSpeculativeStart:
    """node_review 入口投机启动 DQC 生成。"""

    def test_speculative_dqc_overlaps_review(self):
        """dqc_gen 与 sql_review 重叠执行：后启动者能观察到先启动者仍在运行。"""

        state = _make_state()
        spec_started = threading.Event()
        review_started = threading.Event()

        def fake_review_llm(state, task_type, prompt):
            # sql_review：标记自身启动，并确认投机 DGC 已在跑
            review_started.set()
            overlapped = spec_started.wait(timeout=3)
            return "review-" + ("OVERLAP" if overlapped else "SERIAL")

        calls: list[str] = []

        def fake_dqc_llm(state, task_type, prompt):
            calls.append(task_type)
            if task_type == "dqc_gen":
                spec_started.set()
                overlapped = review_started.wait(timeout=3)
                return _resp("dqc-" + ("OVERLAP" if overlapped else "SERIAL"))
            return "other"

        with (
            patch("src.aqueduct.engine.nodes.review.get_skill") as mock_review_skill,
            patch(
                "src.aqueduct.engine.nodes.review.call_llm",
                side_effect=fake_review_llm,
            ),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value=""),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
            patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_dqc_llm),
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
            patch(
                "src.aqueduct.tools.registry.get_tool",
                return_value=_failed_health_tool(),
            ),
        ):
            mock_review_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": "test"}}
            )()
            node_review(state)
            # 投机结果应被 Phase 5 复用（无第二次 dqc_gen 调用）
            node_dqc(state)

        assert state["review_result"] == "review-OVERLAP"
        # P0-3: 投机拆分为 5 类并行调用，Phase 5 复用投机结果不重复调用
        assert sorted(calls) == ["dqc_gen"] * 5
        assert "dqc-OVERLAP" in state["dqc_result"]

    def test_short_sql_skips_speculative_start(self):
        """短/无效 SQL 不启动投机（与血缘守卫一致，保护现有测试）。"""

        state = _make_state()
        state["sql_content"] = "SELECT * FROM t"

        with patch("src.aqueduct.engine.nodes.dqc.call_llm") as mock_llm:
            start_dqc_speculative(state)

        assert "_dqc_spec_future" not in state
        mock_llm.assert_not_called()


class TestSpeculativeConsume:
    """node_dqc 消费投机结果的哈希护栏。"""

    @staticmethod
    def _start_spec(state, response=None):
        """启动投机并在 patch 上下文内等待完成（避免线程逃逸到真实 call_llm）。"""
        if response is None:
            response = _resp("SPEC-RESULT")
        with patch("src.aqueduct.engine.nodes.dqc.call_llm", return_value=response) as mock_llm:
            start_dqc_speculative(state)
            state["_dqc_spec_future"].result(timeout=5)
        return mock_llm

    def test_node_dqc_reuses_matching_spec(self):
        """哈希一致时复用投机结果，不再发起 dqc_gen 调用。"""

        state = _make_state()
        self._start_spec(state)

        with (
            patch("src.aqueduct.engine.nodes.dqc.call_llm") as mock_llm,
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
            patch(
                "src.aqueduct.tools.registry.get_tool",
                return_value=_failed_health_tool(),
            ),
        ):
            node_dqc(state)

        mock_llm.assert_not_called()
        assert "SPEC-RESULT" in state["dqc_result"]
        # 消费后清理线程资源
        assert "_dqc_spec_future" not in state
        assert "_dqc_spec_executor" not in state

    def test_node_dqc_discards_stale_spec_on_sql_change(self):
        """修复循环改写 SQL 后（哈希不一致），丢弃投机结果重新生成。"""

        state = _make_state()
        self._start_spec(state, response=_resp("STALE-SPEC"))
        state["sql_content"] += "\n-- 修复循环改写"

        def fake_llm(state, task_type, prompt):
            assert task_type == "dqc_gen"
            return _resp("FRESH-RESULT")

        with (
            patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm),
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
            patch(
                "src.aqueduct.tools.registry.get_tool",
                return_value=_failed_health_tool(),
            ),
        ):
            node_dqc(state)

        assert "FRESH-RESULT" in state["dqc_result"]
        assert "STALE-SPEC" not in state["dqc_result"]

    def test_node_dqc_falls_back_on_spec_exception(self):
        """投机整轮失败（全类失败抛错）时回退正常路径重新生成。"""
        from src.aqueduct.exceptions import LLMTimeoutError

        state = _make_state()
        call_count = {"n": 0}

        def fake_llm(state, task_type, prompt):
            # 前 5 次 = 投机轮（全类失败）→ 之后 = 正常路径重新生成
            call_count["n"] += 1
            if call_count["n"] <= 5:
                raise LLMTimeoutError("spec 超时")
            return _resp("FRESH-RESULT")

        with (
            patch(
                "src.aqueduct.engine.nodes.dqc.call_llm",
                side_effect=fake_llm,
            ),
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
            patch(
                "src.aqueduct.tools.registry.get_tool",
                return_value=_failed_health_tool(),
            ),
        ):
            start_dqc_speculative(state)
            node_dqc(state)

        assert "FRESH-RESULT" in state["dqc_result"]
        assert call_count["n"] == 10, "投机轮 5 次 + 正常路径 5 次"

    def test_node_dqc_without_spec_normal_path(self):
        """无投机结果时走原有路径（一次 dqc_gen 调用）。"""

        state = _make_state()
        calls: list[str] = []

        def fake_llm(state, task_type, prompt):
            calls.append(task_type)
            return _resp("NORMAL-RESULT")

        with (
            patch("src.aqueduct.engine.nodes.dqc.call_llm", side_effect=fake_llm),
            patch("src.aqueduct.engine.nodes.dqc.save_artifact", return_value=""),
            patch(
                "src.aqueduct.tools.registry.get_tool",
                return_value=_failed_health_tool(),
            ),
        ):
            node_dqc(state)

        assert sorted(calls) == ["dqc_gen"] * 5, "P0-3: 正常路径拆分为 5 类调用"
        assert "NORMAL-RESULT" in state["dqc_result"]

    def test_take_speculative_without_future_returns_none(self):
        """无 future 时 take 返回 None（管道其他模式兼容）。"""

        state = _make_state()
        assert take_speculative_dqc(state) is None


class TestSpeculativeRestart:
    """修复循环重跑 review 时的投机重启。"""

    def test_restart_replaces_old_spec(self):
        """重新启动投机时关闭旧 executor，替换为新 future。"""

        state = _make_state()
        with patch("src.aqueduct.engine.nodes.dqc.call_llm", return_value=_resp("SPEC-1")):
            start_dqc_speculative(state)
            state["_dqc_spec_future"].result(timeout=5)
        old_executor = state["_dqc_spec_executor"]
        old_future = state["_dqc_spec_future"]

        with patch("src.aqueduct.engine.nodes.dqc.call_llm", return_value=_resp("SPEC-2")):
            start_dqc_speculative(state)
            state["_dqc_spec_future"].result(timeout=5)

        assert state["_dqc_spec_future"] is not old_future
        assert old_executor._shutdown, "旧 executor 应被关闭"
        assert state["_dqc_spec_executor"] is not old_executor
