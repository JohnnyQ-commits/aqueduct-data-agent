"""模型路由器 — 按任务类型自动路由到合适的模型档位。

基于 Claude API 或兼容代理部署，模型通过环境变量动态读取：
- ANTHROPIC_DEFAULT_HAIKU_MODEL: 轻量分析（默认 claude-haiku-4-5）
- ANTHROPIC_DEFAULT_SONNET_MODEL: 中等生成（默认 claude-sonnet-4-6）
- ANTHROPIC_DEFAULT_OPUS_MODEL: 重度生成（默认 claude-opus-4-7）

路由策略:
- 分析类任务 → Haiku 档（低延迟、低成本）
- 中等生成 → Sonnet 档（平衡质量与成本）
- 重度生成 → Opus 档（最强推理、保证代码质量）
"""

from __future__ import annotations

import logging

from .base import BaseLLM
from .claude import ClaudeLLM

logger = logging.getLogger(__name__)

# 任务类型分类
ANALYSIS_TASKS = frozenset(
    [
        "requirement_parse",  # 需求解析
        "summarize",  # 摘要生成
        "board_stats",  # 提效看板统计
        "semantic_recall",  # 语义召回
    ]
)

MEDIUM_TASKS = frozenset(
    [
        "scheme_write",  # 方案编写
        "ddl_gen",  # DDL 生成
        "doc_gen",  # 文档输出
    ]
)

HEAVY_TASKS = frozenset(
    [
        "sql_gen",  # SQL 生成
        "sql_review",  # SQL 质检
        "code_review",  # 代码审查
    ]
)


class ModelRouter:
    """按任务类型自动路由到合适的模型。

    用法:
        router = ModelRouter()
        llm = router.route("sql_gen")  # 返回 Opus 档模型
    """

    def __init__(
        self,
        haiku_model: BaseLLM | None = None,
        sonnet_model: BaseLLM | None = None,
        opus_model: BaseLLM | None = None,
    ) -> None:
        """初始化路由器。

        Args:
            haiku_model: Haiku 档模型实例。未指定时自动创建。
            sonnet_model: Sonnet 档模型实例。未指定时自动创建。
            opus_model: Opus 档模型实例。未指定时自动创建。
        """
        self._haiku = haiku_model or ClaudeLLM()
        self._sonnet = sonnet_model or ClaudeLLM()
        self._opus = opus_model or ClaudeLLM()

    def route(self, task_type: str) -> BaseLLM:
        """根据任务类型路由到合适的模型。

        Args:
            task_type: 任务类型标识。

        Returns:
            对应的 LLM 实例。

        Raises:
            ValueError: 未知任务类型时抛出。
        """
        if task_type in ANALYSIS_TASKS:
            llm = self._haiku
            tier = "haiku"
        elif task_type in MEDIUM_TASKS:
            llm = self._sonnet
            tier = "sonnet"
        elif task_type in HEAVY_TASKS:
            llm = self._opus
            tier = "opus"
        else:
            raise ValueError(
                f"未知任务类型: {task_type}。"
                f"可用: {sorted(ANALYSIS_TASKS | MEDIUM_TASKS | HEAVY_TASKS)}"
            )

        logger.info("路由决策: task_type=%s → tier=%s, model=%s", task_type, tier, llm.model_id)
        return llm

    @property
    def haiku(self) -> BaseLLM:
        """Haiku 档模型（轻量分析）。"""
        return self._haiku

    @property
    def sonnet(self) -> BaseLLM:
        """Sonnet 档模型（中等生成）。"""
        return self._sonnet

    @property
    def opus(self) -> BaseLLM:
        """Opus 档模型（重度生成）。"""
        return self._opus

    def list_task_types(self) -> dict[str, list[str]]:
        """返回所有任务类型分类。"""
        return {
            "analysis": sorted(ANALYSIS_TASKS),
            "medium": sorted(MEDIUM_TASKS),
            "heavy": sorted(HEAVY_TASKS),
        }
