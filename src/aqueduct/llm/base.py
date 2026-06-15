"""LLM 抽象基座层。

所有 LLM 后端（Claude、Qwen、Kimi 等）必须实现 BaseLLM。
这样核心逻辑与具体模型提供商解耦，可自由切换模型。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    """对话中的单条消息。"""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMUsage:
    """LLM 响应的 Token 用量统计。"""

    prompt_tokens: int = 0  # 输入 token 数
    completion_tokens: int = 0  # 输出 token 数
    total_tokens: int = 0  # 总计 token 数
    cache_read_tokens: int = 0  # 缓存读取 token 数
    cache_create_tokens: int = 0  # 缓存创建 token 数
    estimated: bool = False  # 是否为估算值（CLI 后端无法获取真实 token 数）


@dataclass
class LLMResponse:
    """LLM 调用的结构化响应。"""

    content: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    model: str = ""
    finish_reason: str = ""


class BaseLLM(ABC):
    """所有 LLM 后端的抽象基类。

    子类必须实现 `chat()` 和 `estimate_tokens()`。
    `max_context` 属性定义模型的上下文窗口大小。
    """

    @property
    @abstractmethod
    def model_id(self) -> str:
        """模型唯一标识（如 'claude-sonnet-4-6'）。"""

    @property
    @abstractmethod
    def max_context(self) -> int:
        """最大上下文窗口（Token 数）。"""

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        **kwargs: Any,
    ) -> LLMResponse:
        """发送对话请求并返回结构化响应。

        Args:
            messages: 对话消息列表。
            **kwargs: 厂商特有参数（temperature、max_tokens、tools 等）。

        Returns:
            LLMResponse，包含内容、用量和元数据。

        Raises:
            LLMError: API 失败或请求无效时抛出。
            LLMContextExceededError: 输入超过上下文窗口时抛出。
        """

    @abstractmethod
    def estimate_tokens(self, text: str) -> int:
        """估算文本的 Token 数量。

        用于上下文预算跟踪和模型路由决策。
        """

    def truncate_to_context(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        """截断消息使其适配模型的上下文窗口。

        默认实现：从开头逐条移除消息，直到总 token 数
        不超过 max_context。子类可覆盖实现更精细的截断策略。
        """
        total = sum(self.estimate_tokens(m.content) for m in messages)
        while messages and total > self.max_context:
            removed = messages.pop(0)
            total -= self.estimate_tokens(removed.content)
        return messages

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} model={self.model_id} context={self.max_context}>"
