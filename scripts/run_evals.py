"""Agent 测试阶段 1 评估入口：evals 数据集 → dev 管道 → 门禁评分卡。

用法：
    python scripts/run_evals.py                     # 全量用例（发版前人工触发）
    python scripts/run_evals.py --case iterative    # 按名称子串过滤
    python scripts/run_evals.py --out evals/runs    # 产物根目录（默认）

定位：模板变更守门 + 定期回归，永不进 per-commit CI（单次评估 =
1 次真实 LLM 跑，43-96 分钟）。评分器复用 v0.6.0 门禁实现，
报告与产物写 evals/runs/（git 忽略）——评估产物是回归基准，不是交付物。
退出码：全部通过 0，任一失败 1。
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, "src")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from aqueduct.evals import render_scorecard, run_evals


def main() -> int:
    parser = argparse.ArgumentParser(description="Aqueduct 评估：evals 数据集 → dev 管道 → 评分卡")
    parser.add_argument("--case", help="只跑名称含该子串的用例")
    parser.add_argument("--out", default="evals/runs", help="评估产物根目录（默认 evals/runs）")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    manifest = repo_root / "evals" / "manifest.json"
    runs_dir = repo_root / args.out

    print(f"[evals] manifest={manifest} filter={args.case or '-'}")
    print("[evals] 开始逐用例跑 dev 管道（真实 LLM，单用例 43-96 分钟）...")
    scores = run_evals(manifest, runs_dir, case_filter=args.case)

    report = runs_dir / f"report-{date.today():%Y%m%d}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_scorecard(scores, date=f"{date.today():%Y-%m-%d}"), encoding="utf-8")

    passed = sum(1 for s in scores if s.passed)
    print(f"\n[evals] 通过 {passed}/{len(scores)}，评分卡: {report}")
    for s in scores:
        failed = ", ".join(c.name for c in s.checks if c.status == "fail") or "—"
        print(
            f"  {'PASS' if s.passed else 'FAIL'}  {s.case.name}（{s.case.scenario}）"
            f"修复轮数={s.fix_iterations} 失败项={failed}"
        )
    return 0 if passed == len(scores) else 1


if __name__ == "__main__":
    sys.exit(main())
