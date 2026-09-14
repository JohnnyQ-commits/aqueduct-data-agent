"""Pre-commit 敏感文件门禁测试。

背景：本仓库有真实的 filter-repo 重写历史——内部文档/真实表名/人员任务域
曾混入提交被推到远程，事后重写成本极高。门禁在 commit 时拦截敏感路径，
把"事后 filter-repo"变成"事前拒绝"。

布局：
- scripts/hooks/check_staged_paths.py — 纯函数 find_violations + CLI（--stdin）
- .githooks/pre-commit — git 钩子，git diff --cached --name-only 管道进 checker
- 激活：git config core.hooksPath .githooks（RELEASE.md 固化）

敏感模式与 .gitignore 同源（internal/、output/、.env 等）——gitignore 只防
"未跟踪文件误 add"，防不住 force-add（git add -f）与已跟踪文件的误移动，
钩子是最后一道闸。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "check_staged_paths.py"
_HOOK = Path(__file__).resolve().parent.parent / ".githooks" / "pre-commit"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_staged_paths", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============ 敏感模式清单 ============


class TestSensitivePatterns:
    def test_pattern_list_covers_gitignore_derived_set(self):
        """与 .gitignore 同源的核心敏感面：内部文档/运行时产物/凭证。"""
        mod = _load_checker()
        patterns = " ".join(mod.SENSITIVE_PATTERNS)
        for required in (
            "internal/",
            "docs/",
            "output/",
            "logs/",
            "workspace/",
            "evals/runs/",
            ".env",
            ".mcp.json",
            "人员",
        ):
            assert required in patterns, f"敏感模式缺失: {required}"


# ============ find_violations 纯函数 ============


class TestFindViolations:
    @pytest.mark.parametrize(
        "staged",
        [
            "internal/notes/design.md",
            "internal",
            "docs/内部平台架构.md",
            "output/Phase4-订单统计.sql",
            "logs/task.2026-09-11.log",
            "workspace/req.md",
            "evals/runs/scorecard-check2/report.md",
            ".env",
            ".env.local",
            ".mcp.json",
            "knowledge/domains/人员管理_新增电商应配未配及人岗不匹配任务__2/semantic-model.md",
        ],
    )
    def test_sensitive_paths_flagged(self, staged):
        mod = _load_checker()
        violations = mod.find_violations([staged])
        assert [v[0] for v in violations] == [staged]
        assert violations[0][1]  # 原因非空

    @pytest.mark.parametrize(
        "staged",
        [
            "src/aqueduct/core.py",
            "tests/test_platform_adapter.py",
            "README.md",
            "knowledge/domains/ecommerce_order/semantic-model.md",
            "src/internal_notes.md",  # 前缀混淆：internal 模式不咬 src/ 下文件
            "scripts/hooks/check_staged_paths.py",
        ],
    )
    def test_clean_paths_pass(self, staged):
        mod = _load_checker()
        assert mod.find_violations([staged]) == []

    def test_mixed_batch_reports_only_violations(self):
        mod = _load_checker()
        violations = mod.find_violations(["src/aqueduct/core.py", ".env", "README.md"])
        assert [v[0] for v in violations] == [".env"]

    def test_windows_separators_normalized(self):
        """Windows 传参带反斜杠也能拦（钩子管道里 git 始终给正斜杠，防手工调用漏网）。"""
        mod = _load_checker()
        violations = mod.find_violations(["internal\\notes\\design.md"])
        assert len(violations) == 1


# ============ CLI 契约 ============


class TestCli:
    def _run(self, args, stdin=None):
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=stdin,
            timeout=60,
        )

    def test_stdin_violation_exit_1(self):
        proc = self._run(["--stdin"], stdin="output/Phase4-x.sql\nsrc/aqueduct/core.py\n")
        assert proc.returncode == 1
        assert "output/Phase4-x.sql" in proc.stderr

    def test_stdin_clean_exit_0(self):
        proc = self._run(["--stdin"], stdin="src/aqueduct/core.py\nREADME.md\n")
        assert proc.returncode == 0

    def test_positional_args_violation_exit_1(self):
        proc = self._run([".env"])
        assert proc.returncode == 1
        assert ".env" in proc.stderr


# ============ 钩子脚本存在性与接线 ============


class TestHookWiring:
    def test_hook_script_exists_and_calls_checker(self):
        assert _HOOK.is_file()
        content = _HOOK.read_text(encoding="utf-8")
        assert "check_staged_paths.py" in content
        assert "git diff --cached --name-only" in content
