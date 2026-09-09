"""LLM 思考预算按任务分档测试（PERF-8）。

此前思考预算是全局单值（AQUEDUCT_LLM_THINKING_BUDGET_TOKENS）——重思考任务
（sql_gen/sql_review 健康分布 17000~22000）与轻量任务（summarize/统计）共用
一档，只能按最重任务取值。机制：按任务类型覆盖映射
AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK='{"sql_gen": 20000, "summarize": 1024}'
（JSON），解析链三层——Settings.thinking_budget_for（任务覆盖 > 全局回退）
→ helpers.call_llm 把解析结果经 chat(thinking_budget=...) 传入
→ _chat_sdk kwargs 优先、未传回退全局单值（直接调 chat 的旧路径不变）。
默认空映射 = 零行为变化。
"""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest

from src.aqueduct.config.settings import get_settings
from src.aqueduct.engine.nodes.helpers import call_llm
from src.aqueduct.llm.base import LLMMessage, LLMResponse
from src.aqueduct.llm.claude import ClaudeLLM

_MODEL = "claude-sonnet-4-6-20250514"


@pytest.fixture
def fresh_settings():
    """每个测试前后清空 settings 缓存，确保环境变量修改生效。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================
# 层 1: Settings 解析 —— 任务覆盖 > 全局回退
# ============================================================


class TestBudgetResolution:
    """Settings.thinking_budget_for(task_type) 解析规则。"""

    def test_default_empty_mapping_falls_back_to_global(self, fresh_settings, monkeypatch):
        """默认空映射：所有任务回退全局单值（零行为变化的锚定测试）。"""
        monkeypatch.delenv("AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK", raising=False)
        monkeypatch.delenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", raising=False)
        settings = get_settings()
        assert settings.llm_thinking_budget_by_task == {}
        assert settings.thinking_budget_for("sql_gen") == settings.llm_thinking_budget_tokens

    def test_per_task_override_wins_unlisted_falls_back(self, fresh_settings, monkeypatch):
        """映射内的任务用覆盖值，未列出的任务回退全局单值。"""
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "1024")
        monkeypatch.setenv(
            "AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK", '{"sql_gen": 20000, "sql_review": 22000}'
        )
        settings = get_settings()
        assert settings.thinking_budget_for("sql_gen") == 20000
        assert settings.thinking_budget_for("sql_review") == 22000
        assert settings.thinking_budget_for("doc_gen") == 1024

    def test_env_json_mapping_parsed(self, fresh_settings, monkeypatch):
        """JSON 环境变量解析为 dict[str, int]。"""
        monkeypatch.setenv(
            "AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK", '{"sql_gen": 20000, "summarize": 1024}'
        )
        assert get_settings().llm_thinking_budget_by_task == {
            "sql_gen": 20000,
            "summarize": 1024,
        }

    def test_zero_override_disables_thinking_for_task(self, fresh_settings, monkeypatch):
        """映射值 0 = 该任务不发思考参数（可对轻量任务单独关闭）。"""
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "1024")
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK", '{"summarize": 0}')
        assert get_settings().thinking_budget_for("summarize") == 0


# ============================================================
# 层 2: helpers.call_llm —— 解析结果传入 chat kwargs
# ============================================================


class _FakeLLM:
    model_id = "fake-model"

    def __init__(self):
        self.chat_kwargs: list[dict] = []

    def chat(self, messages, **kwargs):
        self.chat_kwargs.append(kwargs)
        return LLMResponse(content="正文内容", model="fake-model")


class _FakeRouter:
    def __init__(self, llm: _FakeLLM):
        self._llm = llm

    def route(self, task_type: str):
        return self._llm


class TestCallLlmPassesBudget:
    """call_llm 把按任务解析的预算经 thinking_budget kwarg 传给 LLM。"""

    def test_call_llm_passes_resolved_budget(self, fresh_settings, monkeypatch):
        """映射内任务：chat 收到 thinking_budget=覆盖值。"""
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "1024")
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK", '{"sql_gen": 20000}')

        llm = _FakeLLM()
        state = {"metadata": {"requirement_name": "perf8_test"}, "_llm_router": _FakeRouter(llm)}
        with patch("src.aqueduct.engine.nodes.helpers._strip_llm_meta", side_effect=lambda c: c):
            call_llm(state, "sql_gen", "写 SQL")

        assert llm.chat_kwargs
        assert llm.chat_kwargs[0].get("thinking_budget") == 20000

    def test_call_llm_passes_global_when_no_mapping(self, fresh_settings, monkeypatch):
        """无映射：chat 收到 thinking_budget=全局单值（透传，非丢弃）。"""
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "1024")
        monkeypatch.delenv("AQUEDUCT_LLM_THINKING_BUDGET_BY_TASK", raising=False)

        llm = _FakeLLM()
        state = {"metadata": {"requirement_name": "perf8_test"}, "_llm_router": _FakeRouter(llm)}
        with patch("src.aqueduct.engine.nodes.helpers._strip_llm_meta", side_effect=lambda c: c):
            call_llm(state, "doc_gen", "写文档")

        assert llm.chat_kwargs
        assert llm.chat_kwargs[0].get("thinking_budget") == 1024


# ============================================================
# 层 3: _chat_sdk —— kwargs 优先，未传回退全局
# ============================================================


def _install_fake_anthropic(monkeypatch, recorded: dict) -> None:
    """注入伪 anthropic 模块，记录 messages.stream 的调用参数。"""

    class FakeStream:
        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_final_message(self):
            return object()

    class FakeMessages:
        def stream(self, **kwargs):
            recorded.update(kwargs)
            return FakeStream()

    class FakeAnthropic:
        messages = FakeMessages()

        def __init__(self, **kwargs):
            pass

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)


class TestSdkAppliesKwargBudget:
    """SDK 后端：显式 thinking_budget kwarg 优先于 settings 全局值。"""

    def test_sdk_kwarg_budget_overrides_settings(self, fresh_settings, monkeypatch):
        """全局 0（不发参数）时，显式 kwarg 4000 仍生效。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.delenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", raising=False)
        ClaudeLLM._shared_sdk_clients.clear()

        llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")
        recorded: dict = {}
        _install_fake_anthropic(monkeypatch, recorded)

        llm.chat([LLMMessage(role="user", content="t")], thinking_budget=4000)

        assert recorded.get("thinking") == {"type": "enabled", "budget_tokens": 4000}

    def test_sdk_kwarg_zero_omits_param(self, fresh_settings, monkeypatch):
        """显式 kwarg 0（任务级关闭）不发 thinking 参数。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "1024")
        ClaudeLLM._shared_sdk_clients.clear()

        llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")
        recorded: dict = {}
        _install_fake_anthropic(monkeypatch, recorded)

        llm.chat([LLMMessage(role="user", content="t")], thinking_budget=0)

        assert "thinking" not in recorded

    def test_sdk_without_kwarg_keeps_settings_fallback(self, fresh_settings, monkeypatch):
        """直接调 chat 不传 kwarg：回退全局单值（旧路径行为锚定）。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "1024")
        ClaudeLLM._shared_sdk_clients.clear()

        llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")
        recorded: dict = {}
        _install_fake_anthropic(monkeypatch, recorded)

        llm.chat([LLMMessage(role="user", content="t")])

        assert recorded.get("thinking") == {"type": "enabled", "budget_tokens": 1024}

    def test_sdk_kwarg_budget_capped_below_max_tokens(self, fresh_settings, monkeypatch):
        """kwarg 预算 ≥ max_tokens 时截断到 max_tokens // 2（与全局值同规则）。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.delenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", raising=False)
        ClaudeLLM._shared_sdk_clients.clear()

        llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")
        recorded: dict = {}
        _install_fake_anthropic(monkeypatch, recorded)

        llm.chat([LLMMessage(role="user", content="t")], max_tokens=32768, thinking_budget=65536)

        assert recorded.get("thinking") == {"type": "enabled", "budget_tokens": 16384}
