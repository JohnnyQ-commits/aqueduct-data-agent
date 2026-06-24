"""上下文管理器 — 上下文窗口管理、压缩、拼接策略。

负责在 LLM 调用前管理消息上下文，确保不超过模型的最大上下文窗口。
支持自动截断、消息压缩和优先级排序。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .base import BaseLLM, LLMMessage


@dataclass
class ContextBudget:
    """上下文预算配置。"""

    max_tokens: int = 200_000  # 最大上下文窗口
    system_reserve: int = 10_000  # 系统提示词预留空间
    output_reserve: int = 8_000  # 输出内容预留空间
    available: int = 0  # 可用空间（自动计算）

    def __post_init__(self) -> None:
        self.available = self.max_tokens - self.system_reserve - self.output_reserve


class ContextManager:
    """LLM 上下文管理器。

    职责:
    1. 跟踪当前上下文的 Token 使用量
    2. 自动截断超出窗口的消息
    3. 支持消息压缩（保留关键信息，丢弃冗余）
    4. 支持消息优先级排序（系统提示词 > 最新对话 > 历史对话）
    """

    def __init__(
        self,
        model: BaseLLM,
        budget: ContextBudget | None = None,
    ) -> None:
        """初始化上下文管理器。

        Args:
            model: LLM 实例，用于 token 估算。
            budget: 上下文预算。未指定时使用默认值。
        """
        self._model = model
        self._budget = budget or ContextBudget(max_tokens=model.max_context)
        self._messages: list[LLMMessage] = []

    @property
    def messages(self) -> list[LLMMessage]:
        """当前上下文消息列表。"""
        return list(self._messages)

    @property
    def token_count(self) -> int:
        """当前上下文的 Token 总数。"""
        return sum(self._model.estimate_tokens(m.content) for m in self._messages)

    @property
    def remaining_tokens(self) -> int:
        """剩余可用 Token 数。"""
        return self._budget.available - self.token_count

    def add(self, message: LLMMessage) -> bool:
        """添加一条消息到上下文。

        如果添加后超出上下文窗口，自动触发截断。

        Args:
            message: 要添加的消息。

        Returns:
            True 表示添加成功，False 表示消息因空间不足被丢弃。
        """
        self._messages.append(message)
        # 如果超出预算，自动截断
        if self.token_count > self._budget.available:
            old_count = len(self._messages)
            self._truncate()
            # 如果新消息在截断后被移除（消息列表未变长），说明消息被丢弃
            if len(self._messages) == old_count - 1:
                return False
        return True

    def add_many(self, messages: list[LLMMessage]) -> None:
        """批量添加消息。"""
        for msg in messages:
            self.add(msg)

    def clear(self) -> None:
        """清空上下文，保留系统提示词。"""
        self._messages = [m for m in self._messages if m.role == "system"]

    def reset(self) -> None:
        """完全重置上下文。"""
        self._messages = []

    def _truncate(self) -> None:
        """截断消息使其适配上下文窗口。

        策略:
        1. 保留所有 system 消息
        2. 从最旧的非 system 消息开始移除
        3. 直到总 token 数不超过预算
        """
        # 分离系统消息和普通消息
        system_msgs = [m for m in self._messages if m.role == "system"]
        other_msgs = [m for m in self._messages if m.role != "system"]

        # 从最旧的开始移除
        while other_msgs:
            other_msgs.pop(0)
            total = sum(self._model.estimate_tokens(m.content) for m in system_msgs + other_msgs)
            if total <= self._budget.available:
                break

        self._messages = system_msgs + other_msgs

    def compress(
        self,
        compressor: Callable[[list[LLMMessage]], LLMMessage] | None = None,
    ) -> None:
        """压缩上下文。

        默认策略：将历史对话压缩为一条摘要消息。
        自定义策略：传入 compressor 函数。

        Args:
            compressor: 压缩函数，接收消息列表，返回一条摘要消息。
                        默认使用 "历史对话已压缩，共 N 条消息" 的简单摘要。
        """
        if compressor is None:
            compressor = self._default_compressor

        system_msgs = [m for m in self._messages if m.role == "system"]
        other_msgs = [m for m in self._messages if m.role != "system"]

        if len(other_msgs) <= 2:
            return  # 消息太少，不需要压缩

        # 保留最新的 2 条消息
        recent = other_msgs[-2:]
        history = other_msgs[:-2]

        summary = compressor(history)
        self._messages = system_msgs + [summary] + recent

    @staticmethod
    def _default_compressor(messages: list[LLMMessage]) -> LLMMessage:
        """默认压缩函数：生成简单摘要。"""
        user_msgs = [m for m in messages if m.role == "user"]
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        return LLMMessage(
            role="system",
            content=(
                f"[历史对话已压缩] "
                f"共 {len(messages)} 条消息 "
                f"（用户 {len(user_msgs)} 条，助手 {len(assistant_msgs)} 条）。"
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<ContextManager tokens={self.token_count}/"
            f"{self._budget.available} messages={len(self._messages)}>"
        )
