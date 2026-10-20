"""引擎链与方言转换 util 单元测试（探查/DQC 快车道共享）。

用户拍板（2026-10-20）：探查 SQL 引擎首选 presto，其次 spark，最后 hive；
遇到不兼容语法**优先转换 SQL 语法**（同引擎重试）再降级下一个引擎。
hive SQL 是生成产物的原生方言，hive 引擎无需转换只试原句。
"""

from src.aqueduct.utils.sql_engine import (
    ENGINE_CHAIN,
    convert_sql_dialect,
    is_timeout_error,
    iter_engine_attempts,
)


class TestEngineChain:
    def test_chain_order_is_presto_spark_hive(self):
        assert ENGINE_CHAIN == ("presto", "spark", "hive")


class TestConvertSqlDialect:
    def test_hive_backticks_to_presto_quotes(self):
        out = convert_sql_dialect("select `a` from t", "presto")
        assert out == 'SELECT "a" FROM t'

    def test_spark_keeps_backticks(self):
        out = convert_sql_dialect("select `a` from t", "spark")
        assert "`a`" in out

    def test_hive_engine_identity(self):
        sql = "select a from t"
        assert convert_sql_dialect(sql, "hive") == sql

    def test_transpile_failure_returns_original(self, monkeypatch):
        """转换失败（sqlglot 缺失/内部异常）静默回原句，不阻塞快车道。"""
        import src.aqueduct.utils.sql_engine as mod

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(mod, "_transpile", boom)
        sql = "select a from t"
        assert convert_sql_dialect(sql, "presto") == sql


class TestIterEngineAttempts:
    def test_simple_sql_yields_three_engines(self):
        """无需转换的 SQL：每引擎一次，链序 presto→spark→hive。"""
        seq = list(iter_engine_attempts("select 1"))
        engines = [e for e, _ in seq]
        sqls = [s for _, s in seq]
        assert engines == ["presto", "spark", "hive"]
        assert sqls == ["select 1", "select 1", "select 1"]

    def test_conversion_differs_yields_same_engine_twice(self):
        """有方言差异（反引号→presto 双引号）：转换句紧跟原句；spark 转换仅
        大小写差异（反引号合法）被归一跳过；hive 始终原句。"""
        sql = "select `a` from t"
        seq = list(iter_engine_attempts(sql))
        assert seq == [
            ("presto", sql),
            ("presto", 'SELECT "a" FROM t'),
            ("spark", sql),
            ("hive", sql),
        ]


class TestIsTimeoutError:
    def test_matches_timeout_messages(self):
        assert is_timeout_error("任务超时 (10 min)")
        assert is_timeout_error("Query exceeded timeout")
        assert is_timeout_error("Job TIMEOUT after 300s")

    def test_non_timeout_errors_not_matched(self):
        assert not is_timeout_error("编译失败")
        assert not is_timeout_error("Invalid function 10011: nullif")
        assert not is_timeout_error("")
        assert not is_timeout_error(None)
