"""知识存储与查询 API — MemoryStore。

加载、缓存、搜索业务域本体模型。
支持两种目录结构：
- 域目录模式: {dir}/{domain_id}/domain.json（推荐）
- 扁平模式: {dir}/{domain_id}.json（旧版兼容）

域来源：
- 静态域（knowledge/domains）：3 个示例域，提交到 git
- 动态域（internal/knowledge）：运行时生成，gitignored
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..exceptions import DomainNotFoundError
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

    def __init__(
        self,
        domains_dir: Path | str | None = None,
        dynamic_dir: Path | str | None = None,
    ) -> None:
        """初始化知识存储。

        Args:
            domains_dir: 静态业务域目录（示例域）。
                         默认从 Settings.knowledge_dir 读取。
            dynamic_dir: 动态业务域目录（运行时生成，知识回流写入）。
                         默认从 Settings.dynamic_knowledge_dir 读取。
        """
        if domains_dir is None or dynamic_dir is None:
            from ..config.settings import get_settings

            settings = get_settings()
            if domains_dir is None:
                domains_dir = settings.knowledge_dir
            if dynamic_dir is None:
                dynamic_dir = settings.dynamic_knowledge_dir

        self._domains_dir = Path(domains_dir)
        self._dynamic_dir = Path(dynamic_dir)
        self._cache: dict[str, DomainModel] = {}

    def load(self, domain_id: str) -> DomainModel:
        """加载指定业务域。

        搜索顺序：静态域目录 → 动态域目录。

        Args:
            domain_id: 业务域 ID（如 'ecommerce_order'）。

        Returns:
            DomainModel 实例。

        Raises:
            DomainNotFoundError: 业务域文件不存在。
        """
        if domain_id not in self._cache:
            path = self._find_domain_file(domain_id)
            if path is None:
                raise DomainNotFoundError(
                    f"业务域 '{domain_id}' 不存在（已搜索: {self._domains_dir}, {self._dynamic_dir}）"
                )

            self._cache[domain_id] = DomainModel.from_json(path)
            logger.debug(
                "加载业务域 '%s': %d 实体, %d 关系",
                domain_id,
                len(self._cache[domain_id].entities),
                len(self._cache[domain_id].relationships),
            )

        return self._cache[domain_id]

    def _find_domain_file(self, domain_id: str) -> Path | None:
        """在静态域和动态域目录中查找域文件。

        Args:
            domain_id: 业务域 ID。

        Returns:
            域文件路径，未找到时返回 None。
        """
        for base_dir in (self._domains_dir, self._dynamic_dir):
            # 优先尝试域目录模式: {domain_id}/domain.json
            path = base_dir / domain_id / "domain.json"
            if path.exists():
                return path
            # 兼容扁平模式: {domain_id}.json
            path = base_dir / f"{domain_id}.json"
            if path.exists():
                return path
        return None

    def list_domains(self) -> list[str]:
        """列出所有可用业务域 ID（静态域 + 动态域合并去重）。

        Returns:
            业务域 ID 列表。
        """
        domain_ids: set[str] = set()
        for base_dir in (self._domains_dir, self._dynamic_dir):
            if not base_dir.exists():
                continue
            # 域目录模式: */domain.json
            for p in base_dir.glob("*/domain.json"):
                domain_ids.add(p.parent.name)
            # 扁平模式: *.json
            for p in base_dir.glob("*.json"):
                domain_ids.add(p.stem)

        if not domain_ids:
            logger.warning(
                "业务域目录为空: %s, %s", self._domains_dir, self._dynamic_dir
            )
        return sorted(domain_ids)

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
            except DomainNotFoundError:
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
        """保存业务域模型到动态域目录。

        动态域写入 internal/knowledge/（gitignored），
        不影响 knowledge/domains/ 中的静态示例域。

        Args:
            domain: 要保存的业务域模型。
        """
        domain_dir = self._dynamic_dir / domain.domain_id
        domain_dir.mkdir(parents=True, exist_ok=True)
        path = domain_dir / "domain.json"
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
