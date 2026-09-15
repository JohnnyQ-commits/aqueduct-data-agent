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


# ============ 内容级扫描（2026-09-15 脱敏事件加固） ============
# 事故：路径门禁拦不住已跟踪文件里的敏感内容（公司名/真实域名/员工ID 曾随
# bdp_manifest.json 与测试 fixtures 推到公开远端，事后 filter-repo 重写 197 提交）。
# 内容门禁设计：
# - 敏感词清单放本地不入库文件（scripts/hooks/sensitive_content.local.txt，
#   gitignore + 路径门禁双保险）——公开仓库本身不能包含敏感词，连门禁配置也不行
# - 扫 git diff --cached 的新增行（+ 行），上下文行/删除行不拦（已入库内容
#   由历史重写处理，钩子只拦"正在引入"）
# 本测试文件自身 tracked，敏感词一律运行时拼接，禁止字面量出现。

_COMPANY = "\u987a\u4e30"  # 公司名（unicode 转义拼接，防字面量入库）
_DOMAIN = "sf-" + "express.com"  # 真实域名（拼接）
_EMPID = "01444" + "576"  # 员工ID（拼接）


class TestParsePatterns:
    def test_parse_skips_comments_blanks_and_strips_whitespace(self):
        mod = _load_checker()
        text = "# 注释\n  \n term-a \nterm-b\n"
        assert mod.parse_patterns(text) == ["term-a", "term-b"]

    def test_missing_patterns_file_returns_empty(self, tmp_path):
        """公开仓库默认无本地清单——内容门禁静默放行，不报错。"""
        mod = _load_checker()
        assert mod.load_content_patterns(tmp_path / "nonexistent.txt") == []

    def test_patterns_file_loaded_utf8(self, tmp_path):
        mod = _load_checker()
        f = tmp_path / "terms.txt"
        f.write_text(f"# 本地敏感词\n{_COMPANY}\n{_DOMAIN}\n", encoding="utf-8")
        assert mod.load_content_patterns(f) == [_COMPANY, _DOMAIN]


def _diff_of(*added_lines: str, path: str = "src/foo.json") -> str:
    """构造最小合法 diff 文本（含头/上下文/删除/新增行）。"""
    lines = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"]
    lines.append("@@ -1,3 +1,4 @@")
    lines.append("context line")
    lines.extend("-" + old for old in ("old line one", "old line two"))
    lines.extend("+" + a for a in added_lines)
    return "\n".join(lines) + "\n"


class TestFindContentViolations:
    def test_added_line_with_sensitive_term_flagged(self):
        mod = _load_checker()
        diff = _diff_of(f'"description": "BDP {_COMPANY} 平台"')
        violations = mod.find_content_violations(diff, [_COMPANY])
        assert [v[0] for v in violations] == ["src/foo.json"]
        assert _COMPANY in violations[0][1]  # 原因里指认命中词

    def test_each_sensitive_term_class_caught(self):
        mod = _load_checker()
        for term in (_COMPANY, _DOMAIN, _EMPID):
            diff = _diff_of(f'config_value = "{term}"')
            assert mod.find_content_violations(diff, [term]), f"漏拦: {term!r}"

    def test_context_and_removed_lines_not_flagged(self):
        """已入库内容出现在上下文/删除行——钩子不拦（历史问题走重写）。"""
        mod = _load_checker()
        diff = _diff_of("clean new line")
        violations = mod.find_content_violations(diff, [_COMPANY])
        assert violations == []

    def test_added_line_clean_passes(self):
        mod = _load_checker()
        diff = _diff_of("BDP（大数据平台）能力声明")
        assert mod.find_content_violations(diff, [_COMPANY]) == []

    def test_diff_header_lines_never_flagged(self):
        """diff 元数据行（+++ b/... 等）不算新增内容。"""
        mod = _load_checker()
        diff = _diff_of("ok", path=f"src/{_COMPANY}.py")
        assert mod.find_content_violations(diff, [_COMPANY]) == []

    def test_multiple_files_each_reported(self):
        mod = _load_checker()
        diff = _diff_of(f"x = {_EMPID}", path="tests/a.py") + _diff_of(
            f"y = '{_DOMAIN}'", path="src/b.py"
        )
        violations = mod.find_content_violations(diff, [_EMPID, _DOMAIN])
        assert sorted(v[0] for v in violations) == ["src/b.py", "tests/a.py"]

    def test_case_insensitive_ascii_match(self):
        mod = _load_checker()
        diff = _diff_of("url = https://" + _DOMAIN.upper() + "/api")
        violations = mod.find_content_violations(diff, [_DOMAIN])
        assert len(violations) == 1


class TestContentCli:
    def _run(self, args, stdin=None, cwd=None):
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=stdin,
            timeout=60,
            cwd=cwd,
        )

    def test_content_mode_missing_patterns_file_exit_0(self):
        """--content 指向不存在的清单：公开仓库默认形态，放行。"""
        proc = self._run(["--content", "--patterns", "Z:/no/such/file.txt"])
        assert proc.returncode == 0

    def test_content_mode_blocks_staged_sensitive_content(self, tmp_path):
        """真实 git 仓库：staged 新增行含敏感词 → exit 1 且报告文件与命中词。"""
        import os

        repo = tmp_path / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}
        for cmd in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True, env=env)
        terms = tmp_path / "terms.txt"
        terms.write_text(_COMPANY, encoding="utf-8")
        (repo / "manifest.json").write_text(
            f'{{"description": "BDP {_COMPANY} 平台"}}', encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "manifest.json"], cwd=repo, check=True, capture_output=True, env=env
        )
        proc = self._run(["--content", "--patterns", str(terms)], cwd=repo)
        assert proc.returncode == 1
        assert "manifest.json" in proc.stderr
        assert _COMPANY in proc.stderr

    def test_content_mode_clean_staged_exit_0(self, tmp_path):
        import os

        repo = tmp_path / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}
        for cmd in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True, env=env)
        terms = tmp_path / "terms.txt"
        terms.write_text(_COMPANY, encoding="utf-8")
        (repo / "readme.md").write_text("# clean repo\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "readme.md"], cwd=repo, check=True, capture_output=True, env=env
        )
        proc = self._run(["--content", "--patterns", str(terms)], cwd=repo)
        assert proc.returncode == 0


# ============ 钩子脚本存在性与接线 ============


class TestHookWiring:
    def test_hook_script_exists_and_calls_checker(self):
        assert _HOOK.is_file()
        content = _HOOK.read_text(encoding="utf-8")
        assert "check_staged_paths.py" in content
        assert "git diff --cached --name-only" in content

    def test_hook_wires_content_gate(self):
        """钩子除路径门外还须接内容门禁（--content）。"""
        content = _HOOK.read_text(encoding="utf-8")
        assert "--content" in content

    def test_patterns_file_gitignored_and_path_gated(self):
        """清单文件双保险：gitignore 挡 add + 路径门禁挡 force-add。"""
        repo_root = _SCRIPT.parent.parent.parent
        gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
        assert "sensitive_content.local.txt" in gitignore
        mod = _load_checker()
        assert any("sensitive_content.local.txt" in f for f in mod.SENSITIVE_FILES)
