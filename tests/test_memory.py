"""Memory 层测试 — 知识召回优化。"""

from __future__ import annotations

import pytest

from src.aqueduct.memory.domain import DomainModel
from src.aqueduct.memory.recall import KnowledgeRecall
from src.aqueduct.memory.store import MemoryStore


class TestMemoryStore:
    """MemoryStore 测试。"""

    def test_store_creation(self):
        store = MemoryStore()
        assert store is not None

    def test_list_domains(self):
        store = MemoryStore()
        domains = store.list_domains()
        assert isinstance(domains, list)

    def test_load_specific_domain(self):
        store = MemoryStore()
        # 尝试加载已知存在的域
        try:
            domain = store.load("ecommerce_order")
            assert domain is not None
            assert domain.domain_id == "ecommerce_order"
        except FileNotFoundError:
            pytest.skip("Domain file not found")

    def test_load_nonexistent_domain_raises(self):
        store = MemoryStore()
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent_domain_xyz")


class TestKnowledgeRecall:
    """KnowledgeRecall 测试。"""

    def test_recall_with_empty_requirement(self):
        recall = KnowledgeRecall()
        result = recall.recall("")
        assert "domain_id" in result
        assert "entities" in result
        assert "metrics" in result

    def test_recall_limits_entities(self):
        """测试 max_entities 参数限制返回数量。"""
        recall = KnowledgeRecall()
        result = recall.recall("查询业务员数据订单信息", max_entities=2)
        entities_text = result.get("entities", "")
        if entities_text:
            entity_lines = [line for line in entities_text.split("\n") if line.startswith("-")]
            assert len(entity_lines) <= 2

    def test_recall_limits_metrics(self):
        """测试 max_metrics 参数限制返回数量。"""
        recall = KnowledgeRecall()
        result = recall.recall("查询业务员数据订单信息", max_metrics=3)
        metrics_text = result.get("metrics", "")
        if metrics_text:
            metric_lines = [line for line in metrics_text.split("\n") if line.startswith("-")]
            assert len(metric_lines) <= 3

    def test_recall_returns_expected_keys(self):
        recall = KnowledgeRecall()
        result = recall.recall("查询业务员工单数据")
        expected_keys = {"domain_id", "domain_context", "entities", "metrics", "mermaid"}
        assert set(result.keys()) == expected_keys


class TestDomainModel:
    """DomainModel 测试。"""

    def test_create_domain_model(self):
        domain = DomainModel(
            domain_id="test_domain",
            name="测试域",
            description="测试用业务域",
        )
        assert domain.domain_id == "test_domain"
        assert domain.name == "测试域"

    def test_domain_to_mermaid(self):
        domain = DomainModel(
            domain_id="test",
            name="Test",
            description="Test domain",
        )
        mermaid = domain.to_mermaid()
        assert isinstance(mermaid, str)

    def test_search_entities_empty(self):
        domain = DomainModel(
            domain_id="test",
            name="Test",
            description="Test",
        )
        results = domain.search_entities("")
        assert isinstance(results, list)
