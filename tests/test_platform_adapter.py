"""平台适配器（开源通用化）测试 — PlatformAdapter 协议 + 空 adapter。

设计来源：知识库《平台适配器架构-开源通用化设计》。
六动词语义端口（查元数据/执行SQL/查血缘/执行DQC/任务部署运维/检索历史交付物）
+ has_capability 能力探测 + auto 探测语义与既有各触点门控 1:1 对齐（零行为变化）
+ none 强制离线（管道照跑：Phase1 静态分析、Phase4 本地校验、Phase5 SQL 供手工执行）。

关键约束：
- auto 探测必须复刻既有谓词：table_metadata ← MCPConfig().is_configured()；
  sql_execute/dqc_execute ← settings.execution_enabled is True（P1-2 严格口径）
- adapter 是模块级单例、不进 state——避免 _llm_router 式 checkpoint 序列化失败
- 平台能力门禁替换四处隐式触点：requirement 表结构查询 / sql 试跑 / review
  试跑门禁 / dqc 执行开关，替换后 auto 语义行为不变
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.aqueduct.config.settings import Settings, get_settings
from src.aqueduct.exceptions import ConfigError


@pytest.fixture(autouse=True)
def _reset_adapter_singleton():
    """每个用例前后清空 adapter 单例，防串味。"""
    from src.aqueduct.platform import reset_platform_adapter

    reset_platform_adapter()
    yield
    reset_platform_adapter()


# ============ 能力常量 ============


class TestCapabilityConstants:
    def test_six_capabilities(self):
        """六动词端口：设计文档定死的语义能力集，十年内不变的那个集合。"""
        from src.aqueduct.platform import ALL_CAPABILITIES

        assert (
            frozenset(
                {
                    "table_metadata",
                    "sql_execute",
                    "lineage",
                    "dqc_execute",
                    "task_ops",
                    "artifact_search",
                }
            )
            == ALL_CAPABILITIES
        )


# ============ NoneAdapter（空 adapter / 强制离线） ============


class TestNoneAdapter:
    def test_name_and_zero_capabilities(self):
        from src.aqueduct.platform import NoneAdapter

        adapter = NoneAdapter()
        assert adapter.name == "none"
        assert adapter.capabilities() == frozenset()

    def test_has_capability_false_for_all_six(self):
        from src.aqueduct.platform import ALL_CAPABILITIES, NoneAdapter

        adapter = NoneAdapter()
        for cap in ALL_CAPABILITIES:
            assert adapter.has_capability(cap) is False

    def test_has_capability_unknown_raises(self):
        from src.aqueduct.platform import NoneAdapter

        with pytest.raises(ValueError, match="未知能力"):
            NoneAdapter().has_capability("teleport")

    def test_health_check_unavailable_offline(self):
        from src.aqueduct.platform import NoneAdapter

        health = NoneAdapter().health_check()
        assert health["status"] == "unavailable"
        assert "离线" in health["message"]


# ============ AutoAdapter（探测语义与既有门控 1:1） ============


class TestAutoAdapter:
    def test_both_signals_full_capabilities(self):
        from src.aqueduct.platform import AutoAdapter

        adapter = AutoAdapter(mcp_configured=True, execution_enabled=True)
        assert adapter.capabilities() == frozenset({"table_metadata", "sql_execute", "dqc_execute"})

    def test_mcp_only(self):
        from src.aqueduct.platform import AutoAdapter

        adapter = AutoAdapter(mcp_configured=True, execution_enabled=False)
        assert adapter.capabilities() == frozenset({"table_metadata"})

    def test_execution_only(self):
        from src.aqueduct.platform import AutoAdapter

        adapter = AutoAdapter(mcp_configured=False, execution_enabled=True)
        assert adapter.capabilities() == frozenset({"sql_execute", "dqc_execute"})

    def test_no_signals_zero_capabilities(self):
        from src.aqueduct.platform import AutoAdapter

        adapter = AutoAdapter(mcp_configured=False, execution_enabled=False)
        assert adapter.capabilities() == frozenset()

    def test_never_claims_unimplemented_capabilities(self):
        """lineage/task_ops/artifact_search 尚无实现，任何情况下不得谎报。"""
        from src.aqueduct.platform import AutoAdapter

        adapter = AutoAdapter(mcp_configured=True, execution_enabled=True)
        assert adapter.has_capability("lineage") is False
        assert adapter.has_capability("task_ops") is False
        assert adapter.has_capability("artifact_search") is False

    def test_declared_name_for_bdp(self):
        from src.aqueduct.platform import AutoAdapter

        assert AutoAdapter(mcp_configured=True, execution_enabled=True).name == "auto"
        assert (
            AutoAdapter(declared_name="bdp", mcp_configured=True, execution_enabled=True).name
            == "bdp"
        )

    def test_health_check_delegates_to_executor_when_sql_declared(self):
        from src.aqueduct.platform import AutoAdapter

        adapter = AutoAdapter(mcp_configured=False, execution_enabled=True)
        fake = MagicMock()
        fake.execute.return_value = SimpleNamespace(success=True, data={"status": "ok"})
        with patch("src.aqueduct.tools.registry.get_tool", return_value=fake):
            health = adapter.health_check()
        fake.execute.assert_called_once_with(action="health_check")
        assert health["status"] == "ok"

    def test_health_check_skips_without_sql_capability(self):
        from src.aqueduct.platform import AutoAdapter

        adapter = AutoAdapter(mcp_configured=True, execution_enabled=False)
        health = adapter.health_check()
        assert health["status"] == "unavailable"
        assert "未声明" in health["message"]


# ============ loader：声明解析 + 单例 ============


class TestLoader:
    def test_platform_none_forces_offline_even_with_mcp(self):
        """none 是强制声明：即使 MCP 已配置也不得声明任何能力。"""
        from src.aqueduct.platform import NoneAdapter, load_adapter

        settings = Settings(platform="none")
        with patch("src.aqueduct.mcp.config.MCPConfig.is_configured", return_value=True):
            adapter = load_adapter(settings)
        assert isinstance(adapter, NoneAdapter)

    def test_auto_resolves_from_mcp_and_execution(self):
        from src.aqueduct.platform import AutoAdapter, load_adapter

        settings = Settings(platform="auto", execution_enabled=True)
        with patch("src.aqueduct.mcp.config.MCPConfig.is_configured", return_value=True):
            adapter = load_adapter(settings)
        assert isinstance(adapter, AutoAdapter)
        assert "table_metadata" in adapter.capabilities()
        assert "sql_execute" in adapter.capabilities()

    def test_auto_without_any_platform_signals(self):
        from src.aqueduct.platform import load_adapter

        settings = Settings(platform="auto", execution_enabled=False)
        with patch("src.aqueduct.mcp.config.MCPConfig.is_configured", return_value=False):
            adapter = load_adapter(settings)
        assert adapter.capabilities() == frozenset()

    def test_bdp_declared_keeps_name(self):
        from src.aqueduct.platform import load_adapter

        settings = Settings(platform="bdp", execution_enabled=True)
        with patch("src.aqueduct.mcp.config.MCPConfig.is_configured", return_value=True):
            adapter = load_adapter(settings)
        assert adapter.name == "bdp"

    def test_unknown_platform_rejected_at_settings(self):
        """配置错误 fail fast：Settings 构造时即拒绝，不带病运行。"""
        with pytest.raises(ConfigError, match="platform"):
            Settings(platform="acme")

    def test_get_platform_adapter_cached_singleton(self):
        from src.aqueduct.platform import get_platform_adapter

        first = get_platform_adapter()
        assert get_platform_adapter() is first

    def test_reset_platform_adapter_reloads(self):
        from src.aqueduct.platform import get_platform_adapter, reset_platform_adapter

        first = get_platform_adapter()
        reset_platform_adapter()
        assert get_platform_adapter() is not first

    def test_env_var_platform_none(self, monkeypatch):
        """AQUEDUCT_PLATFORM=none 环境变量声明强制离线。"""
        from src.aqueduct.platform import get_platform_adapter

        monkeypatch.setenv("AQUEDUCT_PLATFORM", "none")
        get_settings.cache_clear()
        try:
            adapter = get_platform_adapter()
            assert adapter.name == "none"
            assert adapter.capabilities() == frozenset()
        finally:
            get_settings.cache_clear()


# ============ 平台横幅（管道启动可见性） ============


class TestPlatformBanner:
    def test_describe_adapter_none(self):
        from src.aqueduct.platform import NoneAdapter, describe_adapter

        text = describe_adapter(NoneAdapter())
        assert "none" in text
        assert "离线" in text or "无" in text

    def test_describe_adapter_auto_lists_capabilities(self):
        from src.aqueduct.platform import AutoAdapter, describe_adapter

        text = describe_adapter(AutoAdapter(mcp_configured=True, execution_enabled=True))
        assert "table_metadata" in text
        assert "sql_execute" in text

    def test_log_platform_banner(self, caplog):
        from src.aqueduct.platform import log_platform_banner

        with caplog.at_level(logging.INFO, logger="src.aqueduct.platform"):
            log_platform_banner()
        assert any("[platform]" in r.message for r in caplog.records)


# ============ 门禁接线：auto 语义不变、none 强制降级 ============


class TestRequirementMetadataGate:
    """Phase 1 表结构查询：none → 跳过（静态分析）；auto+MCP → 照常查询。"""

    def _state(self):
        return {"requirement": "从 orders.ods_order_detail 读取明细，按天统计订单量"}

    def test_none_skips_even_if_mcp_configured(self, caplog):
        from src.aqueduct.platform import NoneAdapter

        with (
            patch("src.aqueduct.mcp.config.MCPConfig.is_configured", return_value=True),
            patch("src.aqueduct.platform.get_platform_adapter", return_value=NoneAdapter()),
            caplog.at_level(logging.INFO, logger="src.aqueduct.engine.nodes.requirement"),
        ):
            from src.aqueduct.engine.nodes.requirement import _query_table_schemas

            result = _query_table_schemas(self._state())

        assert result == {}
        assert any("静态分析" in r.message for r in caplog.records)
        assert not any("尝试查询" in r.message for r in caplog.records)

    def test_auto_with_mcp_passes_gate(self, caplog):
        """auto + MCP 已配置 → 门禁放行（到达"尝试查询"日志即证明未被拦）。"""
        with (
            patch("src.aqueduct.mcp.config.MCPConfig.is_configured", return_value=True),
            patch("src.aqueduct.mcp.client.SyncMCPClient", return_value=MagicMock()),
            caplog.at_level(logging.INFO, logger="src.aqueduct.engine.nodes.requirement"),
        ):
            from src.aqueduct.engine.nodes.requirement import _query_table_schemas

            _query_table_schemas(self._state())

        assert any("尝试查询" in r.message for r in caplog.records)


class TestSqlTrialGate:
    """Phase 4 试跑：none → 跳过（不打数据平台）；auto + execution_enabled=True → 不变。"""

    def test_none_skips_trial(self, caplog):
        from src.aqueduct.platform import NoneAdapter

        state = {"sql_content": "select 1", "_sql_path": None}
        with (
            patch("src.aqueduct.platform.get_platform_adapter", return_value=NoneAdapter()),
            patch("src.aqueduct.engine.nodes.sql._run_trial_selects") as trial_mock,
            caplog.at_level(logging.DEBUG, logger="src.aqueduct.engine.nodes.sql"),
        ):
            from src.aqueduct.engine.nodes.sql import _auto_trial_run

            _auto_trial_run(state, "whatever.sql")

        trial_mock.assert_not_called()
        assert any("试跑跳过" in r.message for r in caplog.records)


class TestReviewTrialGate:
    """P1-2 试跑门禁（review 侧）：none → 零 issues（不进修复循环）。"""

    def test_none_returns_no_issues(self):
        from src.aqueduct.platform import NoneAdapter

        state = {
            "sql_content": (
                "select a, b from orders.ods_order_detail "
                "where inc_day = '2026-01-01' and order_status in ('1','2') and amount > 0"
            )
        }
        with patch("src.aqueduct.platform.get_platform_adapter", return_value=NoneAdapter()):
            from src.aqueduct.engine.nodes.review import _trial_run_issues

            issues = _trial_run_issues(state)
        assert issues == []


class TestDqcExecuteGate:
    """Phase 5 DQC 执行开关：none → 用例标记 SKIPPED（SQL 供手工执行）。"""

    def test_none_marks_skipped(self, tmp_path):
        from src.aqueduct.platform import NoneAdapter

        dqc_sql = (
            "-- [完整性-订单量非零] 订单表行数应大于 0\n"
            "-- 预期: 行数 > 0\n"
            "SELECT COUNT(*) FROM orders.ods_order_detail WHERE inc_day='2026-01-01';\n"
        )
        dqc_file = tmp_path / "dqc.sql"
        dqc_file.write_text(dqc_sql, encoding="utf-8")

        with patch("src.aqueduct.platform.get_platform_adapter", return_value=NoneAdapter()):
            from src.aqueduct.tools.dqc import DQCTool

            result = DQCTool().execute(dqc_sql=str(dqc_file))

        assert result.success is True
        statuses = {c["status"] for c in result.data["results"]}
        assert statuses == {"SKIPPED"}
