"""评估层测试 — src/aqueduct/evals.py（Agent 测试阶段 1：最小评估闭环）。

评估 = 跑管道 + 门禁重组为评分卡。本文件全部密封（无 LLM、无数据平台）：
- load_manifest：manifest.json 解析与缺省值
- score_case：七类检查（产物完整 / 结构契约 / 关键内容 / 规范 linter /
  DQC 用例 / 真实试跑 / 管道错误）的通过、失败、跳过路径
- render_scorecard：评分卡渲染
- run_evals：注入假管道的编排（真实管道由 scripts/run_evals.py 冒烟）

评分器直接复用门禁实现（_lint_sql_issues / validate_structure /
_parse_dqc_sql / _trial_run_issues），不复制逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.aqueduct.evals import (
    EvalCase,
    load_manifest,
    render_scorecard,
    run_evals,
    score_case,
)

# ---------------------------------------------------------------- fixtures --


@pytest.fixture(autouse=True)
def _seal_trial_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """全文件密封：默认关闭真实试跑开关。

    本机 .env 带平台凭证时 execution_enabled 为 True，score_case 会经
    _trial_run_issues 真连数据平台（health_check + LIMIT 10 试跑）——
    单测绝不能依赖网络与 cookie 有效性。试跑专项测试自行覆盖为 True。
    """
    monkeypatch.setattr("src.aqueduct.evals._trial_enabled", lambda: False)


# linter 干净的 Phase4 SQL：小写关键字 / 无 CTE / 分区过滤 / nullif 除法保护
GOOD_SQL = """\
-- 需求: demo 订单日统计
insert overwrite table dw_demo.dws_order_daily_stat
partition (inc_day = '$[time(yyyyMMdd,-1d)]')
select
    city,
    count(distinct order_id) as order_count,
    sum(pay_amount) / nullif(count(distinct customer_id), 0) as avg_amount
from dw_demo.dwd_order_info_di
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by city
;
"""

# linter 必报 Critical 的 SQL：大写关键字 + CTE
BAD_SQL = """\
-- 需求: demo 订单日统计（反面教材）
WITH base AS (
    SELECT city, order_id, pay_amount FROM dw_demo.dwd_order_info_di
)
INSERT OVERWRITE TABLE dw_demo.dws_order_daily_stat PARTITION (inc_day = '20260901')
SELECT city, COUNT(order_id) AS order_count FROM base GROUP BY city;
"""

# 12 条带 `-- [...]` 头的 DQC 用例（阈值 10）
DQC_SQL = (
    "\n".join(
        f"-- [完整性-检查{i}]\n"
        f"select count(*) from dw_demo.dws_order_daily_stat "
        f"where inc_day = '20260901' and city is not null;"
        for i in range(12)
    )
    + "\n"
)

REQUIRED_ARTIFACTS = [
    "Phase1-需求理解摘要.md",
    "Phase2-设计方案.md",
    "Phase3-表结构.sql",
    "Phase4-demo.sql",
    "Phase5-数据质量测试.sql",
    "Phase6-Design.md",
    "Phase6-知识沉淀.md",
]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_case(**overrides) -> EvalCase:
    base = {
        "name": "demo_case",
        "requirement": "cases/demo.md",
        "scenario": "greenfield",
        "required_artifacts": REQUIRED_ARTIFACTS,
        "required_keywords": {"Phase3-表结构.sql": ["order_count", "inc_day"]},
        "min_dqc_cases": 10,
    }
    base.update(overrides)
    return EvalCase(**base)


def _write_good_artifacts(out: Path) -> None:
    _write(
        out / "Phase1-需求理解摘要.md",
        "# 需求理解摘要\n\n- 目标表：dw_demo.dws_order_daily_stat\n\n"
        "### 待确认问题\n- Q1: 统计粒度是否为城市？\n",
    )
    _write(
        out / "Phase2-设计方案.md",
        "## 取数逻辑\n单表聚合，按城市分组。\n\n"
        "## 字段映射\n| 字段 | 来源 |\n|---|---|\n| order_count | count(order_id) |\n\n"
        "## 上下游依赖\n上游：dwd_order_info_di。\n",
    )
    _write(
        out / "Phase3-表结构.sql",
        "create table if not exists dw_demo.dws_order_daily_stat (\n"
        "    city string,\n    order_count bigint,\n"
        "    avg_amount decimal(16,2)\n)\n"
        "partitioned by (inc_day string)\nstored as parquet;\n",
    )
    _write(out / "Phase4-demo.sql", GOOD_SQL)
    _write(out / "Phase5-数据质量测试.sql", DQC_SQL)
    _write(
        out / "Phase6-Design.md",
        "# 需求背景\n订单日统计。\n\n## 设计方案\n单表聚合。\n\n"
        "## 表结构\n见 DDL。\n\n## 核心SQL\n见 ETL。\n\n"
        "```mermaid\ngraph LR\nA[dwd] --> B[dws]\n```\n",
    )
    _write(
        out / "Phase6-知识沉淀.md",
        "# 知识沉淀\n\n## 业务域知识\n电商订单。\n\n## 表结构经验\n分区表。\n\n"
        "## SQL开发经验\nnullif 保护。\n\n## 指标口径\norder_count 去重。\n\n"
        "## 待确认事项\n无。\n",
    )


def _good_state() -> dict:
    return {"errors": [], "fix_iterations": 1, "sql_content": GOOD_SQL}


def _check(score, name):
    return next(c for c in score.checks if c.name == name)


# ------------------------------------------------------------ load_manifest --


class TestLoadManifest:
    def test_parses_cases(self, tmp_path: Path) -> None:
        manifest = {
            "cases": [
                {
                    "name": "demo_case",
                    "requirement": "cases/demo.md",
                    "scenario": "greenfield",
                    "required_artifacts": ["Phase1-需求理解摘要.md"],
                    "required_keywords": {"Phase1-需求理解摘要.md": ["目标表"]},
                    "min_dqc_cases": 10,
                }
            ]
        }
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        cases = load_manifest(path)

        assert len(cases) == 1
        assert cases[0].name == "demo_case"
        assert cases[0].requirement == "cases/demo.md"
        assert cases[0].scenario == "greenfield"
        assert cases[0].min_dqc_cases == 10
        assert cases[0].required_keywords == {"Phase1-需求理解摘要.md": ["目标表"]}

    def test_defaults(self, tmp_path: Path) -> None:
        manifest = {
            "cases": [
                {
                    "name": "minimal",
                    "requirement": "cases/minimal.md",
                    "required_artifacts": ["Phase1-需求理解摘要.md"],
                }
            ]
        }
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")

        (case,) = load_manifest(path)

        assert case.scenario == "greenfield"
        assert case.required_keywords == {}
        assert case.min_dqc_cases == 10

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "nope.json")


# -------------------------------------------------------------- score_case --


class TestScoreCase:
    def test_all_checks_pass(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)

        score = score_case(_make_case(), out, _good_state())

        assert score.passed
        failed = [c for c in score.checks if c.status == "fail"]
        assert failed == []
        # 单元测试环境无数据平台凭证，试跑应跳过而非失败
        trial = _check(score, "真实试跑")
        assert trial.status == "skip"

    def test_missing_artifact_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        (out / "Phase6-Design.md").unlink()

        score = score_case(_make_case(), out, _good_state())

        assert not score.passed
        check = _check(score, "产物完整")
        assert check.status == "fail"
        assert "Phase6-Design.md" in check.detail

    def test_missing_contract_section_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        # 抹掉 Phase2 的"字段映射"章节（契约要求三选三）
        _write(
            out / "Phase2-设计方案.md",
            "## 取数逻辑\n单表聚合。\n\n## 上下游依赖\n上游：dwd_order_info_di。\n",
        )

        score = score_case(_make_case(), out, _good_state())

        assert not score.passed
        check = _check(score, "结构契约")
        assert check.status == "fail"
        assert "Phase2-设计方案.md" in check.detail

    def test_missing_keyword_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        _write(
            out / "Phase3-表结构.sql",
            "create table dw_demo.t (city string);\n",  # 缺 order_count / inc_day
        )

        score = score_case(_make_case(), out, _good_state())

        assert not score.passed
        check = _check(score, "关键内容")
        assert check.status == "fail"
        assert "order_count" in check.detail

    def test_linter_critical_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        _write(out / "Phase4-demo.sql", BAD_SQL)

        score = score_case(_make_case(), out, _good_state())

        assert not score.passed
        check = _check(score, "规范 linter")
        assert check.status == "fail"
        assert "Critical" in check.detail

    def test_dqc_below_minimum_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        _write(out / "Phase5-数据质量测试.sql", DQC_SQL.split("\n")[0] + "\n")

        score = score_case(_make_case(), out, _good_state())

        assert not score.passed
        check = _check(score, "DQC 用例")
        assert check.status == "fail"
        assert "有效用例" in check.detail  # 报告实际条数与阈值差距

    def test_dqc_degraded_marker_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        _write(out / "Phase5-数据质量测试.sql", DQC_SQL + "-- [DQC降级] 本次为降级产出\n")

        score = score_case(_make_case(), out, _good_state())

        assert not score.passed
        check = _check(score, "DQC 用例")
        assert check.status == "fail"
        assert "降级" in check.detail

    def test_pipeline_errors_fail(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        state = _good_state() | {"errors": ["Phase4: LLM 超时"]}

        score = score_case(_make_case(), out, state)

        assert not score.passed
        check = _check(score, "管道错误")
        assert check.status == "fail"
        assert "LLM 超时" in check.detail

    def test_trial_issues_fail_when_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        monkeypatch.setattr("src.aqueduct.evals._trial_enabled", lambda: True)
        monkeypatch.setattr(
            "src.aqueduct.evals._trial_run_issues",
            lambda state: [{"severity": "Critical", "message": "试跑失败: 表不存在"}],
        )

        score = score_case(_make_case(), out, _good_state())

        assert not score.passed
        check = _check(score, "真实试跑")
        assert check.status == "fail"
        assert "表不存在" in check.detail

    def test_fix_iterations_recorded(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        state = _good_state() | {"fix_iterations": 3}

        score = score_case(_make_case(), out, state)

        assert score.fix_iterations == 3


# ---------------------------------------------------------- render_scorecard --


class TestRenderScorecard:
    def test_contains_summary_and_case_rows(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        score = score_case(_make_case(), out, _good_state())

        md = render_scorecard([score], date="2026-09-07")

        assert "2026-09-07" in md
        assert "demo_case" in md
        assert "greenfield" in md
        assert "通过 1/1" in md  # 汇总行
        assert "修复轮数" in md  # 过程指标列

    def test_failed_case_marked(self, tmp_path: Path) -> None:
        out = tmp_path / "runs" / "demo_case"
        _write_good_artifacts(out)
        (out / "Phase1-需求理解摘要.md").unlink()
        score = score_case(_make_case(), out, _good_state())

        md = render_scorecard([score], date="2026-09-07")

        assert "通过 0/1" in md
        assert "FAIL" in md


# ----------------------------------------------------------------- run_evals --


class TestRunEvals:
    def _write_manifest(self, root: Path) -> Path:
        manifest = {
            "cases": [
                {
                    "name": "case_pass",
                    "requirement": "cases/pass.md",
                    "required_artifacts": REQUIRED_ARTIFACTS,
                    "required_keywords": {"Phase3-表结构.sql": ["order_count", "inc_day"]},
                    "min_dqc_cases": 10,
                },
                {
                    "name": "case_fail",
                    "requirement": "cases/fail.md",
                    "required_artifacts": ["Phase1-需求理解摘要.md"],
                },
            ]
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _fake_pipeline(case: EvalCase, output_dir: Path) -> dict:
        if case.name == "case_pass":
            _write_good_artifacts(output_dir)
            return {"errors": [], "fix_iterations": 1, "sql_content": GOOD_SQL}
        # 失败用例：只产出一个文档，且管道带错误
        _write(output_dir / "Phase1-需求理解摘要.md", "# 需求理解摘要\n")
        return {"errors": ["Phase4: 重试耗尽"], "fix_iterations": 0}

    def test_orchestrates_cases_and_scores(self, tmp_path: Path) -> None:
        manifest_path = self._write_manifest(tmp_path)

        scores = run_evals(manifest_path, tmp_path / "runs", pipeline=self._fake_pipeline)

        assert [s.case.name for s in scores] == ["case_pass", "case_fail"]
        assert scores[0].passed
        assert not scores[1].passed
        # 每个用例有独立产物目录
        assert (tmp_path / "runs" / "case_pass" / "Phase1-需求理解摘要.md").exists()
        assert (tmp_path / "runs" / "case_fail" / "Phase1-需求理解摘要.md").exists()

    def test_case_filter(self, tmp_path: Path) -> None:
        manifest_path = self._write_manifest(tmp_path)

        scores = run_evals(
            manifest_path,
            tmp_path / "runs",
            pipeline=self._fake_pipeline,
            case_filter="case_pass",
        )

        assert [s.case.name for s in scores] == ["case_pass"]

    def test_default_pipeline_is_real_dev(self) -> None:
        # 不传 pipeline 时必须用真实 Aqueduct().dev（防止假管道悄悄成为默认）
        import inspect

        from src.aqueduct import evals as evals_mod

        src = inspect.getsource(evals_mod)
        assert "Aqueduct()" in src


# ------------------------------------------------ 真实数据集一致性守卫 --

# 管道固定产物名（各节点 save_artifact 的全集，除按需求名参数化的两个外）
PIPELINE_ARTIFACTS = {
    "Phase1-需求理解摘要.md",
    "Phase2-设计方案.md",
    "Phase3-表结构.sql",
    "Phase4-SQL校验报告.md",
    "Phase4-试跑报告.md",
    "Phase4-字段级血缘图.md",
    "Phase4-成本预警.md",
    "Phase5-数据质量测试.sql",
    "Phase5-DQC执行报告.md",
    "Phase6-Design.md",
    "Phase6-交付总报告.md",
    "Phase6-知识沉淀.md",
    "Phase6-提效看板.md",
}
_REPO_ROOT = Path(__file__).resolve().parents[1]


class TestRealManifest:
    """守护 evals/manifest.json 与真实管道产物名的一致性。

    起因：manifest 曾误写「Phase5-质量仪表盘.md」（插件模式清单串入），
    若不拦截要真跑 43 分钟才发现产物名对不上。此契约测试先行拦截：
    节点 save_artifact 改名时，这里必须同步（与 test_context_alignment
    的键链路快照同思路）。
    """

    def _load(self) -> list[EvalCase]:
        return load_manifest(_REPO_ROOT / "evals" / "manifest.json")

    @staticmethod
    def _is_known(name: str, stem: str) -> bool:
        return (
            name in PIPELINE_ARTIFACTS
            or name == f"Phase4-{stem}.sql"
            or name == f"Phase5-{stem}_审查报告.md"
        )

    def test_artifact_names_known_to_pipeline(self) -> None:
        for case in self._load():
            stem = Path(case.requirement).stem
            for name in case.required_artifacts:
                assert self._is_known(name, stem), (
                    f"{case.name}: {name} 不是管道产物名（查节点 save_artifact）"
                )

    def test_phase4_sql_matches_requirement_stem(self) -> None:
        for case in self._load():
            stem = Path(case.requirement).stem
            assert f"Phase4-{stem}.sql" in case.required_artifacts, case.name

    def test_keyword_files_are_required_artifacts(self) -> None:
        # 关键词锚点只应打在必含产物上（否则缺文件时检查被静默跳过）
        for case in self._load():
            for filename in case.required_keywords:
                assert filename in case.required_artifacts, f"{case.name}: {filename}"

    def test_requirement_paths_exist(self) -> None:
        manifest_dir = _REPO_ROOT / "evals"
        for case in self._load():
            assert (manifest_dir / case.requirement).exists(), case.name
