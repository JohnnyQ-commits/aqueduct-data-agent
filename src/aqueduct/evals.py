"""评估层（Agent 测试阶段 1：最小评估闭环）。

评估 = 跑一遍 dev 管道 + 把 v0.6.0 门禁重组为评分卡。评分器直接
import 门禁实现，不复制逻辑——门禁改了，评估口径自动跟着改。

定位（见 Agent测试体系规划）：模板变更守门 + 定期回归。
永不进 per-commit CI——单次评估 = 1 次真实 LLM 跑（43-96 分钟），
且网关不稳定会污染结果。入口是 ``scripts/run_evals.py``（开发者动作，
不进 CLI 主命令）。

评估产物写 ``evals/runs/``，与真实交付 ``output/`` 分离：
评估产物是回归基准，不是交付物。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .engine.contract import validate_structure
from .engine.nodes.dqc import _parse_dqc_sql
from .engine.nodes.review import _lint_sql_issues, _trial_run_issues

# 评分卡检查项名称
ARTIFACT_CHECK = "产物完整"
CONTRACT_CHECK = "结构契约"
KEYWORD_CHECK = "关键内容"
LINT_CHECK = "规范 linter"
DQC_CHECK = "DQC 用例"
TRIAL_CHECK = "真实试跑"
ERROR_CHECK = "管道错误"

# dqc.py 部分类别生成失败时的降级标记（出现即视为无效产出）
DQC_DEGRADED_MARKER = "[DQC降级]"


@dataclass
class EvalCase:
    """一个评估用例：需求文档 + 期望产物清单（三级断言的最小集）。

    不做全文 golden diff——对 LLM 输出太脆（措辞变化即误报），
    复用 P0-2「契约锚定模板而非金样本」的结论。
    """

    name: str
    requirement: str  # 相对 manifest.json 所在目录
    required_artifacts: list[str]
    scenario: str = "greenfield"  # greenfield（全新）| iterative（迭代）
    required_keywords: dict[str, list[str]] = field(default_factory=dict)
    min_dqc_cases: int = 10


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""


@dataclass
class CaseScore:
    case: EvalCase
    checks: list[CheckResult]
    errors: list[str]
    fix_iterations: int = 0

    @property
    def passed(self) -> bool:
        # skip 不算失败：无数据平台环境（CI/本地）试跑天然跳过
        return all(c.status != "fail" for c in self.checks)


def _trial_enabled() -> bool:
    """数据平台试跑是否启用。

    沿用 P1-2 的严格判断口径：``execution_enabled is not True``
    （缺配置/False/None）一律视为未启用，评估记 skip 而非 fail。
    """
    from .config.settings import get_settings

    return get_settings().execution_enabled is True


def load_manifest(path: Path | str) -> list[EvalCase]:
    """解析评估清单 manifest.json，返回用例列表。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"评估清单不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EvalCase(**case) for case in data["cases"]]


def score_case(case: EvalCase, output_dir: Path | str, state: dict) -> CaseScore:
    """对单个用例的产物目录 + 管道终态打分（密封：无 LLM、无平台副作用）。"""
    output_dir = Path(output_dir)
    errors = list(state.get("errors") or [])
    fix_iterations = int(state.get("fix_iterations") or 0)
    checks: list[CheckResult] = []

    # 1. 产物完整
    missing = [a for a in case.required_artifacts if not (output_dir / a).exists()]
    checks.append(
        CheckResult(
            ARTIFACT_CHECK,
            "fail" if missing else "pass",
            "缺失: " + ", ".join(missing)
            if missing
            else f"{len(case.required_artifacts)} 个产物齐全",
        )
    )

    # 2. 结构契约（只查在场的文档；缺文件已由产物完整扣分）
    contract_issues: list[str] = []
    checked = 0
    for name in case.required_artifacts:
        path = output_dir / name
        if not path.exists():
            continue
        missing_secs = validate_structure(name, path.read_text(encoding="utf-8"))
        checked += 1
        if missing_secs:
            contract_issues.append(f"{name} 缺 {', '.join(missing_secs)}")
    checks.append(
        CheckResult(
            CONTRACT_CHECK,
            "fail" if contract_issues else "pass",
            "; ".join(contract_issues) if contract_issues else f"{checked} 个文档契约通过",
        )
    )

    # 3. 关键内容（口径锚点：指标名、表名等必须出现）
    keyword_issues: list[str] = []
    for filename, keywords in case.required_keywords.items():
        path = output_dir / filename
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        absent = [k for k in keywords if k not in content]
        if absent:
            keyword_issues.append(f"{filename} 缺 {', '.join(absent)}")
    checks.append(
        CheckResult(
            KEYWORD_CHECK,
            "fail" if keyword_issues else "pass",
            "; ".join(keyword_issues) if keyword_issues else "关键词全部命中",
        )
    )

    # 4. 规范 linter（复用 P0-1，Critical 一票否决，Warning 只记录）
    sql_files = sorted(output_dir.glob("Phase4-*.sql"))
    if not sql_files:
        checks.append(CheckResult(LINT_CHECK, "skip", "未找到 Phase4 SQL"))
    else:
        issues = []
        for path in sql_files:
            issues.extend(_lint_sql_issues(path.read_text(encoding="utf-8")))
        critical = [i for i in issues if i["severity"] == "Critical"]
        warnings = [i for i in issues if i["severity"] == "Warning"]
        if critical:
            detail = "; ".join(i["message"] for i in critical[:3])
            checks.append(
                CheckResult(
                    LINT_CHECK,
                    "fail",
                    f"{len(critical)} Critical / {len(warnings)} Warning: {detail}",
                )
            )
        else:
            checks.append(CheckResult(LINT_CHECK, "pass", f"0 Critical / {len(warnings)} Warning"))

    # 5. DQC 用例（复用 P0-3 解析器；降级标记 = 无效产出直接 fail）
    dqc_path = output_dir / "Phase5-数据质量测试.sql"
    if not dqc_path.exists():
        checks.append(CheckResult(DQC_CHECK, "skip", "文件缺失（见产物完整）"))
    else:
        content = dqc_path.read_text(encoding="utf-8")
        if DQC_DEGRADED_MARKER in content:
            checks.append(
                CheckResult(DQC_CHECK, "fail", "存在 [DQC降级] 标记——降级产出不计有效用例")
            )
        else:
            n_cases = len(_parse_dqc_sql(content))
            if n_cases < case.min_dqc_cases:
                checks.append(
                    CheckResult(
                        DQC_CHECK, "fail", f"有效用例 {n_cases} < 要求 {case.min_dqc_cases}"
                    )
                )
            else:
                checks.append(
                    CheckResult(DQC_CHECK, "pass", f"有效用例 {n_cases} >= {case.min_dqc_cases}")
                )

    # 6. 真实试跑（复用 P1-2；无平台环境跳过）
    if not _trial_enabled():
        checks.append(
            CheckResult(TRIAL_CHECK, "skip", "数据平台未启用（execution_enabled 非 True）")
        )
    else:
        issues = _trial_run_issues(state)
        if issues:
            checks.append(
                CheckResult(TRIAL_CHECK, "fail", "; ".join(i["message"] for i in issues[:3]))
            )
        else:
            checks.append(CheckResult(TRIAL_CHECK, "pass", "试跑通过"))

    # 7. 管道错误（含修复循环收敛后的遗留 errors）
    checks.append(
        CheckResult(
            ERROR_CHECK,
            "fail" if errors else "pass",
            "; ".join(errors[:3]) if errors else "无错误",
        )
    )

    return CaseScore(case=case, checks=checks, errors=errors, fix_iterations=fix_iterations)


def render_scorecard(scores: list[CaseScore], date: str) -> str:
    """渲染 Markdown 评分卡（汇总表 + 每用例明细）。"""
    passed = sum(1 for s in scores if s.passed)
    lines = [
        "# Aqueduct 评估报告",
        "",
        f"- 日期：{date}",
        f"- 结果：通过 {passed}/{len(scores)}",
        "",
        "| 用例 | 场景 | 结果 | 修复轮数 | 失败项 |",
        "|---|---|---|---|---|",
    ]
    for s in scores:
        failed = [c.name for c in s.checks if c.status == "fail"]
        lines.append(
            f"| {s.case.name} | {s.case.scenario} | {'PASS' if s.passed else 'FAIL'} "
            f"| {s.fix_iterations} | {', '.join(failed) or '—'} |"
        )
    lines += ["", "## 明细", ""]
    for s in scores:
        lines += [
            f"### {s.case.name}（{s.case.scenario}）",
            "",
            "| 检查 | 结果 | 说明 |",
            "|---|---|---|",
        ]
        for c in s.checks:
            lines.append(f"| {c.name} | {c.status} | {c.detail} |")
        lines.append("")
    return "\n".join(lines)


def _default_pipeline(case: EvalCase, manifest_dir: Path, output_dir: Path) -> dict:
    """真实 dev 管道（唯一非密封路径）。返回管道终态 dict。"""
    from .core import Aqueduct

    requirement = (manifest_dir / case.requirement).resolve()
    result = Aqueduct().dev(requirement=str(requirement), output_dir=str(output_dir))
    return result.state


def run_evals(
    manifest_path: Path | str,
    runs_dir: Path | str,
    pipeline: Callable[[EvalCase, Path], dict] | None = None,
    case_filter: str | None = None,
) -> list[CaseScore]:
    """评估编排：逐用例跑管道 → 产物落 runs 目录 → 打分。

    Args:
        manifest_path: manifest.json 路径。
        runs_dir: 评估产物根目录（每用例一个子目录）。用 evals/runs/，
            不要指向 output/——评估产物不是交付物。
        pipeline: 管道函数 (case, output_dir) -> state dict；
            传 None 用真实 ``Aqueduct().dev``（测试注入假管道密封）。
        case_filter: 只跑名称含该子串的用例。

    Returns:
        每用例的 CaseScore，顺序与 manifest 一致。
    """
    cases = load_manifest(manifest_path)
    if case_filter:
        cases = [c for c in cases if case_filter in c.name]
    runs_dir = Path(runs_dir)
    manifest_dir = Path(manifest_path).parent

    scores = []
    for case in cases:
        output_dir = runs_dir / case.name
        output_dir.mkdir(parents=True, exist_ok=True)
        state = (
            _default_pipeline(case, manifest_dir, output_dir)
            if pipeline is None
            else pipeline(case, output_dir)
        )
        scores.append(score_case(case, output_dir, state))
    return scores
