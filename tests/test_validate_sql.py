import pytest
from scripts.validate_sql import (
    check_select_star,
    check_partition_filter,
    check_keyword_case,
    check_division,
    check_join_without_on,
    check_nvl
)

def test_check_select_star():
    # 违规情况
    sql1 = "select * from table1"
    assert check_select_star(sql1, sql1.split('\n')) == True
    
    # 正常情况：显式字段
    sql2 = "select col1, col2 from table1"
    assert check_select_star(sql2, sql2.split('\n')) == False
    
    # 正常情况：UNION ALL 豁免
    sql3 = "select * from t1 union all select * from t2"
    assert check_select_star(sql3, sql3.split('\n')) == False

def test_check_partition_filter():
    # 正常情况：有分区过滤
    sql1 = "select * from t1 where inc_day = '20230101'"
    assert check_partition_filter(sql1, sql1.split('\n')) == False
    
    # 违规情况：有 WHERE 但无分区过滤
    sql2 = "select * from t1 where col1 = 1"
    assert check_partition_filter(sql2, sql2.split('\n')) == True
    
    # 正常情况：无 WHERE (通常不推荐，但此检查项只查有 WHERE 的情况)
    sql3 = "select * from t1"
    assert check_partition_filter(sql3, sql3.split('\n')) == False

def test_check_keyword_case():
    # 违规情况：大写关键字
    sql1 = "SELECT col1 FROM t1"
    assert check_keyword_case(sql1, sql1.split('\n')) == True
    
    # 正常情况：小写关键字
    sql2 = "select col1 from t1"
    assert check_keyword_case(sql2, sql2.split('\n')) == False

def test_check_division():
    # 违规情况：直接除法
    sql1 = "select a / b from t1"
    assert check_division(sql1, sql1.split('\n')) == True
    
    # 正常情况：NVL 保护
    sql2 = "select nvl(a,0) / nvl(b,1) from t1"
    assert check_division(sql2, sql2.split('\n')) == False
    
    # 正常情况：when ... > 0 保护
    sql3 = """
    case when b > 0 
    then a / b 
    else 0 end
    """
    assert check_division(sql3, sql3.split('\n')) == False

def test_check_join_without_on():
    # 正常情况：同一行有 ON
    sql1 = "join table1 t1 on a.id = t1.id"
    assert check_join_without_on(sql1, sql1.split('\n')) == False
    
    # 正常情况：下一行有 ON
    sql2 = "join table1 t1\non a.id = t1.id"
    assert check_join_without_on(sql2, sql2.split('\n')) == False
    
    # 违规情况：没有 ON
    sql3 = "join table1 t1"
    assert check_join_without_on(sql3, sql3.split('\n')) == True

def test_check_nvl():
    # 违规情况：SUM 没加 NVL
    sql1 = "select sum(col1) from t1"
    assert check_nvl(sql1, sql1.split('\n')) == True
    
    # 正常情况：SUM 加了 NVL
    sql2 = "select sum(nvl(col1,0)) from t1"
    assert check_nvl(sql2, sql2.split('\n')) == False
    
    # 正常情况：SUM 加了 case
    sql3 = "select sum(case when col1 > 0 then col1 else 0 end) from t1"
    assert check_nvl(sql3, sql3.split('\n')) == False
