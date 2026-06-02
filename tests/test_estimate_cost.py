import pytest
import tempfile
import os
from scripts.estimate_cost import CostEstimator


def make_temp_sql(content):
    tf = tempfile.NamedTemporaryFile(
        mode='w', suffix='.sql', delete=False, encoding='utf-8'
    )
    tf.write(content)
    tf.close()
    return tf.name


def cleanup(path):
    if os.path.exists(path):
        os.remove(path)


def test_extract_tables_basic():
    sql = "insert overwrite table db1.target select * from db2.source1 join db3.source2"
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()

        assert "db1.target" in estimator.tables
        assert "db2.source1" in estimator.tables
        assert "db3.source2" in estimator.tables
    finally:
        cleanup(path)


def test_extract_tables_dedup():
    """重复表名只应出现一次"""
    sql = """
    select * from db1.source1 s1
    join db1.source1 s2 on s1.id = s2.id
    join db2.source2 on s1.id = db2.source2.id
    """
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()

        assert estimator.tables.count("db1.source1") == 1
    finally:
        cleanup(path)


def test_check_risk_missing_partition():
    """有 WHERE 但无分区字段应报高风险"""
    sql = "select * from db1.source1 where col1 = 1"
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        assert len(estimator.risks) >= 1
        assert any("分区过滤" in r for r in estimator.risks)
    finally:
        cleanup(path)


def test_check_risk_has_partition():
    """有分区字段不应报分区风险"""
    sql = "select * from db1.source1 where inc_day = '20230101'"
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        assert not any("分区" in r for r in estimator.risks)
    finally:
        cleanup(path)


def test_check_risk_no_where():
    """没有 WHERE 子句应报极高风险"""
    sql = "select * from db1.source1"
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        assert any("极高风险" in r for r in estimator.risks)
    finally:
        cleanup(path)


def test_check_risk_cartesian_product():
    """JOIN 数量多于 ON 数量应报笛卡尔积风险"""
    sql = """
    select * from db1.source1 s1
    join db2.source2 s2
    join db3.source3 s3 on s2.id = s3.id
    """
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        assert any("笛卡尔积" in r for r in estimator.risks)
    finally:
        cleanup(path)


def test_check_risk_many_tables():
    """关联表超过 5 张应报中风险"""
    sql = """
    select * from
    db1.t1 join db1.t2 on t1.id = t2.id
    join db1.t3 on t1.id = t3.id
    join db1.t4 on t1.id = t4.id
    join db1.t5 on t1.id = t5.id
    join db1.t6 on t1.id = t6.id
    where inc_day = '20230101'
    """
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        assert any("关联表数量较多" in r for r in estimator.risks)
    finally:
        cleanup(path)


def test_check_risk_no_risks():
    """无风险 SQL 应无风险项"""
    sql = """
    select col1 from db1.source1 where inc_day = '20230101'
    """
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        assert len(estimator.risks) == 0
    finally:
        cleanup(path)


def test_generate_report_no_risks():
    """无风险时报告应显示 ✅"""
    sql = "select col1 from db1.source1 where inc_day = '20230101'"
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        report = estimator.generate_report()
        assert "✅" in report
        assert "来源表数量" in report
    finally:
        cleanup(path)


def test_generate_report_with_risks():
    """有风险时报告应显示 ⚠️"""
    sql = "select * from db1.source1 where col1 = 1"
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        report = estimator.generate_report()
        assert "⚠️" in report
    finally:
        cleanup(path)


def test_update_design_doc_append():
    """测试设计文档更新 — 追加模式"""
    sql = "select * from db1.source1 where inc_day = '20230101'"
    sql_path = make_temp_sql(sql)
    design_path = make_temp_sql("# 设计文档\n\n## 七、数据质量\n")
    try:
        estimator = CostEstimator(sql_path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        result = estimator.update_design_doc(design_path)
        assert result is True

        with open(design_path, 'r', encoding='utf-8') as f:
            content = f.read()

        assert "## 十、资源成本预估" in content
        assert "来源表数量" in content
    finally:
        cleanup(sql_path)
        cleanup(design_path)


def test_update_design_doc_nonexistent():
    """设计文档不存在时返回 False"""
    sql = "select * from db1.source1"
    path = make_temp_sql(sql)
    try:
        estimator = CostEstimator(path)
        estimator.load_sql()
        estimator.extract_tables()
        estimator.check_risks()

        result = estimator.update_design_doc("/nonexistent/path/design.md")
        assert result is False
    finally:
        cleanup(path)
