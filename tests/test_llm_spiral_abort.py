"""SDK 流式思考螺旋提前中止测试（PERF-10）。

实测（2026-08-31 真实 sql_gen prompt 探针）：网关对 thinking.budget_tokens
完全不生效——budget=1024 与 8192 均烧光 32768 max_tokens 仍零正文（597s/1014s）。
健康调用思考 ~9000 tokens 后出正文（design_ddl 实测 completion=13736、正文 4788 字符）。

流式监听 message_delta 的 output_tokens：超过阈值仍无正文 → 判定思考螺旋，
提前中止返回空内容，交由 helpers.call_llm 现有空响应重试（非确定性螺旋，
重试即重新掷骰子），把 ~1000s 的注定失败降到阈值/32tok/s。
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from src.aqueduct.config.settings import get_settings
from src.aqueduct.llm.base import LLMMessage
from src.aqueduct.llm.claude import ClaudeLLM

_MODEL = "glm-5.3"


@pytest.fixture
def fresh_settings():
    """每个测试前后清空 settings 缓存，确保环境变量修改生效。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _event(etype, delta=None, usage=None):
    return SimpleNamespace(type=etype, delta=delta, usage=usage)


def _delta(dtype, text=""):
    return SimpleNamespace(type=dtype, text=text)


class FakeEventStream:
    """伪 MessageStream：按事件列表迭代，记录消费进度（验证提前中止）。"""

    def __init__(self, events):
        self._events = events
        self.consumed = 0
        self.exited = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.consumed >= len(self._events):
            raise StopIteration
        event = self._events[self.consumed]
        self.consumed += 1
        return event

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exited = True
        return False

    def get_final_message(self):
        return SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=200,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            stop_reason="end_turn",
        )


def _install_stream(monkeypatch, stream) -> None:
    """注入伪 anthropic 模块，messages.stream 返回给定流。"""

    class FakeMessages:
        @staticmethod
        def stream(**kwargs):
            return stream

    class FakeAnthropic:
        messages = FakeMessages()

        def __init__(self, **kwargs):
            pass

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)


def _make_llm() -> ClaudeLLM:
    ClaudeLLM._shared_sdk_clients.clear()
    return ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")


class TestSpiralAbortSettings:
    """llm_spiral_abort_tokens 配置项。"""

    def test_settings_default_threshold(self, fresh_settings):
        """默认阈值 12000（健康思考 ~9000 之上、烧光 32768 之下）。"""
        assert get_settings().llm_spiral_abort_tokens == 12000


class TestSpiralAbort:
    """思考螺旋提前中止行为。"""

    def test_spiral_aborts_before_cap(self, fresh_settings, monkeypatch):
        """无正文且 output_tokens 超阈值：提前中止，返回空内容。"""

        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.delenv("AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS", raising=False)

        events = [
            _event("message_start"),
            _event("content_block_start"),  # thinking 块开始
            _event("message_delta", usage=SimpleNamespace(output_tokens=11999)),
            _event("message_delta", usage=SimpleNamespace(output_tokens=12500)),  # 触发中止
            _event("message_delta", usage=SimpleNamespace(output_tokens=32768)),  # 不应到达
        ]
        stream = FakeEventStream(events)
        _install_stream(monkeypatch, stream)

        response = _make_llm().chat([LLMMessage(role="user", content="t")])

        assert response.content == ""  # 空内容 → 触发上层空响应重试
        assert stream.consumed == 4, "应在第 4 个事件处中止，不烧到 32768"
        assert len(events) == 5

    def test_healthy_thinking_then_text_not_aborted(self, fresh_settings, monkeypatch):
        """健康调用（思考 9000 后出正文）不中止；有正文后大 token 数也不中止。"""

        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.delenv("AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS", raising=False)

        events = [
            _event("message_delta", usage=SimpleNamespace(output_tokens=9000)),
            _event("content_block_delta", delta=_delta("text_delta", "SELECT 1")),
            _event("message_delta", usage=SimpleNamespace(output_tokens=9500)),
            _event("message_delta", usage=SimpleNamespace(output_tokens=40000)),
        ]
        stream = FakeEventStream(events)
        _install_stream(monkeypatch, stream)

        response = _make_llm().chat([LLMMessage(role="user", content="t")])

        assert response.content == "SELECT 1"
        assert stream.consumed == 4, "健康调用应消费全部事件"

    def test_zero_threshold_disables_abort(self, fresh_settings, monkeypatch):
        """阈值 0 关闭检测：螺旋跑满也不提前中止（fail-open）。"""

        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.setenv("AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS", "0")

        events = [
            _event("message_delta", usage=SimpleNamespace(output_tokens=16000)),
            _event("message_delta", usage=SimpleNamespace(output_tokens=32768)),
        ]
        stream = FakeEventStream(events)
        _install_stream(monkeypatch, stream)

        response = _make_llm().chat([LLMMessage(role="user", content="t")])

        assert response.content == ""
        assert stream.consumed == 2, "阈值 0 时不提前中止"

    def test_env_override_threshold(self, fresh_settings, monkeypatch):
        """AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS 可覆盖阈值。"""

        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.setenv("AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS", "5000")

        events = [
            _event("message_delta", usage=SimpleNamespace(output_tokens=6000)),
            _event("message_delta", usage=SimpleNamespace(output_tokens=32768)),
        ]
        stream = FakeEventStream(events)
        _install_stream(monkeypatch, stream)

        response = _make_llm().chat([LLMMessage(role="user", content="t")])

        assert response.content == ""
        assert stream.consumed == 1, "阈值 5000 时第一个事件即中止"

    def test_spiral_aborts_on_local_thinking_estimate(self, fresh_settings, monkeypatch):
        """本地思考字符估算触发中止（无任何 message_delta usage 事件）。

        实测（2026-08-31 复测日志）：网关只在流末尾才发 usage 增量——
        message_delta 信号中止时 output_tokens 已是 32768，止损没兑现。
        thinking_delta 文本增量是唯一的中途信号：本地估算累计思考 token。
        """

        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.delenv("AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS", raising=False)

        events = [
            # 思考增量：4000 汉字 ≈ 6000 tokens（estimate_tokens 1.5/字）
            _event("content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="思" * 4000)),
            # 累计 12000 ≥ 阈值 12000 → 中止
            _event("content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="考" * 4000)),
            # 不应到达
            _event("content_block_delta", delta=_delta("text_delta", "SELECT 1")),
        ]
        stream = FakeEventStream(events)
        _install_stream(monkeypatch, stream)

        response = _make_llm().chat([LLMMessage(role="user", content="t")])

        assert response.content == ""
        assert stream.consumed == 2, "本地估算达阈值即中止，不等流末尾 usage"

    def test_healthy_thinking_est_below_threshold_completes(self, fresh_settings, monkeypatch):
        """思考低于阈值的健康调用：本地估算不误杀，正常出正文。"""

        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.delenv("AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS", raising=False)

        events = [
            # 4000 汉字 ≈ 6000 tokens < 12000
            _event("content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="思" * 4000)),
            _event("content_block_delta", delta=_delta("text_delta", "SELECT 1")),
        ]
        stream = FakeEventStream(events)
        _install_stream(monkeypatch, stream)

        response = _make_llm().chat([LLMMessage(role="user", content="t")])

        assert response.content == "SELECT 1"
        assert stream.consumed == 2
