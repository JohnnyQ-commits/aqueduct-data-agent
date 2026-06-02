import pytest
import tempfile
import os
import json
from pathlib import Path
from scripts.gen_semantic_doc import (
    load_all_domains,
    generate_mermaid_er,
    domains_to_markdown,
)


DOMAIN_SAMPLE = {
    "domain_id": "test_domain",
    "name": "测试业务域",
    "description": "用于单元测试的业务域",
    "entities": {
        "User": {
            "primary_key": "user_id",
            "source": "db.users",
            "description": "用户实体",
            "attributes": [
                {"name": "user_id", "type": "string", "description": "用户ID"},
                {"name": "name", "type": "string", "description": "姓名"},
            ],
        },
        "Order": {
            "primary_key": "order_id",
            "source": "db.orders",
            "description": "订单实体",
            "attributes": [
                {"name": "order_id", "type": "string", "description": "订单ID"},
                {"name": "user_id", "type": "string", "description": "用户ID"},
            ],
        },
    },
    "relationships": [
        {
            "from": "User",
            "to": "Order",
            "name": "has_orders",
            "description": "用户拥有订单",
        }
    ],
    "metrics": {
        "total_orders": {
            "name": "总订单数",
            "expression": "COUNT(*)",
            "filter": "status = 'active'",
            "unit": "笔",
        }
    },
    "computation_chains": {
        "订单转化率": {
            "definition": "下单用户数 / 访问用户数",
            "steps": [
                {"metric": "total_orders", "description": "获取订单数"},
                {"operator": "DIVIDE", "formula": "orders / visitors"},
            ],
            "unit": "%",
            "risk_threshold": "低于 5% 需预警",
        }
    },
    "derived_attributes": {
        "user_level": {
            "logic": "CASE WHEN order_count > 10 THEN 'VIP' ELSE '普通' END",
            "values": ["VIP", "普通"],
            "description": "用户等级",
        }
    },
}


def test_load_all_domains():
    domains_dir = tempfile.mkdtemp()
    try:
        path1 = os.path.join(domains_dir, "domain1.json")
        path2 = os.path.join(domains_dir, "domain2.json")

        with open(path1, 'w') as f:
            json.dump({"domain_id": "d1", "name": "域1"}, f)
        with open(path2, 'w') as f:
            json.dump({"domain_id": "d2", "name": "域2"}, f)

        domains = load_all_domains(Path(domains_dir))

        assert len(domains) == 2
        names = {d["name"] for d in domains}
        assert "域1" in names
        assert "域2" in names
    finally:
        import shutil
        shutil.rmtree(domains_dir, ignore_errors=True)


def test_generate_mermaid_er_basic():
    mermaid = generate_mermaid_er(DOMAIN_SAMPLE)

    assert "```mermaid" in mermaid
    assert "erDiagram" in mermaid
    assert "User {" in mermaid
    assert "Order {" in mermaid
    assert "string user_id PK" in mermaid
    assert "User ||--o{ Order" in mermaid


def test_generate_mermaid_er_empty_entities():
    domain = {"entities": {}}
    mermaid = generate_mermaid_er(domain)

    assert mermaid == ""


def test_generate_mermaid_er_no_entities_key():
    domain = {"name": "empty"}
    mermaid = generate_mermaid_er(domain)

    assert mermaid == ""


def test_generate_mermaid_er_without_pk():
    domain = {
        "entities": {
            "NoPK": {
                "source": "db.nopk",
                "description": "无主键实体",
            }
        },
        "relationships": [],
    }
    mermaid = generate_mermaid_er(domain)

    assert "NoPK {" in mermaid
    # 无主键时不应出现 string xxx PK 行
    assert "PK" not in mermaid or "string" not in mermaid.split("NoPK {")[1].split("}")[0]


def test_domains_to_markdown_basic():
    md = domains_to_markdown([DOMAIN_SAMPLE])

    assert "# Data Agent 可视化知识库" in md
    assert "测试业务域" in md
    assert "`test_domain`" in md
    assert "用于单元测试的业务域" in md


def test_domains_to_markdown_toc():
    md = domains_to_markdown([DOMAIN_SAMPLE])

    assert "## 目录" in md
    assert "[测试业务域]" in md


def test_domains_to_markdown_entities_table():
    md = domains_to_markdown([DOMAIN_SAMPLE])

    assert "| 实体名 | 主键 | 物理来源 | 描述 |" in md
    assert "User" in md
    assert "`user_id`" in md
    assert "`db.users`" in md


def test_domains_to_markdown_metrics_table():
    md = domains_to_markdown([DOMAIN_SAMPLE])

    assert "| 指标名称 | 计算表达式 | 过滤条件 | 单位 |" in md
    assert "总订单数" in md
    assert "`COUNT(*)`" in md


def test_domains_to_markdown_computation_chains():
    md = domains_to_markdown([DOMAIN_SAMPLE])

    assert "### 4. 计算链路" in md
    assert "订单转化率" in md
    assert "total_orders" in md


def test_domains_to_markdown_derived_attributes():
    md = domains_to_markdown([DOMAIN_SAMPLE])

    assert "### 5. 派生属性/转换规则" in md
    assert "user_level" in md
    assert "VIP" in md


def test_domains_to_markdown_multiple_domains():
    domain2 = {
        "domain_id": "d2",
        "name": "第二个域",
        "description": "另一个业务域",
        "entities": {
            "Product": {
                "primary_key": "product_id",
                "source": "db.products",
                "description": "产品实体",
            }
        },
        "metrics": {},
    }
    md = domains_to_markdown([DOMAIN_SAMPLE, domain2])

    assert "测试业务域" in md
    assert "第二个域" in md
    assert "Product" in md


def test_domains_to_markdown_no_computation_chains():
    """没有计算链路的域不应包含该章节"""
    domain = {
        "domain_id": "simple",
        "name": "简单域",
        "description": "无计算链路",
        "entities": {
            "Item": {"primary_key": "id", "source": "db.items", "description": "物品"},
        },
        "metrics": {},
    }
    md = domains_to_markdown([domain])

    assert "Item" in md
    # 不应包含计算链路章节标题
    assert "### 4. 计算链路" not in md
