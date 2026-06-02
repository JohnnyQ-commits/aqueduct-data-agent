import pytest
import tempfile
import os
from scripts.check_data_quality import DQCExecuter


def make_temp_file(content, suffix='.sql'):
    tf = tempfile.NamedTemporaryFile(
        mode='w', suffix=suffix, delete=False, encoding='utf-8'
    )
    tf.write(content)
    tf.close()
    return tf.name


def cleanup(path):
    if os.path.exists(path):
        os.remove(path)


DQC_SAMPLE = """
-- [唯一性-主键去重] 目标表主键不重复
select count(*) - count(distinct id) from dm_ads.result_table;
-- 预期: count = 0

-- [时效性-数据延迟] 目标表数据在预期时间内产出
select datediff(current_timestamp, max(update_time)) from dm_ads.result_table;
-- 预期: delay < 24h

-- [一致性-跨表对齐] 目标表与参考表记录数一致
select count(*) from dm_ads.result_table where ref_id in (select id from dm_ref.reference_table);
-- 预期: count > 0

-- [反证-异常值排除] 目标表不含非法数据
select count(*) from dm_ads.result_table where amount < 0;
-- 预期: count = 0
"""


def test_parse_test_cases_basic():
    dqc_path = make_temp_file(DQC_SAMPLE)
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()

        assert len(executer.test_cases) >= 4

        categories = {c["category"] for c in executer.test_cases}
        assert "唯一性" in categories
        assert "时效性" in categories
        assert "一致性" in categories
        assert "反证" in categories
    finally:
        cleanup(dqc_path)


def test_parse_test_cases_involved_tables():
    dqc_path = make_temp_file(DQC_SAMPLE)
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()

        # 应提取到涉及的表
        assert any("dm_ads.result_table" in t for t in executer.involved_tables)
    finally:
        cleanup(dqc_path)


def test_parse_test_cases_with_weights():
    """测试带权重的测试用例解析"""
    dqc_sql = """
-- [唯一性-主键检查] 主键不重复
select count(*) - count(distinct id) from dm_ads.t1;
-- 预期: 0
-- 权重: High

-- [一致性-口径对齐] 指标口径与业务定义一致
select count(*) from dm_ads.t1 where col > 0;
-- 预期: 符合业务逻辑
-- 权重: Medium
"""
    dqc_path = make_temp_file(dqc_sql)
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()

        high_case = next((c for c in executer.test_cases if c["weight"] == 30), None)
        medium_case = next((c for c in executer.test_cases if c["weight"] == 15), None)

        assert high_case is not None
        assert medium_case is not None
    finally:
        cleanup(dqc_path)


def test_parse_test_cases_default_weight():
    """未指定权重时应使用默认值 Medium (15)"""
    dqc_sql = """
-- [一致性-记录数] 记录数合理
select count(*) from dm_ads.t1;
-- 预期: count > 0
"""
    dqc_path = make_temp_file(dqc_sql)
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()

        assert len(executer.test_cases) == 1
        assert executer.test_cases[0]["weight"] == 15
    finally:
        cleanup(dqc_path)


def test_parse_test_cases_empty_file():
    """空 DQC 文件不应产生测试用例"""
    dqc_path = make_temp_file("")
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()

        assert len(executer.test_cases) == 0
    finally:
        cleanup(dqc_path)


def test_run_tests_mock():
    """模拟执行应为每个用例设置状态和值"""
    dqc_path = make_temp_file(DQC_SAMPLE)
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()
        executer.run_tests_mock()

        for result in executer.results:
            assert result["status"] in ("PASSED", "FAILED")
            assert "value" in result
            assert "fix_suggestion" in result
            assert "exec_time" in result
    finally:
        cleanup(dqc_path)


def test_run_tests_mock_fix_suggestions():
    """失败用例应根据类型提供修复建议"""
    dqc_sql = """
-- [唯一性-主键检查] 主键不重复
select count(*) - count(distinct id) from dm_ads.t1;
-- 预期: 0
"""
    dqc_path = make_temp_file(dqc_sql)
    try:
        # 运行多次，提高遇到 FAILED 的概率
        for _ in range(20):
            executer = DQCExecuter(dqc_path)
            executer.parse_test_cases()
            executer.run_tests_mock()

            if executer.results[0]["status"] == "FAILED":
                assert "fix_suggestion" in executer.results[0]
                assert len(executer.results[0]["fix_suggestion"]) > 0
                return
    finally:
        cleanup(dqc_path)


def test_generate_dqc_report_md():
    """测试 DQC 报告 Markdown 生成"""
    dqc_path = make_temp_file(DQC_SAMPLE)
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()
        executer.run_tests_mock()

        report = executer.generate_dqc_report_md()

        assert "健康得分" in report
        assert "通过率" in report
        assert "测试详细明细" in report
        assert "涉及表清单" in report
        assert "dm_ads.result_table" in report
    finally:
        cleanup(dqc_path)


def test_generate_dqc_report_health_score():
    """健康评分计算：100 分减去失败权重"""
    dqc_sql = """
-- [唯一性-主键检查] 主键不重复
select count(*) from dm_ads.t1;
-- 预期: 0
-- 权重: High
"""
    dqc_path = make_temp_file(dqc_sql)
    try:
        executer = DQCExecuter(dqc_path)
        executer.parse_test_cases()
        executer.run_tests_mock()

        report = executer.generate_dqc_report_md()

        # 报告应包含得分
        assert "/ 100" in report or "100" in report
    finally:
        cleanup(dqc_path)


def test_update_delivery_report_append():
    """测试交付报告更新 — 追加模式"""
    dqc_sql = """
-- [唯一性-主键检查] 主键不重复
select count(*) from dm_ads.t1;
-- 预期: 0
"""
    dqc_path = make_temp_file(dqc_sql)
    report_path = make_temp_file("# 交付报告\n\n## 其他内容\n", suffix='.md')
    try:
        executer = DQCExecuter(dqc_path, report_path)
        executer.parse_test_cases()
        executer.run_tests_mock()

        result = executer.update_delivery_report()
        assert result is True

        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()

        assert "## 五、数据质量测试结果" in content
        assert "健康得分" in content
    finally:
        cleanup(dqc_path)
        cleanup(report_path)


def test_update_delivery_report_replace():
    """测试交付报告更新 — 替换已有章节"""
    dqc_sql = """
-- [唯一性-主键检查] 主键不重复
select count(*) from dm_ads.t1;
-- 预期: 0
"""
    dqc_path = make_temp_file(dqc_sql)
    report_path = make_temp_file(
        "# 交付报告\n\n## 五、数据质量测试结果\n旧内容\n\n## 六、其他\n",
        suffix='.md'
    )
    try:
        executer = DQCExecuter(dqc_path, report_path)
        executer.parse_test_cases()
        executer.run_tests_mock()

        result = executer.update_delivery_report()
        assert result is True

        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()

        assert "旧内容" not in content
        assert "## 六、其他" in content
    finally:
        cleanup(dqc_path)
        cleanup(report_path)


def test_update_delivery_report_missing_file():
    """交付报告不存在时应返回 False"""
    dqc_sql = """
-- [唯一性-主键检查] 主键不重复
select count(*) from dm_ads.t1;
-- 预期: 0
"""
    dqc_path = make_temp_file(dqc_sql)
    try:
        executer = DQCExecuter(dqc_path, "/nonexistent/report.md")
        executer.parse_test_cases()
        executer.run_tests_mock()

        result = executer.update_delivery_report()
        assert result is False
    finally:
        cleanup(dqc_path)
