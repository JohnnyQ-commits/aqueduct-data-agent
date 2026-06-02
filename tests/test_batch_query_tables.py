import pytest
import tempfile
import os
import json
from scripts.batch_query_tables import (
    extract_tables,
    generate_task_list,
    build_ddl_from_mcp_result,
    build_from_json,
)


def test_extract_tables_basic():
    text = "db1.table1 db2.table2 db3.table3"
    tables = extract_tables(text)

    assert "db1.table1" in tables
    assert "db2.table2" in tables
    assert "db3.table3" in tables


def test_extract_tables_dedup():
    text = "db1.table1 db1.table1 db2.table2"
    tables = extract_tables(text)

    assert tables.count("db1.table1") == 1
    assert len(tables) == 2


def test_extract_tables_from_sql_context():
    text = """
    select * from db1.source_table1 t1
    join db2.source_table2 t2 on t1.id = t2.id
    where db3.dim_table.status = 'active'
    """
    tables = extract_tables(text)

    assert "db1.source_table1" in tables
    assert "db2.source_table2" in tables
    assert "db3.dim_table" in tables


def test_extract_tables_empty():
    text = "no tables here"
    tables = extract_tables(text)

    assert len(tables) == 0


def test_extract_tables_single_part():
    """仅表名（无库名）不应匹配"""
    text = "select * from table1"
    tables = extract_tables(text)

    assert len(tables) == 0


def test_generate_task_list_hive():
    tables = ["db_hive.source_table", "db_hive.dim_table"]
    task_list = generate_task_list(tables)

    assert "共 2 张表" in task_list
    assert "db_hive.source_table" in task_list
    assert "db_hive.dim_table" in task_list
    assert "Type: Hive" in task_list
    assert "bdp_hive_table_search" in task_list


def test_generate_task_list_mysql():
    tables = ["mysql_rds.users", "mysql_rds.orders"]
    task_list = generate_task_list(tables)

    assert "Type: MySQL" in task_list
    assert "bdp_mysql_search" in task_list


def test_generate_task_list_mongo():
    tables = ["mongo_db.collection1"]
    task_list = generate_task_list(tables)

    assert "Type: MongoDB" in task_list
    assert "bdp_mongodb_search" in task_list


def test_build_ddl_hive():
    data = {
        "dbName": "dm_ads",
        "tblName": "result_table",
        "comment": "结果表",
        "columnList": [
            {"columnName": "id", "columnType": "string", "comment": "主键"},
            {"columnName": "name", "columnType": "string", "comment": "名称"},
        ],
    }
    ddl = build_ddl_from_mcp_result(data)

    assert "CREATE TABLE IF NOT EXISTS dm_ads.result_table" in ddl
    assert "`id` string COMMENT '主键'" in ddl
    assert "`name` string COMMENT '名称'" in ddl
    assert "PARTITIONED BY" in ddl
    assert "STORED AS PARQUET" in ddl


def test_build_ddl_mysql():
    data = {
        "dbName": "mysql_rds",
        "tblName": "users",
        "columns": [
            {"name": "id", "type": "int", "remarks": "用户ID"},
            {"name": "email", "type": "varchar(255)"},
        ],
    }
    ddl = build_ddl_from_mcp_result(data)

    assert "CREATE TABLE IF NOT EXISTS mysql_rds.users" in ddl
    assert "`id` int COMMENT '用户ID'" in ddl
    assert "`email` varchar(255)" in ddl
    # MySQL 表不应有分区
    assert "PARTITIONED BY" not in ddl


def test_build_ddl_mongo():
    data = {
        "database": "mongo_db",
        "collectionName": "events",
        "fields": [
            {"fieldName": "_id", "type": "string"},
            {"fieldName": "event_type", "columnType": "string"},
        ],
    }
    ddl = build_ddl_from_mcp_result(data)

    assert "CREATE TABLE IF NOT EXISTS mongo_db.events" in ddl
    assert "`_id` string" in ddl
    assert "`event_type` string" in ddl
    # MongoDB 表不应有分区
    assert "PARTITIONED BY" not in ddl


def test_build_ddl_no_columns():
    data = {
        "dbName": "db1",
        "tblName": "empty_table",
        "columnList": [],
    }
    ddl = build_ddl_from_mcp_result(data)

    assert "CREATE TABLE IF NOT EXISTS db1.empty_table" in ddl
    # PARTITIONED BY 行中包含 COMMENT（分区字段注释），所以只检查列注释
    assert "`id`" not in ddl


def test_build_ddl_store_type_orc():
    data = {
        "dbName": "db1",
        "tblName": "t1",
        "columnList": [{"columnName": "id", "columnType": "string"}],
        "storeType": "orc",
    }
    ddl = build_ddl_from_mcp_result(data)

    assert "STORED AS ORC" in ddl


def test_build_ddl_no_comment():
    data = {
        "dbName": "db1",
        "tblName": "t1",
        "columnList": [{"columnName": "id", "columnType": "string"}],
    }
    ddl = build_ddl_from_mcp_result(data)

    assert "CREATE TABLE IF NOT EXISTS db1.t1" in ddl
    # 列定义中不应有 COMMENT（因为没有提供注释）
    assert "`id` string COMMENT" not in ddl


def test_build_from_json():
    input_data = {
        "tables": [
            {
                "table": "db1.t1",
                "detail": {
                    "dbName": "db1",
                    "tblName": "t1",
                    "comment": "测试表",
                    "columnList": [
                        {"columnName": "id", "columnType": "string", "comment": "主键"}
                    ],
                },
            }
        ]
    }
    json_path = make_temp_json(input_data)
    output_path = json_path.replace('.json', '_out.sql')

    try:
        result = build_from_json(json_path, output_path)
        assert result == 0

        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()

        assert "CREATE TABLE IF NOT EXISTS db1.t1" in content
        assert "`id` string COMMENT '主键'" in content
    finally:
        cleanup(json_path)
        cleanup(output_path)


def test_build_from_json_empty_tables():
    input_data = {"tables": []}
    json_path = make_temp_json(input_data)
    output_path = json_path.replace('.json', '_out.sql')

    try:
        result = build_from_json(json_path, output_path)
        assert result == 1
    finally:
        cleanup(json_path)
        cleanup(output_path)


def test_build_from_json_missing_detail():
    input_data = {
        "tables": [
            {"table": "db1.t1", "detail": None}
        ]
    }
    json_path = make_temp_json(input_data)
    output_path = json_path.replace('.json', '_out.sql')

    try:
        result = build_from_json(json_path, output_path)
        assert result == 0  # 部分失败但仍返回 0

        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()

        assert "TODO" in content
        assert "db1.t1" in content
    finally:
        cleanup(json_path)
        cleanup(output_path)


def make_temp_json(data):
    tf = tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False, encoding='utf-8'
    )
    json.dump(data, tf)
    tf.close()
    return tf.name


def cleanup(path):
    if os.path.exists(path):
        os.remove(path)
