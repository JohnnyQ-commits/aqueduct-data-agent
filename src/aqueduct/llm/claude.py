"""Claude API 适配器。

支持两种后端:
1. Anthropic SDK 模式（已安装 anthropic 包 + 有效 API Key 时）
2. Claude Code CLI 模式（运行在 Claude Code 环境内时，通过 claude CLI 代理）

通过环境变量 ANTHROPIC_BASE_URL、ANTHROPIC_AUTH_TOKEN 配置连接。
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ..exceptions import LLMTimeoutError
from .base import BaseLLM, LLMMessage, LLMResponse, LLMUsage

logger = logging.getLogger(__name__)


class ClaudeLLM(BaseLLM):
    """Claude 模型适配器。

    支持三档模型：
    - Haiku 档：轻量分析（需求解析、统计、语义召回）
    - Sonnet 档：中等生成（方案编写、DDL 生成、文档输出）
    - Opus 档：重度生成（SQL 生成、SQL 质检、CodeReview）
    """

    # 各档模型的上下文窗口大小（Token 数）
    CONTEXT_WINDOWS = {
        "haiku": 200_000,
        "sonnet": 200_000,
        "opus": 200_000,
    }

    # 类级别 SDK 客户端缓存：(api_key, base_url, timeout) → Anthropic 实例
    # 所有 ClaudeLLM 实例共享同一 HTTP 连接池，避免重复建连
    _shared_sdk_clients: dict[tuple[str, str, float], Any] = {}

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        """初始化 Claude LLM 实例。

        Args:
            model_id: 模型标识。未指定时自动从环境变量读取。
            api_key: API 密钥。未指定时读取 ANTHROPIC_AUTH_TOKEN。
            base_url: API 基础 URL。未指定时读取 ANTHROPIC_BASE_URL。
            **kwargs: 传递给 Anthropic API 的额外参数。
        """
        # 在实例初始化时读取环境变量，确保 .env 已加载后能正确获取值
        default_sonnet = os.environ.get(
            "ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6-20250514"
        )

        self._model_id = model_id or default_sonnet
        self._api_key = api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self._base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self._default_kwargs = kwargs

        # 确定模型档位
        tier = self._model_id.lower()
        if "haiku" in tier:
            self._tier = "haiku"
        elif "opus" in tier:
            self._tier = "opus"
        else:
            self._tier = "sonnet"

        # CLI 路径（_detect_backend 可能设置）
        self._claude_cli_path: str | None = None

        # 检测后端能力
        self._backend = self._detect_backend()

    def __repr__(self) -> str:
        """脱敏表示，防止 API Key 泄露到日志/异常。"""
        return (
            f"ClaudeLLM(model={self._model_id!r}, tier={self._tier!r}, "
            f"backend={self._backend!r}, api_key=***)"
        )

    def _find_claude_cli(self) -> str | None:
        """查找 claude CLI 的绝对路径。"""
        import shutil

        # 尝试通过 shutil.which 查找（支持 .cmd/.bat 扩展）
        path = shutil.which("claude")
        if path:
            return path
        path = shutil.which("claude.cmd")
        if path:
            return path
        # Windows 常见位置
        candidates = [
            os.path.expandvars(r"%APPDATA%\npm\claude.cmd"),
            os.path.expandvars(r"%APPDATA%\npm\claude"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    def _detect_backend(self) -> str:
        """检测可用的 LLM 后端。

        AQUEDUCT_LLM_BACKEND 可强制指定（sdk / cli / claude-cli / auto），
        覆盖自动探测 —— 例如装了 claude CLI 但想走 SDK 流式直连时设 sdk。

        Returns:
            "sdk"（Anthropic SDK）或 "claude-cli"（Claude Code CLI）
        """
        forced = self._forced_backend()
        if forced:
            if forced == "claude-cli":
                # 仍解析 CLI 绝对路径（找不到时 _chat_cli 回退裸 "claude" 命令）
                self._claude_cli_path = self._find_claude_cli()
                return "claude-cli"
            return "sdk"

        # 自动探测：优先 Claude Code CLI
        cli_path = self._find_claude_cli()
        if cli_path:
            self._claude_cli_path = cli_path
            return "claude-cli"

        # 回退到 SDK（需要有效的 API Key）
        if self._api_key and len(self._api_key) > 20:
            # 长 token 可能是真正的 API key（工号只有 8 位）
            try:
                from anthropic import Anthropic  # noqa: F401

                return "sdk"
            except ImportError:
                pass

        # 两个都不可用，默认使用 CLI
        return "claude-cli"

    @staticmethod
    def _forced_backend() -> str | None:
        """读取 AQUEDUCT_LLM_BACKEND 强制后端。

        Returns:
            "sdk" / "claude-cli"，或 None（未设置、auto、非法值）。
            非法值记录警告并回退自动探测。
        """
        from ..config.settings import get_settings

        value = get_settings().llm_backend.strip().lower()
        if value in ("", "auto"):
            return None
        if value == "sdk":
            return "sdk"
        if value in ("cli", "claude-cli"):
            return "claude-cli"
        logger.warning(
            "AQUEDUCT_LLM_BACKEND=%r 无效（可选 auto/sdk/cli/claude-cli），回退自动探测",
            value,
        )
        return None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def max_context(self) -> int:
        from ..config.settings import get_settings

        return get_settings().llm_max_context_tokens

    def chat(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        """发送对话请求并返回结构化响应。

        Args:
            messages: 对话消息列表。
            **kwargs: 额外的 API 参数（temperature、max_tokens 等）。

        Returns:
            LLMResponse，包含内容、用量和元数据。

        Raises:
            LLMError: API 失败或请求无效时抛出。
        """
        merged_kwargs = {**self._default_kwargs, **kwargs}

        if self._backend == "sdk":
            return self._chat_sdk(messages, merged_kwargs)
        else:
            return self._chat_cli(messages, merged_kwargs)

    def _chat_sdk(
        self,
        messages: list[LLMMessage],
        kwargs: dict[str, Any],
    ) -> LLMResponse:
        """通过 Anthropic SDK 调用。

        支持超时重试（指数退避，最多 max_retries 次）。
        """
        from anthropic import Anthropic

        from ..config.settings import get_settings

        timeout_seconds = float(get_settings().llm_timeout_seconds)

        # 类级别客户端缓存：所有 ClaudeLLM 实例共享同一 HTTP 连接池
        cache_key = (
            self._api_key or "",
            self._base_url or "",
            timeout_seconds,
        )
        if cache_key not in ClaudeLLM._shared_sdk_clients:
            # token 同值双发：api_key → x-api-key 头（官方 API），
            # auth_token → Authorization: Bearer 头（自建网关只认此头）
            ClaudeLLM._shared_sdk_clients[cache_key] = Anthropic(
                api_key=self._api_key if self._api_key else None,
                auth_token=self._api_key if self._api_key else None,
                base_url=self._base_url if self._base_url else None,
                timeout=timeout_seconds,
            )
        client = ClaudeLLM._shared_sdk_clients[cache_key]

        # 分离 system 消息（如果有）
        system_msg = None
        user_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                user_messages.append({"role": m.role, "content": m.content})

        max_tokens = kwargs.pop("max_tokens", 32768)

        # PERF-7: 思考预算 —— 限档始终思考模型（如 glm-5.3）的思考量。
        # 思考计入 max_tokens：无预算时思考可能烧光额度导致正文为空（实测空响应根因）。
        thinking_budget = get_settings().llm_thinking_budget_tokens
        if thinking_budget > 0:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": min(thinking_budget, max_tokens // 2),
            }

        # 超时重试（指数退避）
        max_retries = 2
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return self._do_sdk_stream(client, user_messages, system_msg, max_tokens, kwargs)
            except Exception as e:
                last_error = e
                error_type = type(e).__name__
                if attempt < max_retries:
                    delay = 2**attempt
                    logger.warning(
                        "SDK 调用失败 (attempt=%d/%d, error=%s: %s), %ds 后重试",
                        attempt + 1,
                        max_retries + 1,
                        error_type,
                        str(e)[:200],
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "SDK 调用最终失败 (%d 次重试后): %s: %s",
                        max_retries + 1,
                        error_type,
                        str(e)[:200],
                    )

        raise (
            LLMTimeoutError(f"SDK 调用失败（{max_retries + 1} 次尝试后）: {last_error}")
            if last_error
            else LLMTimeoutError("SDK 调用失败（未知错误）")
        )

    def _do_sdk_stream(
        self,
        client: Any,
        user_messages: list[dict[str, str]],
        system_msg: str | None,
        max_tokens: int,
        kwargs: dict[str, Any],
    ) -> LLMResponse:
        """执行 SDK 流式调用并解析结果。

        PERF-10: 逐原始事件消费流（不再用 text_stream 聚合——它隐藏了
        thinking 事件，中途只见正文不见 token 消耗）。监听 message_delta
        的累计 output_tokens：无正文且超过 llm_spiral_abort_tokens 时
        判定思考螺旋，提前中止返回空内容，交由上层空响应重试
        （helpers.call_llm 已有 2 次重试 + 退避；螺旋非确定，重试即重新掷骰子）。
        """
        from ..config.settings import get_settings

        abort_tokens = get_settings().llm_spiral_abort_tokens

        stream = client.messages.stream(
            model=self._model_id,
            messages=user_messages,
            system=system_msg,
            max_tokens=max_tokens,
            **kwargs,
        )

        content = ""
        prompt_tokens = 0
        output_tokens = 0
        final_response = None
        spiral_aborted = False

        with stream as s:
            for event in s:
                etype = getattr(event, "type", "")

                if etype == "message_start":
                    # 初始 usage：input_tokens 在这里（message_delta 只带 output）
                    msg_obj = getattr(event, "message", None)
                    usage = getattr(msg_obj, "usage", None) if msg_obj else None
                    if usage is not None:
                        prompt_tokens = getattr(usage, "input_tokens", 0) or 0
                elif etype == "message_delta":
                    # 流式 usage 增量：output_tokens 为累计值（含 thinking）
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        tracked = getattr(usage, "output_tokens", None)
                        if isinstance(tracked, int) and tracked > output_tokens:
                            output_tokens = tracked
                elif etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", "") == "text_delta":
                        content += getattr(delta, "text", "") or ""

                # 思考螺旋检测：零正文 + 累计输出超阈值 → 注定烧光 max_tokens
                if abort_tokens > 0 and not content and output_tokens >= abort_tokens:
                    spiral_aborted = True
                    break

            if not spiral_aborted:
                final_response = s.get_final_message()

        if spiral_aborted:
            logger.warning(
                "[model=%s] 思考螺旋提前中止: 累计 output_tokens=%d 超阈值 %d 仍无正文，"
                "返回空内容交由上层重试（单次止损 %d/%d tokens）",
                self._model_id,
                output_tokens,
                abort_tokens,
                output_tokens,
                max_tokens,
            )
            return LLMResponse(
                content="",
                usage=LLMUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=prompt_tokens + output_tokens,
                ),
                model=self._model_id,
                finish_reason="spiral_abort",
            )

        usage_obj = getattr(final_response, "usage", None)
        prompt_tokens = getattr(usage_obj, "input_tokens", 0) if usage_obj else prompt_tokens
        completion_tokens = getattr(usage_obj, "output_tokens", 0) if usage_obj else output_tokens
        cache_read = getattr(usage_obj, "cache_read_input_tokens", 0) if usage_obj else 0
        cache_create = getattr(usage_obj, "cache_creation_input_tokens", 0) if usage_obj else 0

        stop_reason = getattr(final_response, "stop_reason", "")
        if hasattr(stop_reason, "value"):
            stop_reason = stop_reason.value

        return LLMResponse(
            content=content,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cache_read_tokens=cache_read,
                cache_create_tokens=cache_create,
            ),
            model=self._model_id,
            finish_reason=str(stop_reason) if stop_reason else "",
        )

    def _chat_cli(
        self,
        messages: list[LLMMessage],
        kwargs: dict[str, Any],
    ) -> LLMResponse:
        """通过 Claude Code CLI 代理调用。

        在 Claude Code 环境内，SDK 的 API Key（工号）无法直接用于 SDK 调用。
        此方法通过 `claude -p` 直接获取响应。

        使用列表形式的 subprocess 调用 + 文件重定向，避免 shell 注入风险。
        超时自动重试（指数退避，最多 2 次）。
        """
        from ..config.settings import get_settings

        settings = get_settings()
        max_retries = settings.llm_max_retries
        timeout_seconds = settings.llm_timeout_seconds

        return self._chat_cli_with_retry(
            messages, kwargs, max_retries=max_retries, timeout=timeout_seconds
        )

    def _chat_cli_with_retry(
        self,
        messages: list[LLMMessage],
        kwargs: dict[str, Any],
        max_retries: int = 2,
        timeout: int = 900,
    ) -> LLMResponse:
        """带重试的 CLI 调用实现。"""
        from ..config.settings import get_settings

        # 拼接所有消息为单个 prompt（system 消息作为前缀）
        parts = []
        for msg in messages:
            if msg.role == "system":
                parts.append(f"<system>{msg.content}</system>")
            else:
                parts.append(msg.content)
        raw_prompt = "\n\n".join(parts)

        # 添加任务执行指令，防止 sub-Claude 以对话方式回应
        full_prompt = (
            "请直接执行以下任务，仅输出结果，不要提问或解释。"
            "不要以对话者的身份回复，直接完成任务即可。\n\n" + raw_prompt
        )

        # 安全调用方式：
        # 1. 使用列表形式的 subprocess.run，不经过 shell，杜绝命令注入
        # 2. prompt 通过 stdin 文件重定向传递（避免 @file 被当作"分析文件"）
        # 3. stdout/stderr 通过文件句柄重定向（避免 pipe 挂起问题）
        # 4. --bare 跳过 hooks/权限检查，避免等待用户确认

        # 将 prompt 和输出放到系统临时目录（避免项目根目录残留临时文件）
        tmp_dir = Path(tempfile.mkdtemp(prefix="aqueduct_claude_"))

        # timeout 由 _chat_cli 从 settings.llm_timeout_seconds 传入，重试保持相同超时
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            with (
                tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix="claude_out_",
                    suffix=".txt",
                    delete=False,
                    dir=str(tmp_dir),
                ) as stdout_tmp,
                tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix="claude_err_",
                    suffix=".txt",
                    delete=False,
                    dir=str(tmp_dir),
                ) as stderr_tmp,
            ):
                stdout_path = stdout_tmp.name
                stderr_path = stderr_tmp.name

            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix="prompt_",
                suffix=".txt",
                delete=False,
                encoding="utf-8",
                dir=str(tmp_dir),
            ) as prompt_tmp:
                prompt_tmp.write(full_prompt)
                prompt_path = prompt_tmp.name

            try:
                # 使用列表形式的 subprocess 调用，避免 shell 注入风险
                # stdin 从 prompt 文件读取，stdout/stderr 重定向到临时文件
                claude_cmd = self._claude_cli_path or "claude"

                with (
                    open(prompt_path, encoding="utf-8") as stdin_file,
                    open(stdout_path, "w", encoding="utf-8") as stdout_file,
                    open(stderr_path, "w", encoding="utf-8") as stderr_file,
                ):
                    subprocess.run(
                        [claude_cmd, "-p", "--bare"],
                        stdin=stdin_file,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        timeout=timeout,
                        cwd=str(get_settings().project_root),
                    )

                # 从临时文件读取输出（UTF-8 编码）
                content = Path(stdout_path).read_text(encoding="utf-8").strip()
                if not content and Path(stderr_path).stat().st_size > 0:
                    content = (
                        Path(stderr_path).read_text(encoding="utf-8", errors="replace").strip()
                    )

                # 调用成功，跳出重试循环
                return LLMResponse(
                    content=content or "[LLM 调用返回为空]",
                    usage=LLMUsage(
                        prompt_tokens=self.estimate_tokens(full_prompt),
                        completion_tokens=self.estimate_tokens(content),
                        total_tokens=self.estimate_tokens(full_prompt)
                        + self.estimate_tokens(content),
                        estimated=True,  # CLI 后端无法获取真实 API token 用量，此为字符估算值
                    ),
                    model=self._model_id,
                )

            except subprocess.TimeoutExpired:
                last_error = LLMTimeoutError(
                    f"LLM 调用超时（{timeout}s），模型={self._model_id}",
                )
                logger.error(
                    "[model=%s] LLM CLI 调用超时: timeout=%ds, attempt=%d/%d, prompt_size=%d 字符",
                    self._model_id,
                    timeout,
                    attempt + 1,
                    max_retries + 1,
                    len(full_prompt),
                )
                if attempt < max_retries:
                    # 保持相同超时重试（翻倍会让最坏情况指数膨胀），指数退避
                    delay = 2**attempt
                    logger.warning(
                        "[model=%s] LLM 超时，%ds 后以相同超时（%ds）重试",
                        self._model_id,
                        delay,
                        timeout,
                    )
                    time.sleep(delay)
                    continue

            except Exception as e:
                last_error = e
                logger.error(
                    "[model=%s] LLM CLI 调用异常: error=%s, attempt=%d/%d",
                    self._model_id,
                    str(e),
                    attempt + 1,
                    max_retries + 1,
                )
                if attempt < max_retries:
                    delay = 2**attempt  # 指数退避：1s, 2s
                    logger.warning(
                        "[model=%s] LLM 异常，%0.1f 秒后重试",
                        self._model_id,
                        delay,
                    )
                    time.sleep(delay)
                    continue

            finally:
                # 清理临时文件
                for path in (prompt_path, stdout_path, stderr_path):
                    try:
                        if os.path.exists(path):
                            os.unlink(path)
                    except OSError:
                        pass

        # 清理临时目录
        try:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        # 所有重试均失败
        raise last_error  # type: ignore[misc]

    def estimate_tokens(self, text: str) -> int:
        """估算文本的 Token 数量。

        简化估算：按 1 Token ≈ 1.5 个中文字符或 4 个英文字符计算。
        实际接入后可使用 Anthropic 官方的 token counter。

        Args:
            text: 要估算的文本。

        Returns:
            估算的 Token 数量。
        """
        if not text:
            return 0

        # 粗略估算：中文字符约 1.5 Token/字，英文约 0.25 Token/字符
        chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
        other_chars = len(text) - chinese_chars

        return int(chinese_chars * 1.5 + other_chars * 0.25)
