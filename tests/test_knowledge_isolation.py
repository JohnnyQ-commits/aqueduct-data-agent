"""知识库写入隔离测试（套件泄漏写真实 internal/knowledge 的回归钉）。

缺陷实录（2026-09-11 发现）：report.py 的 ``_update_domain_json`` /
``_regenerate_semantic_docs`` 硬编码 ``project_root/internal/knowledge/domains``，
而 node_report 无条件执行两者——任何带 DDL/SQL 的测试都会把实体/指标合并进
真实内部知识库并重写全部 semantic-model.md。实锤痕迹：

- ``internal/knowledge/domains/kn_spec_test/``（2026-09-09 14:27 创建）——
  域名即 tests/test_knowledge_speculative.py 的 requirement_name，纯测试泄漏产物
- ``order_refund_iterative/semantic-model.md`` 在一次全量跑测窗口内被重写
  （mtime 19:53:34 恰在 pytest 运行区间），召回语料漂移 → recall Top-K 翻转 →
  ``test_memory.py::test_recall_populates_domain_context`` 环境性失败

修复两层：
1. 两个写点改走 ``settings.dynamic_knowledge_dir``（生产默认解析路径不变，
   仍为 internal/knowledge/domains——零行为变化，但变为可重定向）
2. conftest autouse fixture 把 dynamic_knowledge_dir 重定向到 tmp_path
   （测试永不写真库，兜底未来新写点）
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REAL_DOMAINS = _PROJECT_ROOT / "internal" / "knowledge" / "domains"

_DDL = (
    "CREATE TABLE IF NOT EXISTS dw_demo.isolation_probe (\n"
    "    order_id STRING COMMENT '订单ID',\n"
    "    amount DECIMAL(18,2) COMMENT '金额'\n"
    ") COMMENT '隔离探针表';\n"
)


def _redirect_dynamic_dir(monkeypatch, tmp_path) -> Path:
    """把 settings.dynamic_knowledge_dir 钉到 tmp（与 conftest autouse 同语义）。"""
    from src.aqueduct.config.settings import get_settings

    fake_dynamic = tmp_path / "dynamic_knowledge"
    monkeypatch.setattr(get_settings(), "dynamic_knowledge_dir", fake_dynamic, raising=False)
    return fake_dynamic


def _make_state() -> dict:
    return {
        "requirement": "隔离探针需求",
        "mode": "dev",
        "metadata": {"requirement_name": "isolation_probe"},
        "errors": [],
        "artifacts": [],
        "ddl_content": _DDL,
        "sql_content": "",
    }


class TestUpdateDomainJsonIsolation:
    """_update_domain_json 写点必须走 settings.dynamic_knowledge_dir。"""

    def test_writes_under_redirected_dynamic_dir(self, monkeypatch, tmp_path):
        from src.aqueduct.engine.nodes.report import _update_domain_json

        fake_dynamic = _redirect_dynamic_dir(monkeypatch, tmp_path)
        state = _make_state()

        _update_domain_json(state)

        assert (fake_dynamic / "domains" / "isolation_probe" / "domain.json").is_file()

    def test_never_creates_domain_in_real_knowledge(self, monkeypatch, tmp_path):
        from src.aqueduct.engine.nodes.report import _update_domain_json

        _redirect_dynamic_dir(monkeypatch, tmp_path)
        state = _make_state()

        _update_domain_json(state)

        assert not (_REAL_DOMAINS / "isolation_probe").exists()


class TestRegenerateSemanticDocsIsolation:
    """_regenerate_semantic_docs 写点必须走 settings.dynamic_knowledge_dir，
    且真实知识库语义文档一个字节都不能动（19:53 重写事故的回归钉）。"""

    def test_writes_semantic_doc_under_redirected_dir(self, monkeypatch, tmp_path):
        from src.aqueduct.engine.nodes.report import (
            _regenerate_semantic_docs,
            _update_domain_json,
        )

        fake_dynamic = _redirect_dynamic_dir(monkeypatch, tmp_path)
        state = _make_state()
        _update_domain_json(state)

        _regenerate_semantic_docs(state)

        assert (fake_dynamic / "domains" / "isolation_probe" / "semantic-model.md").is_file()

    def test_real_semantic_doc_untouched(self, monkeypatch, tmp_path):
        sentinel = _REAL_DOMAINS / "order_refund_iterative" / "semantic-model.md"
        if not sentinel.is_file():
            pytest.skip("本机无 order_refund_iterative 域（开源环境），跳过真库哨兵检查")
        before = sentinel.read_bytes()

        from src.aqueduct.engine.nodes.report import (
            _regenerate_semantic_docs,
            _update_domain_json,
        )

        _redirect_dynamic_dir(monkeypatch, tmp_path)
        state = _make_state()
        _update_domain_json(state)
        _regenerate_semantic_docs(state)

        assert sentinel.read_bytes() == before
