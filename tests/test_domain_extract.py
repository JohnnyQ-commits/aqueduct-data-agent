"""domain_extract 模块测试：DDL/SQL 提取 + 合并逻辑。"""

from __future__ import annotations

from pathlib import Path

from src.aqueduct.utils.domain_extract import (
    _bump_patch_version,
    _find_matching_paren,
    _to_entity_name,
    create_new_domain,
    extract_entities_from_ddl,
    extract_filter_rules,
    extract_metrics_from_sql,
    load_domain_dict,
    merge_domain_updates,
    save_domain_dict,
)

# ── DDL 实体提取 ─────────────────────────────────────────────


class TestExtractEntitiesFromDDL:
    """DDL 实体提取测试。"""

    SIMPLE_DDL = """
    CREATE TABLE IF NOT EXISTS dw_demo.dwd_order_info_di (
        order_id STRING COMMENT '订单号（主键）',
        customer_id STRING COMMENT '客户ID',
        pay_amount DECIMAL(18,2) COMMENT '实付金额',
        order_time TIMESTAMP COMMENT '下单时间',
        inc_day STRING COMMENT '分区日期'
    ) COMMENT '订单明细表'
    PARTITIONED BY (inc_day STRING)
    STORED AS ORC;
    """

    def test_single_table_extraction(self):
        entities = extract_entities_from_ddl(self.SIMPLE_DDL)
        assert "OrderInfo" in entities
        entity = entities["OrderInfo"]
        assert entity["source"] == "dw_demo.dwd_order_info_di"
        assert entity["primary_key"] == "order_id"
        assert len(entity["attributes"]) == 5

    def test_column_types_normalized(self):
        entities = extract_entities_from_ddl(self.SIMPLE_DDL)
        entity = entities["OrderInfo"]
        attr_types = {a["name"]: a["type"] for a in entity["attributes"]}
        assert attr_types["order_id"] == "string"
        assert attr_types["pay_amount"] == "decimal"
        assert attr_types["order_time"] == "timestamp"

    def test_column_comments_extracted(self):
        entities = extract_entities_from_ddl(self.SIMPLE_DDL)
        attrs = {a["name"]: a for a in entities["OrderInfo"]["attributes"]}
        assert attrs["order_id"]["description"] == "订单号（主键）"
        assert attrs["pay_amount"]["description"] == "实付金额"

    def test_primary_key_from_comment(self):
        """COMMENT 中含"主键"的列自动识别为主键。"""
        entities = extract_entities_from_ddl(self.SIMPLE_DDL)
        assert entities["OrderInfo"]["primary_key"] == "order_id"

    def test_primary_key_fallback_to_id_column(self):
        """没有 COMMENT 主键时，取第一个 _id 列。"""
        ddl = """
        CREATE TABLE db.my_table (
            user_id STRING,
            name STRING,
            amount BIGINT
        );
        """
        entities = extract_entities_from_ddl(ddl)
        entity = entities["MyTable"]
        assert entity["primary_key"] == "user_id"

    def test_multiple_tables(self):
        ddl = """
        CREATE TABLE db.table_a (
            id_a STRING COMMENT '主键',
            name STRING
        );
        CREATE TABLE db.table_b (
            id_b STRING COMMENT '主键',
            value BIGINT
        );
        """
        entities = extract_entities_from_ddl(ddl)
        assert "TableA" in entities
        assert "TableB" in entities

    def test_three_part_table_name(self):
        """支持 db.schema.table 三段式表名。"""
        ddl = """
        CREATE TABLE mydb.myschema.my_table (
            id STRING COMMENT '主键'
        );
        """
        entities = extract_entities_from_ddl(ddl)
        assert "MyTable" in entities
        assert entities["MyTable"]["source"] == "mydb.myschema.my_table"

    def test_empty_ddl(self):
        assert extract_entities_from_ddl("") == {}

    def test_no_create_table(self):
        assert extract_entities_from_ddl("SELECT 1;") == {}


# ── SQL 指标提取 ─────────────────────────────────────────────


class TestExtractMetricsFromSQL:
    """SQL 指标提取测试。"""

    def test_metric_from_comment(self):
        sql = """
        SELECT
            -- 指标: gmv: 成交总额 | SUM(pay_amount) | order_status >= '20' | 元
            SUM(pay_amount) AS gmv
        FROM orders
        """
        metrics = extract_metrics_from_sql(sql)
        assert "gmv" in metrics
        m = metrics["gmv"]
        assert m["name"] == "成交总额"
        assert m["expression"] == "SUM(pay_amount)"
        assert m["filter"] == "order_status >= '20'"
        assert m["unit"] == "元"

    def test_multiple_metrics_from_comments(self):
        sql = """
        SELECT
            -- 指标: gmv: 成交总额 | SUM(pay_amount) | | 元
            -- 指标: order_cnt: 订单数 | COUNT(DISTINCT order_id) | | 单
            SUM(pay_amount) AS gmv,
            COUNT(DISTINCT order_id) AS order_cnt
        FROM orders
        """
        metrics = extract_metrics_from_sql(sql)
        assert "gmv" in metrics
        assert "order_cnt" in metrics

    def test_fallback_to_aggregation(self):
        """没有注释指标时，从聚合表达式提取。"""
        sql = """
        SELECT
            SUM(pay_amount) AS total_pay,
            COUNT(DISTINCT user_id) AS user_cnt
        FROM orders
        WHERE dt = '20240101'
        """
        metrics = extract_metrics_from_sql(sql)
        assert len(metrics) >= 2
        # 应有 SUM_pay_amount 和 COUNT_user_id
        ids = list(metrics.keys())
        assert any("pay_amount" in mid for mid in ids)
        assert any("user_id" in mid for mid in ids)

    def test_empty_sql(self):
        assert extract_metrics_from_sql("") == {}


class TestExtractFilterRules:
    """过滤规则提取测试。"""

    def test_partition_filter(self):
        sql = """
        SELECT *
        FROM orders
        WHERE inc_day = '20240101'
          AND order_status != '50'
        """
        rules = extract_filter_rules(sql)
        assert len(rules) >= 1
        assert any("inc_day" in k for k in rules)

    def test_empty_sql(self):
        assert extract_filter_rules("") == {}


# ── 合并逻辑 ─────────────────────────────────────────────────


class TestMergeDomainUpdates:
    """domain dict 合并测试。"""

    def test_new_entity_added(self):
        existing = {"entities": {"Customer": {"description": "已有"}}}
        updates = {"entities": {"Order": {"source": "db.order"}}}
        merge_domain_updates(existing, updates)
        assert "Customer" in existing["entities"]
        assert "Order" in existing["entities"]

    def test_existing_entity_not_overwritten(self):
        """已有实体保留原 description，不被覆盖。"""
        existing = {
            "entities": {
                "Order": {
                    "source": "db.old_order",
                    "description": "人工精写的描述",
                }
            }
        }
        updates = {
            "entities": {
                "Order": {
                    "source": "db.new_order",
                    "description": "自动提取的描述",
                }
            }
        }
        merge_domain_updates(existing, updates)
        assert existing["entities"]["Order"]["description"] == "人工精写的描述"
        assert existing["entities"]["Order"]["source"] == "db.old_order"

    def test_new_metric_added(self):
        existing = {"metrics": {"gmv": {"name": "GMV"}}}
        updates = {"metrics": {"order_cnt": {"name": "订单数"}}}
        merge_domain_updates(existing, updates)
        assert "gmv" in existing["metrics"]
        assert "order_cnt" in existing["metrics"]

    def test_existing_metric_not_overwritten(self):
        existing = {"metrics": {"gmv": {"expression": "人工定义"}}}
        updates = {"metrics": {"gmv": {"expression": "自动提取"}}}
        merge_domain_updates(existing, updates)
        assert existing["metrics"]["gmv"]["expression"] == "人工定义"

    def test_version_bump(self):
        existing = {"version": "1.2.3", "entities": {}, "metrics": {}, "filter_rules": {}}
        updates = {}
        merge_domain_updates(existing, updates)
        assert existing["version"] == "1.2.4"

    def test_relationships_dedup(self):
        existing = {
            "relationships": [{"from": "A", "to": "B", "condition": "A.id = B.id"}],
            "entities": {},
            "metrics": {},
            "filter_rules": {},
        }
        updates = {
            "relationships": [
                {"from": "A", "to": "B", "condition": "A.id = B.id"},  # 重复
                {"from": "B", "to": "C", "condition": "B.id = C.id"},  # 新增
            ]
        }
        merge_domain_updates(existing, updates)
        assert len(existing["relationships"]) == 2

    def test_preserves_extra_fields(self):
        """hierarchy / derived_attributes 等 Pydantic 不认识的字段应保留。"""
        existing = {
            "version": "1.0.0",
            "entities": {},
            "metrics": {},
            "filter_rules": {},
            "hierarchy": {"Status": {"A": {"rule": "x=1"}}},
            "metadata": {"owner": "test"},
        }
        updates = {"entities": {"NewEntity": {"source": "db.t"}}}
        merge_domain_updates(existing, updates)
        assert existing["hierarchy"]["Status"]["A"]["rule"] == "x=1"
        assert existing["metadata"]["owner"] == "test"


# ── 新域创建 ─────────────────────────────────────────────────


class TestCreateNewDomain:
    """新域骨架创建测试。"""

    def test_basic_structure(self):
        updates = {
            "entities": {"Order": {"source": "db.order"}},
            "metrics": {"gmv": {"name": "GMV"}},
        }
        domain = create_new_domain("test_domain", "测试域", updates)
        assert domain["domain_id"] == "test_domain"
        assert domain["name"] == "测试域"
        assert domain["version"] == "1.0.0"
        assert "Order" in domain["entities"]
        assert "gmv" in domain["metrics"]

    def test_empty_updates(self):
        domain = create_new_domain("empty", "空域", {})
        assert domain["entities"] == {}
        assert domain["metrics"] == {}


# ── domain.json 读写 ──────────────────────────────────────────


class TestDomainDictIO:
    """domain.json 文件读写测试。"""

    def test_save_and_load(self, tmp_path: Path):
        domain = {"domain_id": "test", "name": "测试", "version": "1.0.0"}
        path = tmp_path / "test" / "domain.json"
        save_domain_dict(path, domain)
        loaded = load_domain_dict(path)
        assert loaded == domain

    def test_load_nonexistent(self, tmp_path: Path):
        path = tmp_path / "nonexistent" / "domain.json"
        assert load_domain_dict(path) is None

    def test_load_invalid_json(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid", encoding="utf-8")
        assert load_domain_dict(path) is None


# ── 辅助函数 ─────────────────────────────────────────────────


class TestHelpers:
    """内部辅助函数测试。"""

    def test_to_entity_name(self):
        # dwd_order_info_di → strip dwd_ → order_info_di → strip _di → order_info → OrderInfo
        assert _to_entity_name("dwd_order_info_di") == "OrderInfo"
        # dim_customer_info_df → strip dim_ → customer_info_df → strip _df → customer_info → CustomerInfo
        assert _to_entity_name("dim_customer_info_df") == "CustomerInfo"
        assert _to_entity_name("my_table") == "MyTable"

    def test_bump_patch_version(self):
        assert _bump_patch_version("1.0.0") == "1.0.1"
        assert _bump_patch_version("2.3.4") == "2.3.5"
        assert _bump_patch_version("invalid") == "1.0.1"

    def test_find_matching_paren(self):
        #         0123456789012345678
        text = "abc(def(ghi)jkl)mno"
        # pos 3 = '(', pos 7 = '(', pos 11 = ')', pos 15 = ')'
        assert _find_matching_paren(text, 3) == 15  # 外层 (
        assert _find_matching_paren(text, 7) == 11  # 内层 (
