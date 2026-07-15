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
        from src.aqueduct.exceptions import DomainNotFoundError

        store = MemoryStore()
        with pytest.raises(DomainNotFoundError):
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


class TestNodeRequirementKnowledgeRecall:
    """node_requirement 知识召回集成测试。

    验证 node_requirement 节点执行后：
    - state["domain_context"] 被正确填充
    - 无匹配时 domain_context 为空字符串，不报错
    - 下游节点可正常读取 domain_context
    """

    def _make_state(self, requirement: str) -> dict:
        """构造最小可用的 WorkflowState。"""
        return {
            "requirement": requirement,
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }

    def test_recall_populates_domain_context(self):
        """匹配到领域知识时，domain_context 非空。"""
        from src.aqueduct.engine.nodes.requirement import _recall_domain_knowledge

        state = self._make_state(
            "电商平台需要统计每日订单数和成交金额，"
            "基于 dw_demo.dwd_order_info_di 订单表和 dw_demo.dim_customer_info_df 客户表"
        )
        _recall_domain_knowledge(state)

        # 电商需求应匹配到 ecommerce_order 领域
        assert state.get("domain_id") == "ecommerce_order"
        assert state.get("domain_context") != ""
        assert len(state["domain_context"]) > 50

    def test_recall_no_match_returns_empty(self):
        """无匹配领域时，domain_context 为空字符串，不抛异常。"""
        from src.aqueduct.engine.nodes.requirement import _recall_domain_knowledge

        state = self._make_state("完全无关的随机内容 xyzabc123")
        _recall_domain_knowledge(state)

        # 无匹配时 domain_id 和 domain_context 应为空
        assert state.get("domain_id") == ""
        assert state.get("domain_context") == ""

    def test_recall_empty_requirement_no_crash(self):
        """需求为空时不崩溃。"""
        from src.aqueduct.engine.nodes.requirement import _recall_domain_knowledge

        state = self._make_state("")
        _recall_domain_knowledge(state)

        assert state.get("domain_id") == ""
        assert state.get("domain_context") == ""

    def test_domain_context_available_to_downstream(self):
        """验证召回结果可被下游节点读取。"""
        from src.aqueduct.engine.nodes.requirement import _recall_domain_knowledge

        state = self._make_state("统计每日订单GMV和客户数，数据来源 dw_demo.dwd_order_info_di")
        _recall_domain_knowledge(state)

        # 模拟下游节点的读取方式（和 sql.py / ddl.py 等一致）
        domain_ctx = state.get("domain_context", "")
        assert isinstance(domain_ctx, str)
        if state.get("domain_id"):
            assert len(domain_ctx) > 0


class TestMemoryStoreDualDirectory:
    """MemoryStore 双目录（静态域 + 动态域）测试。"""

    def test_read_from_static_dir(self, tmp_path):
        """从静态域目录读取域。"""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        domain_file = static_dir / "test_domain" / "domain.json"
        domain_file.parent.mkdir()
        domain = DomainModel(domain_id="test_domain", name="测试", description="测试域")
        domain.to_json(domain_file)

        store = MemoryStore(domains_dir=static_dir, dynamic_dir=tmp_path / "dynamic")
        loaded = store.load("test_domain")
        assert loaded.domain_id == "test_domain"

    def test_read_from_dynamic_dir(self, tmp_path):
        """从动态域目录读取域。"""
        dynamic_dir = tmp_path / "dynamic"
        dynamic_dir.mkdir()
        domain_file = dynamic_dir / "dynamic_domain" / "domain.json"
        domain_file.parent.mkdir()
        domain = DomainModel(domain_id="dynamic_domain", name="动态域", description="运行时生成")
        domain.to_json(domain_file)

        store = MemoryStore(domains_dir=tmp_path / "static", dynamic_dir=dynamic_dir)
        loaded = store.load("dynamic_domain")
        assert loaded.domain_id == "dynamic_domain"

    def test_static_takes_priority_over_dynamic(self, tmp_path):
        """静态域优先于动态域（同名时）。"""
        static_dir = tmp_path / "static"
        dynamic_dir = tmp_path / "dynamic"
        for d in (static_dir, dynamic_dir):
            d.mkdir()
            (d / "shared_domain").mkdir()

        static_domain = DomainModel(domain_id="shared_domain", name="静态版", description="static")
        static_domain.to_json(static_dir / "shared_domain" / "domain.json")

        dynamic_domain = DomainModel(domain_id="shared_domain", name="动态版", description="dynamic")
        dynamic_domain.to_json(dynamic_dir / "shared_domain" / "domain.json")

        store = MemoryStore(domains_dir=static_dir, dynamic_dir=dynamic_dir)
        loaded = store.load("shared_domain")
        assert loaded.name == "静态版"

    def test_list_domains_merges_both(self, tmp_path):
        """list_domains 合并静态域和动态域。"""
        static_dir = tmp_path / "static"
        dynamic_dir = tmp_path / "dynamic"
        for d in (static_dir, dynamic_dir):
            d.mkdir()

        (static_dir / "domain_a").mkdir()
        DomainModel(domain_id="domain_a", name="A", description="").to_json(
            static_dir / "domain_a" / "domain.json"
        )
        (dynamic_dir / "domain_b").mkdir()
        DomainModel(domain_id="domain_b", name="B", description="").to_json(
            dynamic_dir / "domain_b" / "domain.json"
        )

        store = MemoryStore(domains_dir=static_dir, dynamic_dir=dynamic_dir)
        domains = store.list_domains()
        assert domains == ["domain_a", "domain_b"]

    def test_save_writes_to_dynamic_dir(self, tmp_path):
        """save() 写入动态域目录，不写入静态域目录。"""
        static_dir = tmp_path / "static"
        dynamic_dir = tmp_path / "dynamic"
        static_dir.mkdir()
        dynamic_dir.mkdir()

        store = MemoryStore(domains_dir=static_dir, dynamic_dir=dynamic_dir)
        domain = DomainModel(domain_id="new_domain", name="新域", description="运行时生成")
        store.save(domain)

        # 动态目录应有文件
        assert (dynamic_dir / "new_domain" / "domain.json").exists()
        # 静态目录不应有文件
        assert not (static_dir / "new_domain").exists()

    def test_load_nonexistent_raises_with_both_dirs(self, tmp_path):
        """域不存在时异常信息包含两个搜索目录。"""
        from src.aqueduct.exceptions import DomainNotFoundError

        store = MemoryStore(domains_dir=tmp_path / "static", dynamic_dir=tmp_path / "dynamic")
        with pytest.raises(DomainNotFoundError, match="已搜索"):
            store.load("nonexistent")

    def test_nonexistent_dirs_no_crash(self, tmp_path):
        """目录不存在时不崩溃，返回空列表。"""
        store = MemoryStore(domains_dir=tmp_path / "nope1", dynamic_dir=tmp_path / "nope2")
        assert store.list_domains() == []


class TestEmbedderMockBackend:
    """Embedder mock 后端测试（不依赖 sentence-transformers）。"""

    def test_embed_returns_vector(self):
        """embed() 返回正确维度的向量。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock")
        vec = embedder.embed("测试文本")
        assert len(vec) == 384
        assert all(isinstance(v, float) for v in vec)

    def test_embed_is_normalized(self):
        """embed() 返回的向量已归一化（L2 范数 ≈ 1.0）。"""
        import math

        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock")
        vec = embedder.embed("归一化测试")
        norm = math.sqrt(sum(v * v for v in vec))
        assert abs(norm - 1.0) < 1e-6

    def test_embed_deterministic(self):
        """相同文本产生相同向量。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock")
        v1 = embedder.embed("相同内容")
        v2 = embedder.embed("相同内容")
        assert v1 == v2

    def test_embed_different_texts_different_vectors(self):
        """不同文本产生不同向量。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock")
        v1 = embedder.embed("文本 A")
        v2 = embedder.embed("文本 B")
        assert v1 != v2

    def test_embed_batch(self):
        """embed_batch() 返回与输入等长的向量列表。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock")
        texts = ["文本一", "文本二", "文本三"]
        vecs = embedder.embed_batch(texts)
        assert len(vecs) == 3
        assert all(len(v) == 384 for v in vecs)

    def test_embed_batch_empty(self):
        """embed_batch() 空输入返回空列表。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock")
        assert embedder.embed_batch([]) == []

    def test_custom_dim(self):
        """mock 后端支持自定义维度。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock", model="128")
        vec = embedder.embed("自定义维度")
        assert len(vec) == 128

    def test_dim_property(self):
        """dim 属性返回向量维度。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="mock")
        assert embedder.dim == 384

    def test_unknown_backend_raises(self):
        """未知后端名称抛出 ValueError。"""
        from src.aqueduct.memory.embedder import Embedder

        embedder = Embedder(backend="nonexistent")
        with pytest.raises(ValueError, match="未知"):
            embedder.embed("test")


class TestCosineSimilarity:
    """余弦相似度和 Top-K 检索测试。"""

    def test_identical_vectors(self):
        """相同向量的相似度为 1.0。"""
        from src.aqueduct.memory.embedder import cosine_similarity

        vec = [1.0, 0.0, 0.0]
        assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        """正交向量的相似度为 0.0。"""
        from src.aqueduct.memory.embedder import cosine_similarity

        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        """反向向量的相似度为 -1.0。"""
        from src.aqueduct.memory.embedder import cosine_similarity

        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6

    def test_dimension_mismatch_raises(self):
        """维度不匹配时抛出 ValueError。"""
        from src.aqueduct.memory.embedder import cosine_similarity

        with pytest.raises(ValueError, match="维度不匹配"):
            cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])

    def test_zero_vector(self):
        """零向量相似度为 0.0。"""
        from src.aqueduct.memory.embedder import cosine_similarity

        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_top_k_similar(self):
        """Top-K 检索返回正确的排序结果。"""
        from src.aqueduct.memory.embedder import top_k_similar

        query = [1.0, 0.0, 0.0]
        candidates = [
            ("A", [0.9, 0.1, 0.0]),
            ("B", [0.1, 0.9, 0.0]),
            ("C", [0.95, 0.05, 0.0]),
            ("D", [-0.5, 0.5, 0.0]),
        ]
        result = top_k_similar(query, candidates, k=2)
        assert len(result) == 2
        assert result[0][0] == "C"  # 最高相似度
        assert result[1][0] == "A"

    def test_top_k_with_threshold(self):
        """Top-K 检索支持阈值过滤。"""
        from src.aqueduct.memory.embedder import top_k_similar

        query = [1.0, 0.0]
        candidates = [
            ("high", [0.99, 0.01]),
            ("low", [-0.5, 0.5]),
        ]
        result = top_k_similar(query, candidates, k=5, threshold=0.5)
        assert len(result) == 1
        assert result[0][0] == "high"

    def test_mock_embedder_semantic_ordering(self):
        """mock 后端的 Top-K 检索：返回结果不为空。"""
        from src.aqueduct.memory.embedder import Embedder, top_k_similar

        embedder = Embedder(backend="mock")
        query_vec = embedder.embed("电商订单统计")
        candidates = [
            ("订单分析", embedder.embed("订单分析")),
            ("完全不同的内容", embedder.embed("完全不同的内容")),
            ("电商相关", embedder.embed("电商相关")),
        ]
        result = top_k_similar(query_vec, candidates, k=3, threshold=-1.0)
        # mock 后端是哈希向量，语义顺序不确定，但应返回 3 个结果
        assert len(result) == 3
        # 第一个结果的相似度应 >= 第二个
        assert result[0][1] >= result[1][1]
