"""call_llm 网络瞬时错误重试单元测试。

2026-10-15 实录：UI 跑真实需求当夜网络抖动，血缘生成与 DQC 拆分生成
（都走 call_llm）裸死降级。a7bd844 已给试跑加了 3s/6s 小步重试，本批把
同款重试收敛进 call_llm——所有 LLM 调用（含血缘/DQC/各 Phase）自动受益。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.aqueduct.engine.nodes import helpers
from src.aqueduct.engine.nodes.helpers import call_llm


class FlakyLLM:
    """前 N 次抛指定异常，之后返回正常响应的假 LLM。"""

    model_id = "fake-model"

    def __init__(self, exc, fail_times, content="-- ok"):
        self._exc = exc
        self._fail_times = fail_times
        self._content = content
        self.chat_calls = 0

    def chat(self, messages, max_tokens=None, thinking_budget=None):
        self.chat_calls += 1
        if self.chat_calls <= self._fail_times:
            raise self._exc
        return SimpleNamespace(content=self._content, usage=None)


class FakeRouter:
    def __init__(self, llm):
        self._llm = llm

    def route(self, task_type):
        return self._llm


def _state(llm):
    return {"metadata": {"requirement_name": "t"}, "_llm_router": FakeRouter(llm)}


@pytest.fixture()
def _no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(helpers.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


class TestCallLlmNetworkRetry:
    def test_transient_error_retried_then_succeeds(self, _no_sleep):
        llm = FlakyLLM(ConnectionResetError("[WinError 10054] 连接中断"), fail_times=2)
        out = call_llm(_state(llm), "sql_gen", "生成 SQL")
        assert out == "-- ok"
        assert llm.chat_calls == 3
        assert _no_sleep == [3.0, 6.0]

    def test_non_transient_error_raises_immediately(self, _no_sleep):
        llm = FlakyLLM(ValueError("prompt 非法"), fail_times=99)
        with pytest.raises(ValueError):
            call_llm(_state(llm), "sql_gen", "生成 SQL")
        assert llm.chat_calls == 1
        assert _no_sleep == []

    def test_transient_retries_exhausted_raises(self, _no_sleep):
        llm = FlakyLLM(ConnectionError("getaddrinfo failed"), fail_times=99)
        with pytest.raises(ConnectionError):
            call_llm(_state(llm), "lineage_gen", "生成血缘")
        assert llm.chat_calls == 3
        assert _no_sleep == [3.0, 6.0]


class TestRegexShared:
    def test_sql_module_reuses_helpers_pattern(self):
        """试跑（sql.py）与 call_llm 必须共用同一瞬时错误判据，避免漂移。"""
        from src.aqueduct.engine.nodes import sql

        assert sql._RE_NETWORK_TRANSIENT is helpers.RE_NETWORK_TRANSIENT
        assert sql._TRIAL_RETRY_DELAYS == helpers.NETWORK_RETRY_DELAYS
