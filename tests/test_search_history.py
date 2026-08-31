"""历史交付物检索测试 — memory/history.py。

覆盖规格 10 个场景：表名提取（全限定名/白名单/URL噪音/表.字段/去重保序）、
output/knowledge 扫描（insert 提取含嵌套括号分区/非目标表/md/json/排除目录/changes 子目录）、
报告输出（insert 行/分区/未沉淀提示/无历史命中）、CLI 解析与脚本独立入口。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.aqueduct.memory.history import extract_table_names, format_report, search_history


def _write(path: Path, content: str) -> None:
    """在临时项目下写入文件（自动建目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _get_parser():
    """延迟导入 create_parser，避免 Windows stdout 替换问题。"""
    from src.aqueduct.cli.main import create_parser

    return create_parser()


# 含嵌套括号分区的交付 SQL（分区表达式内 $[time(yyyyMMdd,-1d)] 带内层右括号）
_SQL_NESTED_PARTITION = """\
-- 需求X: demo 指标
-- 调度: 每日 T+1
insert overwrite table dw_src.ads_demo_metric_di
partition (inc_day='$[time(yyyyMMdd,-1d)]')
select
    a,
    b
from dw_src.dwd_demo_src_di
where inc_day = '$[time(yyyyMMdd,-1d)]'
;
"""


class TestExtractTableNames:
    """场景 1-5：extract_table_names 表名提取。"""

    def test_qualified_name_extracts_table_not_db(self):
        """场景1: 全限定名 dw_src.ads_a → 提取 ads_a，库名 dw_src 不在结果。"""
        result = extract_table_names("从 dw_src.ads_a 同步数据到下游")
        assert result == ["ads_a"]
        assert "dw_src" not in result

    def test_whitelist_prefix_and_length(self):
        """场景2: 独立 token 命中白名单提取；字段名不提取。"""
        result = extract_table_names("需要 dm_demo_table 与 emp_salary_ratio 两个对象")
        assert result == ["dm_demo_table"]
        assert "emp_salary_ratio" not in result

    def test_length_threshold(self):
        """长度 ≥5 才收集：sd_ab 收集、sd_a 不收集。"""
        result = extract_table_names("临时表 sd_a 与 sd_ab")
        assert result == ["sd_ab"]

    def test_url_noise(self):
        """场景3: URL 域名不产出 example/com。"""
        result = extract_table_names("接口文档见 https://x.example.com/path")
        assert result == []

    def test_table_field_reference_keeps_left(self):
        """场景4: 表.字段 引用 — 右侧字段不提取，左侧表名兜底保留。"""
        result = extract_table_names("计算 dm_demo_table.emp_salary_ratio 字段")
        assert result == ["dm_demo_table"]
        assert "emp_salary_ratio" not in result

    def test_table_field_reference_short_table(self):
        """场景4 变体: t_a.emp_field — 两侧均不满足白名单/长度，不提取。"""
        result = extract_table_names("select t_a.emp_field from src")
        assert result == []

    def test_dedup_preserves_order(self):
        """场景5: 重复表名只出现一次，保持首次出现顺序。"""
        text = "先关联 ads_a，再关联 dm_demo_table，最后回写 dw_src.ads_a"
        assert extract_table_names(text) == ["ads_a", "dm_demo_table"]

    def test_lowercase_normalization(self):
        """输入大小写归一化为小写。"""
        assert extract_table_names("同步 DW_SRC.ADS_A 表") == ["ads_a"]

    def test_db_name_exclusion(self):
        """库名排除集: 作库名出现后，独立出现时不再收集。"""
        text = "从 ods_db.ods_orders 取数；ods_db 下还有其他表"
        assert extract_table_names(text) == ["ods_orders"]


class TestSearchHistory:
    """场景 6-8：search_history 扫描与 insert 提取。"""

    def test_sql_insert_extraction_with_nested_partition(self, tmp_path):
        """场景6: insert 目标表 + 行号 + partition，嵌套括号完整不截断。"""
        _write(tmp_path / "output/需求X/正式.sql", _SQL_NESTED_PARTITION)

        results = search_history(["ads_demo_metric_di"], tmp_path)
        entry = results["ads_demo_metric_di"]
        assert len(entry["sql_files"]) == 1

        sql_file = entry["sql_files"][0]
        assert sql_file["path"] == "output/需求X/正式.sql"
        assert len(sql_file["inserts"]) == 1

        ins = sql_file["inserts"][0]
        assert ins["line"] == 3
        assert ins["table"] == "dw_src.ads_demo_metric_di"
        assert ins["partition"] == "inc_day='$[time(yyyyMMdd,-1d)]'"

    def test_insert_into_variant_without_partition(self, tmp_path):
        """insert into 变体识别；无分区时 partition 为空。"""
        _write(
            tmp_path / "output/需求X/b.sql",
            "insert into table dw_src.ads_demo_metric_di\nselect 1\n",
        )
        results = search_history(["ads_demo_metric_di"], tmp_path)
        ins = results["ads_demo_metric_di"]["sql_files"][0]["inserts"][0]
        assert ins["table"] == "dw_src.ads_demo_metric_di"
        assert ins["partition"] == ""

    def test_insert_and_partition_on_same_line(self, tmp_path):
        """insert 与 partition 同一行。"""
        _write(
            tmp_path / "output/需求X/c.sql",
            "insert overwrite table dw_src.ads_demo_metric_di partition (inc_day='$bizdate')\nselect 1\n",
        )
        results = search_history(["ads_demo_metric_di"], tmp_path)
        ins = results["ads_demo_metric_di"]["sql_files"][0]["inserts"][0]
        assert ins["partition"] == "inc_day='$bizdate'"

    def test_partition_outside_four_line_window(self, tmp_path):
        """partition 在 insert 行起 4 行窗口之外 → 不提取。"""
        sql = (
            "insert overwrite table dw_src.ads_demo_metric_di\n"  # L1 窗口 L1-L4
            "select\n"
            "    a\n"
            "from t\n"
            "partition (inc_day='x')\n"  # L5 窗口外
        )
        _write(tmp_path / "output/需求X/d.sql", sql)
        results = search_history(["ads_demo_metric_di"], tmp_path)
        ins = results["ads_demo_metric_di"]["sql_files"][0]["inserts"][0]
        assert ins["partition"] == ""

    def test_file_hit_but_no_insert_for_table(self, tmp_path):
        """场景7: 文件命中（FROM 引用）但非目标表 insert → inserts 为空。"""
        sql = (
            "select a from dw_src.ads_demo_metric_di where inc_day='20240101';\n"
            "insert overwrite table dw_src.dm_other_table partition (dt='1')\n"
            "select 1\n"
        )
        _write(tmp_path / "output/需求X/e.sql", sql)

        results = search_history(["ads_demo_metric_di"], tmp_path)
        entry = results["ads_demo_metric_di"]
        assert len(entry["sql_files"]) == 1
        assert entry["sql_files"][0]["inserts"] == []

    def test_md_hits_and_json_domains_and_excluded_dirs(self, tmp_path):
        """场景8: md 命中行计数、json 语义模型记录、排除目录跳过。"""
        md = (
            "\n"
            "ads_demo_metric_di 是月维度表，每月末产出。\n"
            "\n"
            "ads_demo_metric_di 分区为上月末日期。\n"
        )
        _write(tmp_path / "output/需求X/知识沉淀.md", md)
        _write(
            tmp_path / "knowledge/domains/demo_metric.json",
            '{"entities": [{"table": "dw_src.ads_demo_metric_di"}]}',
        )
        # 排除目录下的文件应被跳过
        _write(
            tmp_path / "output/需求X/.git/hidden.sql",
            "insert overwrite table dw_src.ads_demo_metric_di\nselect 1\n",
        )
        _write(
            tmp_path / "output/需求X/node_modules/pkg.sql",
            "insert overwrite table dw_src.ads_demo_metric_di\nselect 1\n",
        )

        results = search_history(["ads_demo_metric_di"], tmp_path)
        entry = results["ads_demo_metric_di"]

        docs = entry["docs"]
        assert len(docs) == 1
        assert docs[0]["path"] == "output/需求X/知识沉淀.md"
        assert docs[0]["hit_count"] == 2
        assert len(docs[0]["hits"]) == 2
        assert any("月维度表" in hit for hit in docs[0]["hits"])

        assert entry["domains"] == ["knowledge/domains/demo_metric.json"]
        # 排除目录中的 SQL 未被收录
        assert entry["sql_files"] == []

    def test_md_hits_capped_at_five(self, tmp_path):
        """md 命中行最多展示 5 行，hit_count 记总数。"""
        md = "\n".join(f"第{i}行提到 ads_demo_metric_di" for i in range(8)) + "\n"
        _write(tmp_path / "output/需求Y/知识沉淀.md", md)

        results = search_history(["ads_demo_metric_di"], tmp_path)
        doc = results["ads_demo_metric_di"]["docs"][0]
        assert doc["hit_count"] == 8
        assert len(doc["hits"]) == 5

    def test_changes_subdirectory_scanned(self, tmp_path):
        """output/{需求名}/changes/ 变更目录同样被扫描。"""
        _write(
            tmp_path / "output/需求X/changes/CR-001/变更.sql",
            "insert into table dw_src.ads_demo_metric_di\nselect 1\n",
        )
        results = search_history(["ads_demo_metric_di"], tmp_path)
        assert (
            results["ads_demo_metric_di"]["sql_files"][0]["path"]
            == "output/需求X/changes/CR-001/变更.sql"
        )

    def test_case_insensitive_match(self, tmp_path):
        """词边界匹配忽略大小写（大写表名可命中）。"""
        _write(tmp_path / "output/需求X/知识沉淀.md", "ADS_DEMO_METRIC_DI 是月维度表。\n")
        _write(
            tmp_path / "output/需求X/f.sql",
            "INSERT OVERWRITE TABLE DW_SRC.ADS_DEMO_METRIC_DI\nSELECT 1\n",
        )
        results = search_history(["ads_demo_metric_di"], tmp_path)
        entry = results["ads_demo_metric_di"]
        assert entry["docs"][0]["hit_count"] == 1
        ins = entry["sql_files"][0]["inserts"][0]
        assert ins["table"].lower() == "dw_src.ads_demo_metric_di"

    def test_table_names_normalized_lowercase(self, tmp_path):
        """检索表名归一化为小写作为返回 key。"""
        _write(tmp_path / "output/需求X/知识沉淀.md", "ads_demo_metric_di 说明\n")
        results = search_history(["ADS_DEMO_METRIC_DI"], tmp_path)
        assert "ads_demo_metric_di" in results

    def test_no_hits(self, tmp_path):
        """零命中：三类结果均为空列表。"""
        _write(tmp_path / "output/需求X/知识沉淀.md", "无关内容\n")
        results = search_history(["ads_never_seen"], tmp_path)
        entry = results["ads_never_seen"]
        assert entry == {"sql_files": [], "docs": [], "domains": []}


class TestFormatReport:
    """场景 9：format_report 报告输出。"""

    def test_report_with_insert_partition_and_hint(self, tmp_path):
        """insert 行/分区/未沉淀提示。"""
        _write(tmp_path / "output/需求X/正式.sql", _SQL_NESTED_PARTITION)
        _write(
            tmp_path / "output/需求X/知识沉淀.md", "ads_demo_metric_di 是月维度表，每月末产出。\n"
        )

        results = search_history(["ads_demo_metric_di"], tmp_path)
        report = format_report(results)

        assert "=== 历史交付物检索 ===" in report
        assert "[表] ads_demo_metric_di — 命中 2 个文件" in report
        assert "[交付SQL] output/需求X/正式.sql" in report
        assert "L3: insert overwrite table dw_src.ads_demo_metric_di" in report
        assert "partition (inc_day='$[time(yyyyMMdd,-1d)]')" in report
        assert "[知识文档] output/需求X/知识沉淀.md (1 处)" in report
        assert "[提示] ads_demo_metric_di 未沉淀语义模型" in report
        assert "建议补建 knowledge/domains/*.json" in report

    def test_report_with_domain_no_hint(self, tmp_path):
        """命中语义模型 → 输出语义模型行，无未沉淀提示。"""
        _write(tmp_path / "output/需求X/正式.sql", _SQL_NESTED_PARTITION)
        _write(
            tmp_path / "knowledge/domains/demo_metric.json",
            '{"entities": [{"table": "dw_src.ads_demo_metric_di"}]}',
        )

        results = search_history(["ads_demo_metric_di"], tmp_path)
        report = format_report(results)

        assert "[语义模型] knowledge/domains/demo_metric.json" in report
        assert "未沉淀语义模型" not in report

    def test_report_zero_hit(self):
        """零命中输出。"""
        results = {"ads_never_seen": {"sql_files": [], "docs": [], "domains": []}}
        report = format_report(results)
        assert "[表] ads_never_seen — 无历史命中（新表或未开发过）" in report


class TestSearchHistoryCLI:
    """场景 10：CLI search-history 子命令。"""

    def test_parse_tables(self):
        parser = _get_parser()
        args = parser.parse_args(["search-history", "ads_x", "dm_y"])
        assert args.command == "search-history"
        assert args.tables == ["ads_x", "dm_y"]

    def test_parse_doc_and_root(self):
        parser = _get_parser()
        args = parser.parse_args(["search-history", "--doc", "需求文档.md", "--root", "/tmp/proj"])
        assert args.doc == "需求文档.md"
        assert args.tables == []
        assert args.root == "/tmp/proj"

    def test_parse_tables_with_doc(self):
        parser = _get_parser()
        args = parser.parse_args(["search-history", "ads_x", "dm_y", "--doc", "req.md"])
        assert args.tables == ["ads_x", "dm_y"]
        assert args.doc == "req.md"

    def test_handler_merges_tables_and_doc(self, tmp_path, capsys):
        """tables 与 --doc 组合：合并去重后检索。"""
        from src.aqueduct.cli.main import _search_history

        doc = tmp_path / "req.md"
        doc.write_text("从 dw_src.ads_a 取数，写入 dm_demo_table\n", encoding="utf-8")
        _write(tmp_path / "output/需求X/a.sql", "insert overwrite table dw_src.ads_a\nselect 1\n")

        parser = _get_parser()
        args = parser.parse_args(
            ["search-history", "ads_a", "--doc", str(doc), "--root", str(tmp_path)]
        )

        assert _search_history(args) == 0
        out = capsys.readouterr().out
        assert "[表] ads_a — 命中 1 个文件" in out
        assert "L1: insert overwrite table dw_src.ads_a" in out
        assert "[表] dm_demo_table — 无历史命中" in out

    def test_handler_no_tables_returns_error(self, capsys):
        """无表名且无 --doc → 返回 1。"""
        from src.aqueduct.cli.main import _search_history

        parser = _get_parser()
        args = parser.parse_args(["search-history"])
        assert _search_history(args) == 1

    def test_handler_missing_doc_returns_error(self):
        """--doc 指向不存在的文件 → 返回 1。"""
        from src.aqueduct.cli.main import _search_history

        parser = _get_parser()
        args = parser.parse_args(["search-history", "--doc", "nope.md", "--root", "."])
        assert _search_history(args) == 1


class TestScriptEntry:
    """__main__ 独立脚本入口（免虚拟环境直跑）。"""

    @property
    def _script(self) -> Path:
        return Path(__file__).resolve().parents[1] / "src" / "aqueduct" / "memory" / "history.py"

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self._script), *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )

    def test_script_with_tables_and_doc(self, tmp_path):
        """位置参数 + --doc 合并检索（GBK 控制台兼容由入口 reconfigure 保证）。"""
        _write(
            tmp_path / "output/需求X/正式.sql", "insert overwrite table dw_src.ads_a\nselect 1\n"
        )
        doc = tmp_path / "req.md"
        doc.write_text("目标表 dm_demo_table\n", encoding="utf-8")

        proc = self._run("ads_a", "--doc", str(doc), "--root", str(tmp_path))
        assert proc.returncode == 0, proc.stderr
        assert "[表] ads_a — 命中 1 个文件" in proc.stdout
        assert "L1: insert overwrite table dw_src.ads_a" in proc.stdout
        assert "[表] dm_demo_table — 无历史命中" in proc.stdout

    def test_script_no_args_prints_help(self):
        proc = self._run()
        assert proc.returncode == 1
        assert "usage" in proc.stdout.lower()
