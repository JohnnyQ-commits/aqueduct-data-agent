import pytest
import tempfile
import os
from scripts.validate_sql import Validator

def run_validator(sql_content, strict=False):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as tf:
        tf.write(sql_content)
        temp_path = tf.name
    
    try:
        validator = Validator(temp_path, strict)
        report = validator.run()
        return report
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def has_issue(report, level, message_part):
    for issue in report["issues"]:
        if issue["level"] == level and message_part in issue["message"]:
            return True
    return False

def test_check_select_star():
    # 违规情况
    sql1 = "select * from table1"
    report1 = run_validator(sql1)
    assert has_issue(report1, "ERROR", "使用了 SELECT *") == True
    
    # 正常情况：显式字段
    sql2 = "select col1, col2 from table1"
    report2 = run_validator(sql2)
    assert has_issue(report2, "ERROR", "使用了 SELECT *") == False
    
    # 正常情况：UNION ALL 豁免
    sql3 = "select * from t1 union all select * from t2"
    report3 = run_validator(sql3)
    assert has_issue(report3, "ERROR", "使用了 SELECT *") == False

def test_check_partition_filter():
    # 正常情况：有分区过滤
    sql1 = "select * from t1 where inc_day = '20230101'"
    report1 = run_validator(sql1)
    assert has_issue(report1, "WARN", "未找到分区字段过滤") == False
    
    # 违规情况：有 WHERE 但无分区过滤
    sql2 = "select * from t1 where col1 = 1"
    report2 = run_validator(sql2)
    assert has_issue(report2, "WARN", "未找到分区字段过滤") == True
    
    # 正常情况：无 WHERE
    sql3 = "select * from t1"
    report3 = run_validator(sql3)
    assert has_issue(report3, "WARN", "未找到分区字段过滤") == False

def test_check_keyword_case():
    # 违规情况：大写关键字
    sql1 = "SELECT col1 FROM t1"
    report1 = run_validator(sql1)
    assert has_issue(report1, "WARN", "关键字应全小写") == True
    
    # 正常情况：小写关键字
    sql2 = "select col1 from t1"
    report2 = run_validator(sql2)
    assert has_issue(report2, "WARN", "关键字应全小写") == False

def test_check_division():
    # 违规情况：直接除法
    sql1 = "select a / b from t1"
    report1 = run_validator(sql1)
    assert has_issue(report1, "WARN", "除法未做判空判零") == True
    
    # 正常情况：保护处理
    sql2 = "select nvl(a,0) / nvl(b,1) from t1"
    report2 = run_validator(sql2)
    assert has_issue(report2, "WARN", "除法未做判空判零") == False

def test_check_join_without_on():
    # 正常情况：同一行有 ON
    sql1 = "select * from t1 join table1 t2 on t1.id = t2.id"
    report1 = run_validator(sql1)
    assert has_issue(report1, "WARN", "JOIN 语句缺少 ON 条件") == False
    
    # 违规情况：没有 ON
    sql2 = "select * from t1 join table2 t2"
    report2 = run_validator(sql2)
    assert has_issue(report2, "WARN", "JOIN 语句缺少 ON 条件") == True

def test_check_nvl():
    # 违规情况：SUM 没加 NVL
    sql1 = "select sum(col1) from t1"
    report1 = run_validator(sql1)
    assert has_issue(report1, "WARN", "SUM 聚合未使用 NVL 处理空值") == True
    
    # 正常情况：SUM 加了 NVL
    sql2 = "select sum(nvl(col1,0)) from t1"
    report2 = run_validator(sql2)
    assert has_issue(report2, "WARN", "SUM 聚合未使用 NVL 处理空值") == False
