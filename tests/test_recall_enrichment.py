"""知识召回注入丰富化测试（TODO-4：属性 5→15 + filter_rules/business_rules 浮现）。

背景（CLI vs Skill 质量差距根因分析）：CLI 管道的 Phase 1 只把 domain_context
一个字符串注入全部下游 prompt，而召回摘要里实体属性截断在 5 个，
domain.json 中已建模的 filter_rules / business_rules（domain.py:160-162，
含 schema 归一化）从未浮现——审查/开发/质量阶段看不到业务规则与默认过滤条件，
是 CLI 产出比插件模式浅的单一因素之一。

契约：
- 实体属性浮现上限 5 → 15（_format_domain_summary 与 recall() 实体行两处）
- domain_context 新增「业务规则」「过滤规则」两节（非空才渲染，各上限 20 条，
  filter_rules 的非字符串值 JSON 序列化）
- recall() 结果键集不变（domain_context 是唯一进入 state 的通道，不加键）
"""

from __future__ import annotations

from src.aqueduct.memory.domain import Attribute, DomainModel, Entity
from src.aqueduct.memory.recall import KnowledgeRecall
from src.aqueduct.memory.store import MemoryStore


def _attr(n: int) -> Attribute:
    return Attribute(name=f"attr_{n:02d}", type="string", description=f"运单字段{n}")


def _make_domain(
    n_attrs: int = 20,
    business_rules: dict | None = None,
    filter_rules: dict | None = None,
) -> DomainModel:
    return DomainModel(
        domain_id="logistics_waybill",
        name="物流运单域",
        description="物流运单业务域",
        entities={
            "运单": Entity(
                primary_key="waybill_id",
                source="dw_demo.dwd_waybill_info_di",
                description="物流运单",
                attributes=[_attr(i) for i in range(1, n_attrs + 1)],
            )
        },
        metrics={},
        business_rules=business_rules or {},
        filter_rules=filter_rules or {},
    )


class TestSummaryAttributeDepth:
    """_format_domain_summary 实体属性浮现深度 5 → 15。"""

    def test_summary_lists_up_to_15_attributes(self):
        from src.aqueduct.memory.recall import KnowledgeRecall as KR

        summary = KR._format_domain_summary(_make_domain(n_attrs=20))
        assert "attr_06" in summary, "第 6 个属性应浮现（旧截断 5）"
        assert "attr_15" in summary
        assert "attr_16" not in summary, "上限 15"

    def test_summary_keeps_short_lists_intact(self):
        from src.aqueduct.memory.recall import KnowledgeRecall as KR

        summary = KR._format_domain_summary(_make_domain(n_attrs=3))
        for name in ("attr_01", "attr_02", "attr_03"):
            assert name in summary


class TestSummaryRuleSections:
    """domain_context 浮现业务规则与过滤规则（唯一进入 state 的通道）。"""

    def test_business_rules_section_rendered(self):
        from src.aqueduct.memory.recall import KnowledgeRecall as KR

        summary = KR._format_domain_summary(
            _make_domain(
                business_rules={"refund_only_original": "退款仅支持原单整单退款，不支持部分退款"}
            )
        )
        assert "### 业务规则" in summary
        assert "- **refund_only_original**: 退款仅支持原单整单退款" in summary

    def test_filter_rules_section_rendered_with_json_values(self):
        """filter_rules 值可为任意结构（dict/list），需序列化为可读文本。"""
        from src.aqueduct.memory.recall import KnowledgeRecall as KR

        summary = KR._format_domain_summary(
            _make_domain(
                filter_rules={
                    "valid_order": "order_status = 'COMPLETED'",
                    "partition": {"field": "inc_day", "format": "yyyyMMdd"},
                }
            )
        )
        assert "### 过滤规则" in summary
        assert "- **valid_order**: order_status = 'COMPLETED'" in summary
        assert "inc_day" in summary and "yyyyMMdd" in summary

    def test_empty_rules_render_no_sections(self):
        from src.aqueduct.memory.recall import KnowledgeRecall as KR

        summary = KR._format_domain_summary(_make_domain())
        assert "### 业务规则" not in summary
        assert "### 过滤规则" not in summary

    def test_rule_sections_capped_at_20(self):
        """动态域自动增量合并可能累积大量规则——每节上限 20 条控 prompt 体积。"""
        from src.aqueduct.memory.recall import KnowledgeRecall as KR

        rules = {f"rule_{i:02d}": f"规则{i}" for i in range(1, 26)}
        summary = KR._format_domain_summary(_make_domain(business_rules=rules, filter_rules=rules))
        assert "rule_20" in summary
        assert "rule_21" not in summary


class _StubStore:
    """match_domain 直接返回指定域的最小 store 桩（绕开文件系统）。"""

    def __init__(self, domain: DomainModel) -> None:
        self._domain = domain

    def match_domain(self, requirement: str) -> DomainModel | None:
        return self._domain

    def _extract_keywords(self, text: str) -> list[str]:
        return ["运单"]


class TestRecallResultEnrichment:
    """recall() 端到端：实体行属性深度 + 规则进入 domain_context。"""

    def _make_store(self, tmp_path, domain: DomainModel) -> MemoryStore:
        dynamic = tmp_path / "dynamic"
        dynamic.mkdir()
        domain.to_json(dynamic / domain.domain_id / "domain.json")
        return MemoryStore(domains_dir=tmp_path / "static", dynamic_dir=dynamic)

    def test_recall_entity_line_lists_15_matched_attrs(self):
        """需求关键词命中的实体属性行，浮现上限 5 → 15。"""
        # 构造 20 个描述含"运单"的属性，桩 store 关键词"运单"全部命中
        domain = _make_domain(n_attrs=20)
        result = KnowledgeRecall(store=_StubStore(domain)).recall("统计每日运单量与运单状态分布")
        entities = result["entities"]
        assert "attr_06" in entities, "第 6 个命中属性应浮现（旧截断 5）"
        assert "attr_15" in entities
        assert "attr_16" not in entities

    def test_recall_result_keys_unchanged(self):
        """规则不新增结果键——domain_context 是唯一注入通道（锚定既有契约）。"""
        store = _StubStore(_make_domain(business_rules={"r1": "规则一"}))
        result = KnowledgeRecall(store=store).recall("统计运单量")
        assert set(result.keys()) == {
            "domain_id",
            "domain_context",
            "entities",
            "metrics",
            "mermaid",
        }

    def test_recall_domain_context_contains_rules_end_to_end(self, tmp_path):
        """tmp store 真实 match_domain 路径：规则内容进入 domain_context。"""
        domain = _make_domain(
            business_rules={"refund_only_original": "退款仅支持原单整单退款"},
            filter_rules={"valid_waybill": "waybill_status = 'SIGNED'"},
        )
        store = self._make_store(tmp_path, domain)
        result = KnowledgeRecall(store=store).recall("统计物流运单量")

        assert result["domain_id"] == "logistics_waybill"
        assert "### 业务规则" in result["domain_context"]
        assert "退款仅支持原单整单退款" in result["domain_context"]
        assert "### 过滤规则" in result["domain_context"]
        assert "waybill_status = 'SIGNED'" in result["domain_context"]
