import pytest
from scripts.gen_design import (
    extract_tables_from_sql,
    parse_target_table,
    parse_source_tables,
    parse_join_logic,
    parse_field_list_from_sql
)

def test_extract_tables_from_sql():
    sql = "insert overwrite table db1.table1 select * from db2.table2 join db3.table3"
    tables = extract_tables_from_sql(sql)
    assert "db1.table1" in tables
    assert "db2.table2" in tables
    assert "db3.table3" in tables

def test_parse_target_table():
    sql1 = "insert overwrite table db1.table1 select ..."
    assert parse_target_table(sql1) == "db1.table1"
    
    sql2 = "create table if not exists db1.table1 (col1 string)"
    assert parse_target_table(sql2) == "db1.table1"
    
    sql3 = "insert into db1.table1 select ..."
    assert parse_target_table(sql3) == "db1.table1"

def test_parse_source_tables():
    sql = """
    insert overwrite table db1.target_table
    select * 
    from db2.source_table1 t1
    where inc_day = '20230101'
    join db3.source_table2 t2 on t1.id = t2.id
    join db4.dim_table t3 on t1.id = t3.id
    """
    sources = parse_source_tables(sql)
    
    # 验证提取到的表（排除目标表）
    table_names = [s['table'] for s in sources]
    assert "db2.source_table1" in table_names
    assert "db3.source_table2" in table_names
    assert "db4.dim_table" in table_names
    assert "db1.target_table" not in table_names
    
    # 验证分区解析
    for s in sources:
        if s['table'] == "db2.source_table1":
            assert s['partition'] == "20230101"
        if "dim" in s['table']:
            assert s['partition'] == "快照表(无分区)"

def test_parse_join_logic():
    sql = """
    select * 
    from t1
    left join db2.table2 t2 on t1.id = t2.id
    inner join (select * from t3) t3 on t1.id = t3.id
    """
    joins = parse_join_logic(sql)
    assert len(joins) == 2
    assert joins[0]['table'] == "db2.table2"
    assert joins[0]['alias'] == "t2"
    assert "t1.id = t2.id" in joins[0]['condition']

def test_parse_field_list_from_sql():
    sql = "select col1 as field1, col2 as field2 from t1"
    fields = parse_field_list_from_sql(sql)
    assert "field1" in fields
    assert "field2" in fields
    assert "select" not in fields
