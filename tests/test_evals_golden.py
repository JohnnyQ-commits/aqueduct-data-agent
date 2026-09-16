"""Evals golden set — linter 好样本必过 + 坏样本必拦。

Evals 工程固化的确定性部分：把金样本回归从 test_validator.py 的规则级单测
升级为语料级套件——good/ 语料必须零 ERROR 零 WARN（防规则改动引入误报），
bad/ 语料每文件一违规、按 manifest 期望断言命中（防规则改动漏拦）。
与规则级单测互补：单测守规则细节，这里守语料整体。

语料全部为脱敏合成风格（公开示例域表名），可安全入库。
manifest: evals/golden/manifest.json（good 清单 + bad 期望条目）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.aqueduct.tools.validator import Validator

_REPO = Path(__file__).resolve().parent.parent
_GOLDEN = _REPO / "evals" / "golden"
_MANIFEST = json.loads((_GOLDEN / "manifest.json").read_text(encoding="utf-8"))


def _run(rel: str) -> dict:
    return Validator(str(_GOLDEN / rel)).run()


class TestGoldenGood:
    @pytest.mark.parametrize("rel", _MANIFEST["good"])
    def test_good_sample_zero_error_zero_warn(self, rel):
        """好样本必过——零 ERROR 零 WARN（含历史上误报过的 CASE 守护形态）。"""
        report = _run(rel)
        assert report["error_count"] == 0, f"{rel} 误报: {report['issues']}"
        assert report["warn_count"] == 0, f"{rel} 告警: {report['issues']}"


class TestGoldenBad:
    @pytest.mark.parametrize("item", _MANIFEST["bad"], ids=[i["file"] for i in _MANIFEST["bad"]])
    def test_bad_sample_caught(self, item):
        """坏样本必拦——期望级别 + 期望规则消息命中。"""
        report = _run(item["file"])
        hits = [
            i
            for i in report["issues"]
            if i["level"] == item["level"] and item["expect"] in i["message"]
        ]
        assert hits, f"{item['file']} 未拦到 {item['expect']!r}: {report['issues']}"


class TestManifestCoverage:
    def test_no_orphan_sql_files(self):
        """语料目录里每个 .sql 都必须在 manifest 挂号（防加语料忘登记成哑文件）。"""
        listed = set(_MANIFEST["good"]) | {i["file"] for i in _MANIFEST["bad"]}
        on_disk = {p.relative_to(_GOLDEN).as_posix() for p in _GOLDEN.rglob("*.sql")}
        assert listed == on_disk, f"孤儿语料: {on_disk - listed} / 幽灵条目: {listed - on_disk}"

    def test_bad_set_covers_all_error_rules(self):
        """坏样本集必须覆盖全部 7 类 ERROR 规则——缺一类就有盲区。"""
        expects = " | ".join(i["expect"] for i in _MANIFEST["bad"])
        for rule_marker in (
            "显式列出字段",  # 1 SELECT *
            "关键字应全小写",  # 3 关键字大小写
            "除法未做判空判零保护",  # 4 §7.2
            "禁止使用 CTE",  # 8 §6.3
            "禁止分区字段",  # 9 §1.1
            "禁止 CROSS JOIN",  # 10 §10.6
            "tmp_ 前缀",  # 11 §1.3
        ):
            assert rule_marker in expects, f"坏样本集缺规则: {rule_marker}"
