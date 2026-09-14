"""DataPlatformAdapter 响应信封解析测试。

缺陷实录（2026-09-12 cookie 刷新后冒烟发现）：_hive_submit/_hive_fetch 硬编码
旧信封 ``{code: 200, data: {...}}``，而平台 execute 端点实际返回
``{ok: True, data: 58001942, message: None}``——提交真实成功却被判
"提交失败"，``data`` 裸 int 即 executionId 本体。这是执行链路假死的第二层
原因（第一层是 cookie 过期 302）。解析须双信封兼容。
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
    """按序返回预设响应，记录请求 payload。"""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = responses
        self.payloads: list[dict[str, Any]] = []

    def post(self, endpoint: str, json: dict[str, Any]) -> _FakeResponse:
        self.payloads.append(json)
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


class TestHiveFetchEnvelope:
    """_hive_fetch 双信封兼容。"""

    def test_ok_envelope_returns_records(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient(
            [{"ok": True, "data": {"records": [{"a": 1}]}, "message": None}]
        )

        assert adapter._hive_fetch(9, "copilot_test") == [{"a": 1}]

    def test_legacy_code_envelope_returns_records(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"code": 200, "data": {"records": []}}])

        assert adapter._hive_fetch(9, "copilot_test") == []

    def test_failure_envelope_raises(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter.client = _FakeClient([{"ok": False, "data": None, "message": "gone"}])

        with pytest.raises(RuntimeError, match="获取结果失败"):
            adapter._hive_fetch(9, "copilot_test")
