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

        Returns:
            "sdk"（Anthropic SDK）或 "claude-cli"（Claude Code CLI）
        """
        # 优先检查 Claude Code CLI
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

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def max_context(self) -> int:
        return self.CONTEXT_WINDOWS.get(self._tier, 200_000)

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
        """通过 Anthropic SDK 调用。"""
        from anthropic import Anthropic

        client = Anthropic(
            api_key=self._api_key if self._api_key else None,
            base_url=self._base_url if self._base_url else None,
        )

        # 分离 system 消息（如果有）
        system_msg = None
        user_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                user_messages.append({"role": m.role, "content": m.content})

        max_tokens = kwargs.pop("max_tokens", 32768)

        stream = client.messages.stream(
            model=self._model_id,
            messages=user_messages,
            system=system_msg,
            max_tokens=max_tokens,
            **kwargs,
        )

        content = ""
        final_response = None
        with stream as s:
            for text in s.text_stream:
                content += text
            final_response = s.get_final_message()

        usage_obj = getattr(final_response, "usage", None)
        prompt_tokens = getattr(usage_obj, "input_tokens", 0) if usage_obj else 0
        completion_tokens = getattr(usage_obj, "output_tokens", 0) if usage_obj else 0
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

        return self._chat_cli_with_retry(messages, kwargs, max_retries=max_retries)

    def _chat_cli_with_retry(
        self,
        messages: list[LLMMessage],
        kwargs: dict[str, Any],
        max_retries: int = 2,
    ) -> LLMResponse:
        """带重试的 CLI 调用实现。"""
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

        # 将 prompt 和输出放到项目临时目录（路径短且安全）
        tmp_dir = Path(__file__).resolve().parent.parent.parent.parent / ".claude_tmp"
        tmp_dir.mkdir(exist_ok=True)

        timeout = 600  # 单次超时时间（秒）
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
                        cwd=Path(__file__).resolve().parent.parent.parent.parent,
                    )

                # 从临时文件读取输出（UTF-8 编码）
                content = Path(stdout_path).read_text(encoding="utf-8").strip()
                if not content and Path(stderr_path).stat().st_size > 0:
                    content = Path(stderr_path).read_text(encoding="utf-8", errors="replace").strip()

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
                    new_timeout = timeout * 2
                    logger.warning(
                        "[model=%s] LLM 超时，第 %d 次重试，timeout=%ds",
                        self._model_id,
                        attempt + 1,
                        new_timeout,
                    )
                    timeout = new_timeout
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

    def __repr__(self) -> str:
        return f"<ClaudeLLM model={self._model_id} tier={self._tier} backend={self._backend} context={self.max_context}>"
