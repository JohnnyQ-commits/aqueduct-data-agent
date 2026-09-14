"""DataPlatformAdapter 响应信封与三步协议测试。

缺陷实录一（2026-09-12 cookie 刷新后冒烟发现）：_hive_submit/_hive_fetch 硬编码
旧信封 ``{code: 200, data: {...}}``，而平台 execute 端点实际返回
``{ok: True, data: 58001942, message: None}``——提交真实成功却被判
"提交失败"，``data`` 裸 int 即 executionId 本体。这是执行链路假死的第二层
原因（第一层是 cookie 过期 302）。解析须双信封兼容。

缺陷实录二（2026-09-14 用户 DevTools 截图揭端点 + 全链路实测）：文档三步协议
的轮询/取结果端点从未实测通过——真实协议为：

- 提交 ``POST /hive/execute`` → ``{ok, data: <executionId int>}``
- 轮询 ``GET /hive/getLog?clusterId&windowId&executionId`` →
  ``{ok, data: {isFinish, isSuccess, resultId: <uuid>, log[]}}``
- 取结果 ``GET /hive/getResult?resultId&windowId&clusterId`` →
  ``{ok, data: [行字典]}``——**不能带 userId**（带则 500）

文档的 ``POST /hive/executionStatus``（resultId 写成 int）与
``POST /hive/result`` 均 404，端点名/方法/resultId 类型全错。
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:  # 模拟 2xx
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """按序返回预设响应，记录请求。"""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, endpoint: str, json: dict[str, Any]) -> _FakeResponse:
        self.calls.append(("POST", endpoint, json))
        return _FakeResponse(self._responses.pop(0))

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.calls.append(("GET", endpoint, params or {}))
        return _FakeResponse(self._responses.pop(0))


def _make_adapter(monkeypatch) -> Any:
    """无真实凭证构造 adapter（env 打桩），client 换成 FakeClient。"""
    from src.aqueduct.mcp.adapters.dp_client import DataPlatformAdapter

    for key, val in (
        ("DP_BASE_URL", "https://dp.example.com"),
        ("DP_COOKIE", "session=test"),
        ("DP_USER_ID", "01234567"),
    ):
        monkeypatch.setenv(key, val)
    return DataPlatformAdapter()


class TestHiveSubmitEnvelope:
    """_hive_submit 双信封兼容。"""

    def test_ok_envelope_bare_int_data_is_execution_id(self, monkeypatch):
        """新信封 {ok:True, data:<int>}：data 即 executionId 本体。"""
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"ok": True, "data": 58001942, "message": None}])

        exec_id = adapter._hive_submit("select 1", "copilot_test")

        assert exec_id == 58001942

    def test_legacy_code_envelope_dict_data_still_accepted(self, monkeypatch):
        """旧信封 {code:200, data:{executionId:...}} 保持兼容。"""
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"code": 200, "data": {"executionId": 123}, "message": None}])

        assert adapter._hive_submit("select 1", "copilot_test") == 123

    def test_ok_false_raises(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"ok": False, "data": None, "message": "denied"}])

        with pytest.raises(RuntimeError, match="提交失败"):
            adapter._hive_submit("select 1", "copilot_test")

    def test_code_500_raises(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"code": 500, "data": None, "message": "boom"}])

        with pytest.raises(RuntimeError, match="提交失败"):
            adapter._hive_submit("select 1", "copilot_test")


class TestHiveWaitGetLog:
    """_hive_wait 真实协议：GET getLog 轮询 isFinish，resultId 为 UUID。"""

    def test_polls_until_finish_returns_uuid_result_id(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient(
            [
                {"ok": True, "data": {"isFinish": False, "log": ["INFO : running"]}},
                {
                    "ok": True,
                    "data": {
                        "isFinish": True,
                        "isSuccess": True,
                        "resultId": "ee1971e5-299f-4e07-82a2-150b9e39dc8e",
                        "log": [],
                    },
                },
            ]
        )

        result_id = adapter._hive_wait(58008694, "bb5d6958-window")

        assert result_id == "ee1971e5-299f-4e07-82a2-150b9e39dc8e"
        method, endpoint, params = adapter.client.calls[-1]
        assert (method, endpoint) == ("GET", "/bdp-fc-ide-external-controller/hive/getLog")
        assert params["executionId"] == 58008694
        assert params["windowId"] == "bb5d6958-window"
        assert params["clusterId"] == 1

    def test_failed_job_raises(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient(
            [
                {
                    "ok": True,
                    "data": {
                        "isFinish": True,
                        "isSuccess": False,
                        "message": "compiled error",
                        "resultId": None,
                    },
                }
            ]
        )

        with pytest.raises(RuntimeError, match="任务执行失败"):
            adapter._hive_wait(58008694, "win")


class TestHiveFetchGetResult:
    """_hive_fetch 真实协议：GET getResult，参数 resultId+windowId+clusterId，
    绝不带 userId（带则平台 500——live 实测的参数陷阱）。"""

    def test_returns_rows_without_user_id_param(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"ok": True, "data": [{"probe2": "1"}], "message": None}])

        rows = adapter._hive_fetch("8f1a73ae-9b57-49e1-80a1-0d9563dc07f0", "bb5d6958-window")

        assert rows == [{"probe2": "1"}]
        method, endpoint, params = adapter.client.calls[-1]
        assert (method, endpoint) == (
            "GET",
            "/bdp-fc-ide-external-controller/hive/getResult",
        )
        assert params["resultId"] == "8f1a73ae-9b57-49e1-80a1-0d9563dc07f0"
        assert params["windowId"] == "bb5d6958-window"
        assert params["clusterId"] == 1
        assert "userId" not in params

    def test_null_data_returns_empty_list(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"ok": True, "data": None, "message": None}])

        assert adapter._hive_fetch("rid", "win") == []

    def test_failure_envelope_raises(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"ok": False, "data": None, "message": "gone"}])

        with pytest.raises(RuntimeError, match="获取结果失败"):
            adapter._hive_fetch("rid", "win")


class TestExecuteHiveQueryFullChain:
    """execute_hive_query 三步集成（假 client 串全链）：提交→轮询→取结果。"""

    def test_select_statement_full_chain(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient(
            [
                {"ok": True, "data": 58008694, "message": None},  # submit
                {
                    "ok": True,
                    "data": {
                        "isFinish": True,
                        "isSuccess": True,
                        "resultId": "rid-uuid",
                        "log": [],
                    },
                },  # poll
                {"ok": True, "data": [{"a": "1"}], "message": None},  # fetch
            ]
        )

        result = adapter.execute_hive_query("select * from t;")

        assert result["status"] == "success"
        assert result["data"] == [{"a": "1"}]
        assert result["row_count"] == 1
        # 三步端点顺序与方法
        assert [c[0] for c in adapter.client.calls] == ["POST", "GET", "GET"]

    def test_ddl_skips_fetch(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient(
            [
                {"ok": True, "data": 58008694, "message": None},
                {
                    "ok": True,
                    "data": {
                        "isFinish": True,
                        "isSuccess": True,
                        "resultId": "rid-uuid",
                        "log": [],
                    },
                },
            ]
        )

        result = adapter.execute_hive_query("CREATE TABLE x (id INT)")

        assert result == {"status": "success", "data": [], "row_count": 0}
        assert [c[0] for c in adapter.client.calls] == ["POST", "GET"]
