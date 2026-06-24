"""知识存储与查询 API — MemoryStore。

加载、缓存、搜索业务域本体模型（knowledge/domains/*.json）。
支持需求阶段自动召回：根据需求描述匹配最相关的业务域。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .domain import Attribute, DomainModel, Metric

logger = logging.getLogger(__name__)


class MemoryStore:
    """知识存储与查询 API。

    职责:
    1. 加载业务域本体模型（带 LRU 缓存）
    2. 列出所有可用业务域
    3. 需求阶段自动召回（基于关键词匹配）
    4. 实体/指标搜索
    5. 关系图谱生成（Mermaid）
    """

    def __init__(self, domains_dir: Path | str | None = None) -> None:
        """初始化知识存储。

        Args:
            domains_dir: 业务域 JSON 目录。
                         默认使用 knowledge/domains。
        """
        if domains_dir is None:
            domains_dir = Path("knowledge/domains")
        self._domains_dir = Path(domains_dir)
        self._cache: dict[str, DomainModel] = {}

    def load(self, domain_id: str) -> DomainModel:
        """加载指定业务域。

        Args:
            domain_id: 业务域 ID（如 'ecommerce_order'）。

        Returns:
            DomainModel 实例。

        Raises:
            FileNotFoundError: 业务域文件不存在。
        """
        if domain_id not in self._cache:
            path = self._domains_dir / f"{domain_id}.json"
            if not path.exists():
                raise FileNotFoundError(f"业务域 '{domain_id}' 不存在: {path}")

            self._cache[domain_id] = DomainModel.from_json(path)
            logger.debug(
                "加载业务域 '%s': %d 实体, %d 关系",
                domain_id,
                len(self._cache[domain_id].entities),
                len(self._cache[domain_id].relationships),
            )

        return self._cache[domain_id]

    def list_domains(self) -> list[str]:
        """列出所有可用业务域 ID。

        Returns:
            业务域 ID 列表。
        """
        if not self._domains_dir.exists():
            logger.warning("业务域目录不存在: %s", self._domains_dir)
            return []

        return sorted([p.stem for p in self._domains_dir.glob("*.json")])

    def match_domain(self, requirement: str) -> DomainModel | None:
        """需求阶段自动召回：根据需求描述匹配最相关的业务域。

        策略：基于关键词匹配分数排序。
        匹配维度：业务域名、实体名、属性名、指标名、关系描述。

        Args:
            requirement: 需求描述文本。

        Returns:
            最匹配的业务域模型。无匹配时返回 None。
        """
        if not requirement:
            return None

        kw = requirement.lower()

        # 提取关键词（简单分词：中文按字，英文按词）
        keywords = self._extract_keywords(kw)
        if not keywords:
            return None

        best_score = 0.0
        best_domain: DomainModel | None = None

        for domain_id in self.list_domains():
            try:
                domain = self.load(domain_id)
            except FileNotFoundError:
                continue

            score = self._score_domain(domain, keywords)
            if score > best_score:
                best_score = score
                best_domain = domain

        # 阈值过滤：匹配度低于 0.3 认为不相关
        if best_score >= 0.3:
            logger.info("召回业务域 '%s'（匹配度 %.2f）", best_domain.domain_id, best_score)
            return best_domain

        logger.info("无匹配业务域（最高匹配度 %.2f，阈值 0.3）", best_score)
        return None

    def find_entities(self, domain_id: str, keyword: str) -> list[tuple[str, list[Attribute]]]:
        """在本体中搜索匹配的实体。

        Args:
            domain_id: 业务域 ID。
            keyword: 搜索关键字。

        Returns:
            列表，每项为 (实体名, 匹配属性列表)。
        """
        domain = self.load(domain_id)
        return domain.search_entities(keyword)

    def find_metrics(self, domain_id: str, keyword: str) -> list[tuple[str, Metric]]:
        """在本体中搜索匹配的指标。

        Args:
            domain_id: 业务域 ID。
            keyword: 搜索关键字。

        Returns:
            列表，每项为 (指标 ID, 指标定义)。
        """
        domain = self.load(domain_id)
        return domain.search_metrics(keyword)

    def get_relationship_graph(self, domain_id: str) -> str:
        """生成可导航的关系图谱（Mermaid ER 图）。

        Args:
            domain_id: 业务域 ID。

        Returns:
            Mermaid ER 图文本。
        """
        domain = self.load(domain_id)
        return domain.to_mermaid()

    def save(self, domain: DomainModel) -> None:
        """保存业务域模型到 JSON 文件。

        Args:
            domain: 要保存的业务域模型。
        """
        path = self._domains_dir / f"{domain.domain_id}.json"
        domain.to_json(path)
        # 更新缓存
        self._cache[domain.domain_id] = domain
        logger.info("保存业务域 '%s' 至 %s", domain.domain_id, path)

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._cache.clear()

    # === 内部方法 ===

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """从文本中提取关键词。

        简单实现：中文按双字 bigram 为主，英文按单词。
        过滤常见中文停用字，减少噪声匹配。
        可替换为 jieba 分词或 embedding 模型。

        Args:
            text: 输入文本。

        Returns:
            关键词列表。
        """
        import re

        # 中文停用字（高频虚词、代词、助词等，单独出现无业务含义）
        STOPWORDS = frozenset(
            "的了是在有和与或等为也不但而且如果因为所以可以已经还是就都"
            "这那个些什么怎么哪谁我你他她它们被把给让到从向对于关于按"
            "一二三四五六七八九十百千万亿上下中前后左右里外内外"
        )

        # 英文单词（长度 >= 2）
        words = re.findall(r"[a-z]+", text)
        # 中文双字 bigram（比单字更有区分度）
        chinese_bigrams = [text[i : i + 2] for i in range(len(text) - 1)]

        # 合并并去重
        all_kw: set[str] = set()
        for w in words:
            if len(w) >= 2:
                all_kw.add(w)
        for bg in chinese_bigrams:
            # 只保留两个都是中文字符的 bigram，且不在停用词中
            if all("一" <= c <= "鿿" for c in bg) and bg not in STOPWORDS:
                all_kw.add(bg)

        return list(all_kw)

    @staticmethod
    def _score_domain(domain: DomainModel, keywords: list[str]) -> float:
        """计算业务域与关键词的匹配分数。

        Args:
            domain: 业务域模型。
            keywords: 关键词列表。

        Returns:
            匹配分数（0.0 ~ 1.0）。
        """
        if not keywords:
            return 0.0

        total_matches = 0
        total_keywords = len(keywords)

        # 1. 业务域名匹配（权重 3x）
        domain_text = f"{domain.name} {domain.description}".lower()
        for kw in keywords:
            if kw in domain_text:
                total_matches += 3

        # 2. 实体名匹配（权重 2x）
        for entity_name, entity in domain.entities.items():
            entity_text = f"{entity_name} {entity.description}".lower()
            for kw in keywords:
                if kw in entity_text:
                    total_matches += 2

        # 3. 属性名匹配（权重 1x）
        for entity in domain.entities.values():
            for attr in entity.attributes:
                attr_text = f"{attr.name} {attr.description}".lower()
                for kw in keywords:
                    if kw in attr_text:
                        total_matches += 1

        # 4. 指标名匹配（权重 2x）
        for metric in domain.metrics.values():
            metric_text = f"{metric.name} {metric.description}".lower()
            for kw in keywords:
                if kw in metric_text:
                    total_matches += 2

        # 归一化到 0.0 ~ 1.0（同一关键词可匹配多个实体/属性，实际分数可超过 max_possible）
        max_possible = total_keywords * 3  # 单关键词单次匹配最大权重
        return min(1.0, total_matches / max_possible) if max_possible > 0 else 0.0
