"""Prompt 模板注册中心 — 结构化 Prompt 模板管理。

替代原有的 Markdown 提示词文件硬编码方式。
每个 Prompt 模板是结构化的，包含 system prompt 和可选的 user prompt，
支持 {变量} 占位符动态渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import LLMMessage


@dataclass
class PromptTemplate:
    """结构化 Prompt 模板。

    用法:
        template = PromptTemplate(
            id="sql_develop",
            system="你是一名资深数据仓库开发工程师...",
            user="请根据以下需求编写 SQL：{requirement_doc}",
            variables=["requirement_doc", "ddl_content"],
        )
        messages = template.render(requirement_doc="需求内容...", ddl_content="DDL内容...")
    """

    id: str  # 模板唯一标识
    system: str  # 系统提示词
    user: str = ""  # 用户提示词（可选）
    variables: list[str] = field(default_factory=list)  # 变量名列表
    description: str = ""  # 模板描述

    def render(self, **kwargs: Any) -> list[LLMMessage]:
        """渲染模板为 LLM 消息列表。

        Args:
            **kwargs: 模板变量值。

        Returns:
            LLMMessage 列表（system + 可选 user）。

        Raises:
            KeyError: 缺少必需变量时抛出。
        """
        try:
            system_text = self.system.format(**kwargs)
        except KeyError as e:
            raise KeyError(
                f"Prompt 模板 '{self.id}' 缺少变量 {e}。必需变量: {self.variables}"
            ) from e

        messages = [LLMMessage(role="system", content=system_text)]

        if self.user:
            try:
                user_text = self.user.format(**kwargs)
                messages.append(LLMMessage(role="user", content=user_text))
            except KeyError as e:
                raise KeyError(f"Prompt 模板 '{self.id}' user 部分缺少变量 {e}。") from e

        return messages

    def estimate_tokens(self, estimator) -> int:
        """估算模板的 Token 数量（不含变量填充）。

        Args:
            estimator: Token 估算函数 (text: str) -> int。

        Returns:
            估算的 Token 数量。
        """
        tokens = estimator(self.system)
        if self.user:
            tokens += estimator(self.user)
        return tokens


# ============================================================
# 全局 Prompt 注册表
# ============================================================

_PROMPTS: dict[str, PromptTemplate] = {}


def register_prompt(template: PromptTemplate) -> PromptTemplate:
    """装饰器/函数：注册 Prompt 模板到全局注册表。

    用法:
        @register_prompt
        SQL_DEVELOP = PromptTemplate(
            id="sql_develop",
            system="...",
        )
    """
    if template.id in _PROMPTS:
        raise ValueError(f"Prompt 模板 '{template.id}' 已注册。")
    _PROMPTS[template.id] = template
    return template


def get_prompt(prompt_id: str) -> PromptTemplate:
    """按 ID 获取 Prompt 模板。"""
    if prompt_id not in _PROMPTS:
        raise KeyError(f"Prompt 模板 '{prompt_id}' 未注册。可用: {list(_PROMPTS.keys())}")
    return _PROMPTS[prompt_id]


def list_prompts() -> list[str]:
    """返回所有已注册的 Prompt 模板 ID。"""
    return list(_PROMPTS.keys())
