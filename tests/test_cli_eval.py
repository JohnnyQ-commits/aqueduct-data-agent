"""aqueduct eval CLI 子命令 + 第三评估用例测试。

评估固化收尾：run_evals/render_scorecard 的完整 API 此前只有
scripts/run_evals.py 一个入口（开发者动作），固化为一等 CLI 子命令
``aqueduct eval``——模板变更守门从"记得有个脚本"降为标准命令路径。

第三用例：supply_chain_inventory（examples 既有需求文档，跨域
greenfield + 滑动窗口除零边界——与 ecommerce 用例的指标域不重叠）。

cli.main 在 Windows 上会替换 sys.stdout，测试延迟导入。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.aqueduct.evals import CaseScore, CheckResult, EvalCase, load_manifest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO_ROOT / "evals" / "manifest.json"


def _fake_score(name: str = "c1", *, failed_check: str | None = None) -> CaseScore:
    checks = [
        CheckResult("产物完整", "pass"),
        CheckResult("结构契约", "fail" if failed_check else "pass", "x" if failed_check else ""),
    ]
    return CaseScore(
        case=EvalCase(name=name, requirement="r.md", required_artifacts=[]),
        checks=checks,
        errors=[],
    )


# ============ eval 子命令解析 ============


class TestEvalParser:
    def test_eval_command_parses(self):
        from src.aqueduct.cli.main import create_parser

        args = create_parser().parse_args(["eval"])
        assert args.command == "eval"
        assert args.case is None
        assert args.out == "evals/runs"

    def test_eval_case_and_out(self):
        from src.aqueduct.cli.main import create_parser

        args = create_parser().parse_args(["eval", "--case", "iterative", "--out", "evals/runs2"])
        assert args.case == "iterative"
        assert args.out == "evals/runs2"


# ============ eval 命令执行（假管道密封） ============


@pytest.fixture
def eval_env(tmp_path, monkeypatch):
    """project_root 钉到 tmp_path，run_evals 打假（不跑真实 LLM）。

    落一个最小合法 manifest——_eval_mode 的存在性前置校验走真实路径。
    """
    from src.aqueduct.config.settings import get_settings

    manifest_dir = tmp_path / "evals"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {"cases": [{"name": "c1", "requirement": "r.md", "required_artifacts": []}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "project_root", tmp_path)
    calls = {}

    def _fake_run_evals(manifest_path, runs_dir, pipeline=None, case_filter=None):
        calls["manifest"] = Path(manifest_path)
        calls["runs_dir"] = Path(runs_dir)
        calls["filter"] = case_filter
        return [_fake_score()]

    import src.aqueduct.evals as evals_mod

    monkeypatch.setattr(evals_mod, "run_evals", _fake_run_evals)
    return tmp_path, calls


class TestEvalCommand:
    def test_eval_writes_report_and_returns_zero(self, eval_env):
        tmp_path, calls = eval_env
        from src.aqueduct.cli.main import _eval_mode

        class NS:
            case = None
            out = "evals/runs"

        rc = _eval_mode(NS())
        assert rc == 0
        assert calls["manifest"] == tmp_path / "evals" / "manifest.json"
        reports = list((tmp_path / "evals" / "runs").glob("report-*.md"))
        assert len(reports) == 1
        assert "c1" in reports[0].read_text(encoding="utf-8")
        assert "通过 1/1" in reports[0].read_text(encoding="utf-8")

    def test_eval_failure_returns_one(self, eval_env, monkeypatch):
        import src.aqueduct.evals as evals_mod

        def _failing_run_evals(manifest_path, runs_dir, pipeline=None, case_filter=None):
            return [_fake_score(failed_check="结构契约")]

        monkeypatch.setattr(evals_mod, "run_evals", _failing_run_evals)
        from src.aqueduct.cli.main import _eval_mode

        class NS:
            case = None
            out = "evals/runs"

        assert _eval_mode(NS()) == 1

    def test_eval_missing_manifest_returns_one_with_hint(self, tmp_path, monkeypatch, capsys):
        """无 manifest 时 rc=1 且提示落 stderr（不沾染 fixture 落的 manifest）。"""
        from src.aqueduct.config.settings import get_settings

        monkeypatch.setattr(get_settings(), "project_root", tmp_path)
        from src.aqueduct.cli.main import _eval_mode

        class NS:
            case = None
            out = "evals/runs"

        assert _eval_mode(NS()) == 1
        assert "manifest.json" in capsys.readouterr().err

    def test_eval_passes_case_filter(self, eval_env, monkeypatch):
        _, calls = eval_env
        import src.aqueduct.evals as evals_mod

        def _capturing_run_evals(manifest_path, runs_dir, pipeline=None, case_filter=None):
            calls["filter"] = case_filter
            return [_fake_score()]

        monkeypatch.setattr(evals_mod, "run_evals", _capturing_run_evals)
        from src.aqueduct.cli.main import _eval_mode

        class NS:
            case = "iterative"
            out = "evals/runs"

        assert _eval_mode(NS()) == 0
        assert calls["filter"] == "iterative"


# ============ 第三评估用例（manifest 锚定） ============


class TestThirdEvalCase:
    def test_manifest_has_three_cases(self):
        cases = load_manifest(_MANIFEST)
        assert len(cases) == 3
        assert [c.scenario for c in cases].count("iterative") == 1

    def test_supply_chain_case_wiring(self):
        cases = {c.name: c for c in load_manifest(_MANIFEST)}
        case = cases["supply_chain_inventory"]
        assert case.scenario == "greenfield"
        requirement = (_MANIFEST.parent / case.requirement).resolve()
        assert requirement.exists(), f"需求文档不存在: {requirement}"

    def test_supply_chain_artifacts_follow_naming(self):
        cases = {c.name: c for c in load_manifest(_MANIFEST)}
        case = cases["supply_chain_inventory"]
        assert "Phase4-supply_chain_inventory.sql" in case.required_artifacts
        for name in case.required_artifacts:
            assert name.startswith(
                ("Phase1-", "Phase2-", "Phase3-", "Phase4-", "Phase5-", "Phase6-")
            )

    def test_supply_chain_keywords_anchor_domain(self):
        """跨域锚点：库存周转指标域不得与电商用例混同。"""
        raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        case = next(c for c in raw["cases"] if c["name"] == "supply_chain_inventory")
        p4_keywords = case["required_keywords"]["Phase4-supply_chain_inventory.sql"]
        assert "turnover_days" in p4_keywords
        assert "dwd_inventory_snapshot_di" in p4_keywords
