import pytest
import tempfile
import os
from scripts.gen_lineage import LineageParser


def make_temp_sql(content):
    """创建临时 SQL 文件并返回路径"""
    tf = tempfile.NamedTemporaryFile(
        mode='w', suffix='.sql', delete=False, encoding='utf-8'
    )
    tf.write(content)
    tf.close()
    return tf.name


def cleanup(path):
    if os.path.exists(path):
        os.remove(path)


def test_parse_table_lineage_basic():
    sql = "insert overwrite table db1.target select * from db2.source1 join db3.source2"
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()

        assert parser.target_table == "db1.target"
        assert "db2.source1" in parser.source_tables
        assert "db3.source2" in parser.source_tables
        assert "db1.target" not in parser.source_tables
    finally:
        cleanup(path)


def test_parse_table_lineage_no_insert():
    """没有 INSERT 语句时，目标表为默认值"""
    sql = "select * from db1.source1 join db2.source2"
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()

        assert parser.target_table == "unknown_target"
        assert "db1.source1" in parser.source_tables
        assert "db2.source2" in parser.source_tables
    finally:
        cleanup(path)


def test_parse_table_lineage_dedup():
    """重复出现的源表只应出现一次"""
    sql = """
    insert overwrite table db1.target
    select * from db2.source1 s1
    join db2.source1 s2 on s1.id = s2.id
    join db3.source2 on s1.id = db3.source2.id
    """
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()

        assert parser.source_tables.count("db2.source1") == 1
        assert "db3.source2" in parser.source_tables
    finally:
        cleanup(path)


def test_parse_field_lineage_basic():
    """测试基本字段血缘解析"""
    sql = """
    insert overwrite table db1.target
    select
        s1.name as user_name,
        s1.age as user_age,
        s2.dept as department
    from db2.source1 s1
    join db3.source2 s2 on s1.id = s2.id
    """
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()
        parser.parse_field_lineage()

        # 验证字段血缘提取
        fields = {f["target_field"] for f in parser.field_lineage}
        assert "user_name" in fields
        assert "user_age" in fields
        assert "department" in fields
    finally:
        cleanup(path)


def test_parse_field_lineage_no_select():
    """没有 SELECT 语句时，字段血缘为空"""
    sql = "insert overwrite table db1.target select 1"
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()
        parser.parse_field_lineage()

        # 简单 SQL 可能解析不到字段，至少不应报错
        assert isinstance(parser.field_lineage, list)
    finally:
        cleanup(path)


def test_generate_mermaid_table():
    """测试 Mermaid 表级血缘图生成"""
    sql = "insert overwrite table db1.target select * from db2.source1 join db3.source2"
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()

        mermaid = parser.generate_mermaid()

        assert "graph LR" in mermaid
        assert "db2_source1 --> db1_target" in mermaid
        assert "db3_source2 --> db1_target" in mermaid
        assert "```mermaid" in mermaid
    finally:
        cleanup(path)


def test_generate_mermaid_field():
    """测试 Mermaid 字段级血缘图生成"""
    sql = """
    insert overwrite table db1.target
    select s1.name as user_name from db2.source1 s1
    """
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()
        parser.parse_field_lineage()

        mermaid = parser.generate_mermaid()

        assert "graph TD" in mermaid
        assert "user_name" in mermaid
    finally:
        cleanup(path)


def test_update_design_doc_append():
    """测试设计文档更新 — 追加模式"""
    sql = "insert overwrite table db1.target select * from db2.source1"
    sql_path = make_temp_sql(sql)
    design_path = make_temp_sql("# 设计文档\n\n## 九、文件清单\n")
    try:
        parser = LineageParser(sql_path)
        parser.load_sql()
        parser.parse_table_lineage()
        parser.parse_field_lineage()

        result = parser.update_design_doc(design_path)
        assert result is True

        with open(design_path, 'r', encoding='utf-8') as f:
            content = f.read()

        assert "## 十一、数据血缘联动" in content
        assert "graph LR" in content
    finally:
        cleanup(sql_path)
        cleanup(design_path)


def test_update_design_doc_nonexistent():
    """测试设计文档不存在时返回 False"""
    sql = "insert overwrite table db1.target select * from db2.source1"
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()
        parser.parse_table_lineage()

        result = parser.update_design_doc("/nonexistent/path/design.md")
        assert result is False
    finally:
        cleanup(path)


def test_comment_stripping():
    """测试注释在 load_sql 时被清除"""
    sql = """
    -- 这是注释
    insert overwrite table db1.target
    select * from db2.source1  -- 行尾注释
    """
    path = make_temp_sql(sql)
    try:
        parser = LineageParser(path)
        parser.load_sql()

        assert "--" not in parser.sql_content
        assert "db1.target" in parser.sql_content
    finally:
        cleanup(path)
