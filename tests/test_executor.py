"""SQLExecutorTool 单元测试。

覆盖工具注册、配置检测、健康检查、批量执行。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.aqueduct.tools.registry import get_tool, is_tool_registered


class TestExecutorRegistration:
    """工具注册测试。"""

    def test_executor_registered(self):
        """executor 工具注册成功。"""
        assert is_tool_registered("executor")

    def test_get_tool_returns_instance(self):
        """get_tool('executor') 返回有效实例。"""
        tool = get_tool("executor")
        assert tool.name == "executor"
        assert tool.description != ""


class TestExecutorHealthCheck:
    """健康检查测试。"""

    def test_health_check_without_config(self):
        """未配置 DP_* 环境变量时 health_check 返回 unavailable。"""
        tool = get_tool("executor")
        result = tool.health_check()
        assert result.success is False
        assert result.data["status"] == "unavailable"
        assert "数据平台" in result.data["message"]

    def test_health_check_with_mock_config(self):
        """Mock adapter 存在时 health_check 返回 ok。"""
        tool = get_tool("executor")
        mock_adapter = MagicMock()
        mock_adapter.execute_hive_query.return_value = {
            "status": "success",
            "data": [],
            "row_count": 0,
        }
        tool._hive_tool.adapter = mock_adapter
        tool._hive_tool._init_error = None

        result = tool.health_check()
        assert result.success is True
        assert result.data["status"] == "ok"

        # 清理
        tool._hive_tool.adapter = None


class TestExecutorExecute:
    """单条执行测试。"""

    def test_execute_without_config(self):
        """未配置时 execute 返回 success=False + 明确错误。"""
        tool = get_tool("executor")
        result = tool.execute(sql="SELECT 1")
        assert result.success is False
        assert "数据平台" in result.error or "未配置" in result.error

    def test_execute_with_mock(self):
        """Mock adapter 时 execute 返回成功结果。"""
        tool = get_tool("executor")
        mock_adapter = MagicMock()
        mock_adapter.execute_hive_query.return_value = {
            "status": "success",
            "data": [{"cnt": 42}],
            "row_count": 1,
        }
        tool._hive_tool.adapter = mock_adapter
        tool._hive_tool._init_error = None

        result = tool.execute(sql="SELECT COUNT(*) as cnt FROM test_table")
        assert result.success is True
        assert result.data["row_count"] == 1
        assert result.data["rows"] == [[42]]
        assert result.data["sql_preview"] != ""

        # 清理
        tool._hive_tool.adapter = None


class TestExecutorBatch:
    """批量执行测试。"""

    def test_execute_batch_empty(self):
        """空 SQL 列表返回空结果。"""
        tool = get_tool("executor")
        result = tool.execute_batch(sqls=[])
        assert result.success is True
        assert result.data["total"] == 0
        assert result.data["results"] == []

    def test_execute_batch_partial_fail(self):
        """部分失败不中断，结果包含每条状态。"""
        tool = get_tool("executor")
        mock_adapter = MagicMock()
        mock_adapter.execute_hive_query.side_effect = [
            {"status": "success", "data": [{"dup": 0}], "row_count": 1},
            RuntimeError("Table not found"),
            {"status": "success", "data": [{"cnt": 100}], "row_count": 1},
        ]
        tool._hive_tool.adapter = mock_adapter
        tool._hive_tool._init_error = None

        result = tool.execute_batch(sqls=[
            "SELECT COUNT(*) as dup FROM t HAVING cnt > 1",
            "SELECT * FROM nonexistent_table",
            "SELECT COUNT(*) as cnt FROM good_table",
        ])

        assert result.success is True  # batch 本身成功
        assert result.data["total"] == 3
        assert result.data["passed"] == 2
        assert result.data["failed"] == 1
        assert result.data["results"][0]["success"] is True
        assert result.data["results"][1]["success"] is False
        assert "Table not found" in result.data["results"][1]["error"]
        assert result.data["results"][2]["success"] is True

        # 清理
        tool._hive_tool.adapter = None


class TestDQCNodeIntegration:
    """DQC 节点集成测试。"""

    def test_dqc_node_skip_when_no_exec(self):
        """DQC 节点在无执行能力时正常跳过，不抛异常。"""
        from src.aqueduct.engine.nodes.dqc import _auto_execute_dqc

        state: dict = {}
        # 不应抛出异常
        _auto_execute_dqc(state, "SELECT 'DQC-001' AS rule_name, 0 AS cnt;")
        assert state.get("dqc_execution_skipped") is True


