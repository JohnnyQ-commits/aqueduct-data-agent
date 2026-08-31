"""LLM 后端选择与 CLI 超时重试策略单元测试（PERF-1 / PERF-2）。

PERF-1: AQUEDUCT_LLM_BACKEND 环境变量强制后端（auto/sdk/cli），覆盖自动探测。
PERF-2: CLI 后端超时重试保持固定超时 + 指数退避（不再翻倍超时）。
"""

from __future__ import annotations

import subprocess
import sys
import types
from unittest.mock import patch

import pytest

from src.aqueduct.config.settings import get_settings
from src.aqueduct.exceptions import LLMTimeoutError
from src.aqueduct.llm.base import LLMMessage
from src.aqueduct.llm.claude import ClaudeLLM

_MODEL = "claude-sonnet-4-6-20250514"


@pytest.fixture
def fresh_settings():
    """每个测试前后清空 settings 缓存，确保环境变量修改生效。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================
# PERF-1: AQUEDUCT_LLM_BACKEND 后端强制切换
# ============================================================


class TestBackendOverride:
    """AQUEDUCT_LLM_BACKEND=sdk|cli|claude-cli|auto 强制选择后端。"""

    def test_settings_default_backend_auto(self, fresh_settings):
        """Settings 新增 llm_backend 字段，默认 auto。"""
        assert get_settings().llm_backend == "auto"

    def test_sdk_forced_even_when_cli_found(self, fresh_settings, monkeypatch):
        """强制 sdk 时，即使 PATH 上有 claude CLI 也走 SDK 后端。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        with patch.object(ClaudeLLM, "_find_claude_cli", return_value="/fake/claude"):
            llm = ClaudeLLM(model_id=_MODEL)
        assert llm._backend == "sdk"

    def test_cli_forced_even_when_sdk_available(self, fresh_settings, monkeypatch):
        """强制 claude-cli 时，即使 SDK 可用（CLI 缺失）也走 CLI 后端。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "claude-cli")
        # 注入可导入的 anthropic 模块 + 长 api_key，使自动探测本会选 sdk
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = object
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        with patch.object(ClaudeLLM, "_find_claude_cli", return_value=None):
            llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40)
        assert llm._backend == "claude-cli"

    def test_cli_alias_accepted(self, fresh_settings, monkeypatch):
        """cli 是 claude-cli 的别名。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "cli")
        with patch.object(ClaudeLLM, "_find_claude_cli", return_value="/fake/claude"):
            llm = ClaudeLLM(model_id=_MODEL)
        assert llm._backend == "claude-cli"

    def test_cli_forced_resolves_cli_path(self, fresh_settings, monkeypatch):
        """强制 claude-cli 时仍解析 CLI 绝对路径。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "claude-cli")
        with patch.object(ClaudeLLM, "_find_claude_cli", return_value="/fake/claude"):
            llm = ClaudeLLM(model_id=_MODEL)
        assert llm._claude_cli_path == "/fake/claude"

    def test_auto_keeps_cli_priority(self, fresh_settings, monkeypatch):
        """auto（默认）：有 claude CLI 时仍优先 CLI —— 保持既有行为。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "auto")
        with patch.object(ClaudeLLM, "_find_claude_cli", return_value="/fake/claude"):
            llm = ClaudeLLM(model_id=_MODEL)
        assert llm._backend == "claude-cli"

    def test_unset_defaults_to_auto(self, fresh_settings, monkeypatch):
        """未设置环境变量时按 auto 自动探测。"""
        monkeypatch.delenv("AQUEDUCT_LLM_BACKEND", raising=False)
        with patch.object(ClaudeLLM, "_find_claude_cli", return_value="/fake/claude"):
            llm = ClaudeLLM(model_id=_MODEL)
        assert llm._backend == "claude-cli"

    def test_invalid_value_falls_back_to_auto(self, fresh_settings, monkeypatch):
        """非法值告警并回退自动探测，不抛异常。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "bogus")
        with patch.object(ClaudeLLM, "_find_claude_cli", return_value="/fake/claude"):
            llm = ClaudeLLM(model_id=_MODEL)
        assert llm._backend == "claude-cli"


# ============================================================
# PERF-1b: SDK 后端认证头 — 同值双发（Bearer + x-api-key）
# ============================================================


class TestSdkAuthHeaders:
    """SDK 客户端同时携带 Authorization: Bearer 与 x-api-key 头。

    网关（如 glm 网关）只认 Authorization: Bearer，官方 API 认 x-api-key；
    token 同值双发两条链路全兼容。
    """

    def test_sdk_client_sends_bearer_and_api_key(self, fresh_settings, monkeypatch):
        """Anthropic 客户端以 api_key + auth_token 同值构造。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        ClaudeLLM._shared_sdk_clients.clear()

        token = "tok" + "x" * 37  # 40 字符，模拟长 token
        llm = ClaudeLLM(model_id=_MODEL, api_key=token, base_url="https://gw.example.com")

        recorded: dict = {}

        class FakeAnthropic:
            def __init__(self, **kwargs):
                recorded.update(kwargs)
                raise RuntimeError("stop-after-construction")

        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = FakeAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

        with pytest.raises(RuntimeError, match="stop-after-construction"):
            llm.chat([LLMMessage(role="user", content="t")])

        assert recorded.get("auth_token") == token
        assert recorded.get("api_key") == token


# ============================================================
# PERF-7: SDK 思考预算 — 限档推理模型思考量（速度 + 空响应防护）
# ============================================================


def _install_fake_anthropic(monkeypatch, recorded: dict) -> None:
    """注入伪 anthropic 模块，记录 messages.stream 的调用参数。"""

    class FakeStream:
        text_stream = iter(["hello"])

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


class TestSdkThinkingBudget:
    """AQUEDUCT_LLM_THINKING_BUDGET_TOKENS 控制 SDK 请求的思考预算。

    glm-5.3 等始终思考的模型：默认档思考量大（实测同任务 17.7s vs 预算 1024 时 4.4s），
    且思考可能烧光 max_tokens 导致正文为空。默认 0 不发参数（兼容非思考模型）。
    """

    def test_budget_sent_when_configured(self, fresh_settings, monkeypatch):
        """配置 1024 时请求带 thinking={"type": "enabled", "budget_tokens": 1024}。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "1024")
        ClaudeLLM._shared_sdk_clients.clear()

        llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")
        recorded: dict = {}
        _install_fake_anthropic(monkeypatch, recorded)

        llm.chat([LLMMessage(role="user", content="t")])

        assert recorded.get("thinking") == {"type": "enabled", "budget_tokens": 1024}

    def test_budget_zero_omits_param(self, fresh_settings, monkeypatch):
        """默认 0 不发 thinking 参数（兼容官方 API 非思考模型）。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.delenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", raising=False)
        ClaudeLLM._shared_sdk_clients.clear()

        llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")
        recorded: dict = {}
        _install_fake_anthropic(monkeypatch, recorded)

        llm.chat([LLMMessage(role="user", content="t")])

        assert "thinking" not in recorded

    def test_budget_capped_below_max_tokens(self, fresh_settings, monkeypatch):
        """预算 ≥ max_tokens 时截断到 max_tokens // 2，保证正文有输出空间。"""
        monkeypatch.setenv("AQUEDUCT_LLM_BACKEND", "sdk")
        monkeypatch.setenv("AQUEDUCT_LLM_THINKING_BUDGET_TOKENS", "65536")
        ClaudeLLM._shared_sdk_clients.clear()

        llm = ClaudeLLM(model_id=_MODEL, api_key="x" * 40, base_url="https://gw.example.com")
        recorded: dict = {}
        _install_fake_anthropic(monkeypatch, recorded)

        llm.chat([LLMMessage(role="user", content="t")], max_tokens=32768)

        assert recorded.get("thinking") == {"type": "enabled", "budget_tokens": 16384}


# ============================================================
# PERF-2: CLI 后端超时重试 — 固定超时 + 指数退避
# ============================================================


def _make_cli_llm() -> ClaudeLLM:
    """构造 CLI 后端实例（不触发真实子进程）。"""
    llm = ClaudeLLM(model_id=_MODEL)
    llm._backend = "claude-cli"
    llm._claude_cli_path = "/fake/claude"
    return llm


class TestCliTimeoutRetry:
    """超时后以相同超时重试（不再翻倍），重试间指数退避。"""

    def test_timeout_kept_fixed_across_retries(self, fresh_settings):
        """max_retries=2 时三次尝试均用 900s，退避 1s/2s，最终抛 LLMTimeoutError。"""
        llm = _make_cli_llm()
        messages = [LLMMessage(role="user", content="test")]

        timeouts: list[int] = []

        def fake_run(cmd, **kwargs):
            timeouts.append(kwargs["timeout"])
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        sleeps: list[float] = []
        with (
            patch("src.aqueduct.llm.claude.subprocess.run", side_effect=fake_run),
            patch(
                "src.aqueduct.llm.claude.time.sleep",
                side_effect=lambda s: sleeps.append(s),
            ),
            pytest.raises(LLMTimeoutError),
        ):
            llm._chat_cli_with_retry(messages, {}, max_retries=2, timeout=900)

        assert timeouts == [900, 900, 900]  # 不翻倍（旧实现为 900/1800/3600）
        assert sleeps == [1, 2]  # 指数退避

    def test_success_after_timeout_uses_same_timeout(self, fresh_settings):
        """第一次超时、第二次成功：超时值保持不变，重试前退避 1s。"""
        llm = _make_cli_llm()
        messages = [LLMMessage(role="user", content="test")]

        timeouts: list[int] = []
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            timeouts.append(kwargs["timeout"])
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            kwargs["stdout"].write("生成结果内容")

        sleeps: list[float] = []
        with (
            patch("src.aqueduct.llm.claude.subprocess.run", side_effect=fake_run),
            patch(
                "src.aqueduct.llm.claude.time.sleep",
                side_effect=lambda s: sleeps.append(s),
            ),
        ):
            response = llm._chat_cli_with_retry(messages, {}, max_retries=2, timeout=900)

        assert response.content == "生成结果内容"
        assert timeouts == [900, 900]
        assert sleeps == [1]
