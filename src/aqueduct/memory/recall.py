"""知识召回模块 — 需求阶段自动召回。

在需求理解阶段，根据需求描述自动从本体知识库中
召回相关的业务域、实体、指标和关系信息。
"""

from __future__ import annotations

import logging

from .domain import DomainModel
from .store import MemoryStore

logger = logging.getLogger(__name__)


class KnowledgeRecall:
    """知识召回器。

    在需求理解阶段调用，返回与需求最相关的知识上下文。
    """

    def __init__(self, store: MemoryStore | None = None) -> None:
        """初始化知识召回器。

        Args:
            store: 知识存储实例。未指定时自动创建。
        """
        self._store = store or MemoryStore()

    def recall(
        self,
        requirement: str,
        max_entities: int = 10,
        max_metrics: int = 10,
    ) -> dict[str, str]:
        """根据需求描述召回相关知识。

        Args:
            requirement: 需求描述文本。
            max_entities: 最多返回的相关实体数。
            max_metrics: 最多返回的相关指标数。

        Returns:
            知识上下文字典：
            - domain_id: 匹配的业务域 ID
            - domain_context: 业务域摘要
            - entities: 相关实体列表（按相关性排序，Top-K）
            - metrics: 相关指标列表（按相关性排序，Top-K）
            - mermaid: 关系图谱（Mermaid）
        """
        result: dict[str, str] = {
            "domain_id": "",
            "domain_context": "",
            "entities": "",
            "metrics": "",
            "mermaid": "",
        }

        # 1. 召回最相关的业务域
        domain = self._store.match_domain(requirement)
        if not domain:
            logger.info("需求无匹配业务域")
            return result

        result["domain_id"] = domain.domain_id

        # 2. 生成业务域摘要
        result["domain_context"] = self._format_domain_summary(domain)

        # 3. 提取相关实体（按关键词匹配度排序，取 Top-K）
        keywords = self._store._extract_keywords(requirement.lower())
        scored_entities: list[tuple[int, str, list]] = []
        for entity_name, entity in domain.entities.items():
            score = 0
            matched_attrs = []
            # 关键词匹配加分
            for kw in keywords:
                if kw in entity_name.lower():
                    score += 3
                for attr in entity.attributes:
                    if kw in attr.name.lower() or kw in (attr.description or "").lower():
                        score += 1
                        if attr not in matched_attrs:
                            matched_attrs.append(attr)
            # 有匹配属性的实体优先
            if matched_attrs:
                score += 1
            scored_entities.append((score, entity_name, matched_attrs))

        # 按分数降序排列，取 Top-K
        scored_entities.sort(key=lambda x: x[0], reverse=True)
        entity_results = []
        for _score, entity_name, attrs in scored_entities[:max_entities]:
            attr_str = ", ".join(a.name for a in attrs[:5])
            entity_results.append(f"- {entity_name}: {attr_str}")
        result["entities"] = "\n".join(entity_results) if entity_results else ""

        # 4. 提取相关指标（按关键词匹配度排序，取 Top-K）
        scored_metrics: list[tuple[int, str, str, str]] = []
        for _mid, metric in domain.metrics.items():
            score = 0
            for kw in keywords:
                if kw in metric.name.lower():
                    score += 3
                if kw in (metric.description or "").lower():
                    score += 1
                if kw in (metric.expression or "").lower():
                    score += 1
            scored_metrics.append((score, metric.name, metric.expression, metric.unit))

        scored_metrics.sort(key=lambda x: x[0], reverse=True)
        metric_results = []
        for _score, name, expr, unit in scored_metrics[:max_metrics]:
            metric_results.append(f"- {name}: `{expr}` ({unit})")
        result["metrics"] = "\n".join(metric_results) if metric_results else ""

        # 5. 生成关系图谱
        result["mermaid"] = domain.to_mermaid()

        logger.info(
            "知识召回完成: domain=%s, entities=%d/%d, metrics=%d/%d",
            domain.domain_id,
            len(entity_results),
            len(domain.entities),
            len(metric_results),
            len(domain.metrics),
        )
        return result

    @staticmethod
    def _format_domain_summary(domain: DomainModel) -> str:
        """生成业务域摘要文本。

        Args:
            domain: 业务域模型。

        Returns:
            摘要文本，包含实体、关系、指标概览。
        """
        lines = [
            f"## 业务域: {domain.name}",
            f"ID: {domain.domain_id}",
            f"描述: {domain.description}",
            "",
            f"实体数: {len(domain.entities)}",
            f"关系数: {len(domain.relationships)}",
            f"指标数: {len(domain.metrics)}",
            "",
            "### 实体列表",
        ]

        for entity_name, entity in domain.entities.items():
            pk = entity.primary_key or "无"
            src = entity.source or "未指定"
            lines.append(f"- **{entity_name}**: 主键={pk}, 来源={src}")
            if entity.description:
                lines.append(f"  - {entity.description}")
            if entity.attributes:
                attr_names = ", ".join(a.name for a in entity.attributes[:5])
                lines.append(f"  - 属性: {attr_names}")

        if domain.metrics:
            lines.append("")
            lines.append("### 指标列表")
            for _mid, metric in domain.metrics.items():
                lines.append(f"- **{metric.name}**: `{metric.expression}` ({metric.unit})")

        return "\n".join(lines)
