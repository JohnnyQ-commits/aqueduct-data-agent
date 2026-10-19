"""降级可见性横幅单元测试。

2026-10-15 实录：UI 跑真实需求当夜网络故障，试跑/血缘/DQC 三防线全部
静默降级，success=True + 16 交付物看起来完整、实际 SQL 从未上过平台。
本批让降级可见：报告头部横幅 + AqueductResult.degradations + UI 状态块。
"""

from __future__ import annotations

from concurrent.futures import Future

from src.aqueduct.engine.nodes.report import (
    _collect_degradations,
    format_degradation_banner,
)
from src.aqueduct.engine.nodes.sql import wait_for_lineage


class TestCollectDegradations:
    def test_clean_state_has_no_degradations(self):
        assert _collect_degradations({"errors": []}) == []

    def test_trial_env_errors_collected(self):
        state = {
            "errors": [],
            "trial_run_result": {
                "total": 3,
                "passed": 0,
                "errors": [],
                "env_errors": ["SELECT #1: [WinError 10060] ..."],
            },
        }
        ds = _collect_degradations(state)
        assert len(ds) == 1
        assert "试跑" in ds[0] and "未在平台验证" in ds[0]

    def test_lineage_error_collected(self):
        state = {"errors": [], "lineage_result": {"error": "血缘生成异常"}}
        ds = _collect_degradations(state)
        assert len(ds) == 1 and "血缘" in ds[0]

    def test_dqc_degraded_error_collected(self):
        state = {
            "errors": ["DQC 部分类别降级: -- [DQC降级] 类别「一致性」本轮生成失败，已跳过该类测试"]
        }
        ds = _collect_degradations(state)
        assert len(ds) == 1 and "DQC" in ds[0]

    def test_probe_timeout_profiles_collected(self):
        """探查超时进降级横幅（run 3 实录）：探查被静默吞掉导致枚举占位上线风险。"""
        state = {
            "errors": [],
            "table_profiles": {
                "db.dwd_detail": "表: db.dwd_detail\n数据探查: 探查超时（10 分钟未返回）——超时未探查，枚举/画像值未实测",
                "db.dim_site": "表: db.dim_site（探查分区 20251231，以下为平台实测数据）",
            },
        }
        ds = _collect_degradations(state)
        assert len(ds) == 1
        assert "探查超时" in ds[0] and "db.dwd_detail" in ds[0]

    def test_regular_sql_errors_not_degradations(self):
        """试跑 errors 是真实 SQL 缺陷（修复环职责），不算降级。"""
        state = {
            "errors": [],
            "trial_run_result": {
                "total": 3,
                "passed": 2,
                "errors": ["SELECT #2: 编译失败"],
                "env_errors": [],
            },
        }
        assert _collect_degradations(state) == []


class TestFormatBanner:
    def test_banner_lists_each_degradation(self):
        text = format_degradation_banner(["试跑环境失败", "血缘生成失败"])
        assert "⚠️" in text and "2 项" in text
        assert "试跑环境失败" in text and "血缘生成失败" in text

    def test_empty_degradations_empty_text(self):
        assert format_degradation_banner([]) == ""


class TestReportBanner:
    def test_delivery_report_contains_banner(self):
        from src.aqueduct.engine.nodes.report import _generate_delivery_report

        state = {
            "metadata": {"requirement_name": "t"},
            "artifacts": [],
            "errors": [],
            "lineage_result": {"error": "血缘生成异常"},
        }
        report = _generate_delivery_report(state)
        assert "未验证降级" in report and "血缘生成异常" in report

    def test_clean_report_has_no_banner(self):
        from src.aqueduct.engine.nodes.report import _generate_delivery_report

        state = {"metadata": {"requirement_name": "t"}, "artifacts": [], "errors": []}
        assert "未验证降级" not in _generate_delivery_report(state)


class TestResultProperty:
    def test_aqueduct_result_degradations(self):
        from src.aqueduct.core import AqueductResult

        state = {
            "errors": [],
            "trial_run_result": {"total": 1, "passed": 0, "errors": [], "env_errors": ["x"]},
        }
        assert len(AqueductResult(state).degradations) == 1


class TestLineageFailureMarker:
    def test_wait_for_lineage_failure_writes_error_marker(self):
        fut: Future = Future()
        fut.set_exception(RuntimeError("网络不可达"))
        state = {"_lineage_future": fut}
        wait_for_lineage(state)  # 不应抛出
        assert "error" in (state.get("lineage_result") or {})
