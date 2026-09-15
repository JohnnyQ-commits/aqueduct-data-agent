#!/usr/bin/env python
"""Pre-commit 敏感门禁（路径 + 内容双层） — 事前拒绝优于事后 filter-repo。

背景：本仓库有两次真实的 filter-repo 历史重写——第一次是内部文档/真实表名/
人员任务域目录混入提交；第二次（2026-09-15）是已跟踪文件的内容泄露：
公司名/真实域名/员工ID 随 bdp_manifest.json 与测试 fixtures 推到公开远端，
事后重写 197 个提交 + 双远端 force push。路径门禁拦不住内容，故补内容层。

两层门禁：
1. 路径层：敏感目录/文件/名称片段（与 .gitignore 同源维护）
2. 内容层：扫 git diff --cached 的新增行（+ 行）是否命中敏感词。敏感词
   清单放本地不入库文件 sensitive_content.local.txt（公开仓库本身不能
   包含敏感词，连门禁配置也不行；清单缺失=公开用户形态，内容层静默放行）。

用法（.githooks/pre-commit 管道调用，也可手工校验）：
    git diff --cached --name-only -z | tr '\\0' '\\n' | python scripts/hooks/check_staged_paths.py --stdin
    python scripts/hooks/check_staged_paths.py <path> [<path> ...]
    python scripts/hooks/check_staged_paths.py --content [--patterns <清单文件>]

退出码：0=放行，1=存在敏感路径或敏感内容（拒绝提交）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# 敏感目录（目录前缀匹配，路径以 / 结尾形式）
SENSITIVE_DIRS: list[str] = [
    "internal/",  # 内部文档 + 动态知识（真实表名/业务知识）
    "docs/",  # 内部文档
    "output/",  # 管道交付物（真实表名与业务口径）
    "logs/",  # 运行日志
    "workspace/",  # 工作区文件
    "evals/runs/",  # 评估运行产物（真实交付物副本）
    ".cache/",  # 运行时缓存（真实表结构）
    ".deprecated/",  # 废弃迁移残留
    ".understand-anything/",  # 工具生成目录
]

# 敏感文件（根目录全路径精确 或 文件名精确——防 src/foo/.env 之类漏网）
SENSITIVE_FILES: list[str] = [
    ".env",  # 环境凭证
    ".env.local",  # 环境凭证
    ".mcp.json",  # MCP 配置（平台凭证）
    ".claude/settings.local.json",  # 本地权限配置
    ".pipeline_manifest.json",  # 管道运行时状态（含需求内容）
    "knowledge/phase1-clarification-recording.md",  # 运行时生成的澄清记录
    "scripts/hooks/sensitive_content.local.txt",  # 内容层敏感词清单（本身含敏感词，永不入库）
]

# 内容层敏感词清单默认位置（本地文件，gitignore + 路径门禁双保险）
DEFAULT_PATTERNS_PATH = Path(__file__).with_name("sensitive_content.local.txt")

# 敏感名称片段（子串匹配——历史真实泄露源：人员任务域目录名）
SENSITIVE_NAME_PARTS: list[str] = [
    "人员",
]

# 扁平清单（覆盖度自查用）
SENSITIVE_PATTERNS: list[str] = SENSITIVE_DIRS + SENSITIVE_FILES + SENSITIVE_NAME_PARTS


def find_violations(paths: list[str]) -> list[tuple[str, str]]:
    """返回 [(staged_path, 原因), ...]（保持入参顺序，仅含违规项）。"""
    violations: list[tuple[str, str]] = []
    for raw in paths:
        p = raw.replace("\\", "/")
        # 只剥一层字面前缀 "./"——lstrip("./") 是字符集剥离，会把 ".env" 剥成 "env"
        if p.startswith("./"):
            p = p[2:]
        reason = _match(p)
        if reason:
            violations.append((raw, reason))
    return violations


def _match(p: str) -> str | None:
    for d in SENSITIVE_DIRS:
        prefix = d[:-1]
        if p == prefix or p.startswith(prefix + "/"):
            return f"敏感目录 {d}（内部内容不入库，见 .gitignore 同源规则）"
    name = p.rsplit("/", 1)[-1]
    for f in SENSITIVE_FILES:
        if p == f or name == f.rsplit("/", 1)[-1]:
            return f"敏感文件 {f}（凭证/本地配置/运行时状态不入库）"
    for part in SENSITIVE_NAME_PARTS:
        if part in p:
            return f"路径含敏感名称片段 {part!r}（内部人员/任务域，曾致历史重写）"
    return None


# ============ 内容层：staged 新增行敏感词扫描 ============

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")


def parse_patterns(text: str) -> list[str]:
    """清单格式：每行一个敏感词；'#' 注释行与空行忽略；首尾空白剥离。"""
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def load_content_patterns(path: Path) -> list[str]:
    """清单文件缺失（公开仓库默认形态）→ 空清单，内容层静默放行。"""
    if not Path(path).is_file():
        return []
    return parse_patterns(Path(path).read_text(encoding="utf-8"))


def _added_lines_by_file(diff_text: str) -> list[tuple[str, str]]:
    """解析 diff，返回 [(文件路径, 新增行内容), ...]。

    只取 + 新增行（上下文/删除行是已入库内容，由历史重写处理，钩子只拦
    "正在引入"）；diff 元数据行（+++ 头等）不算新增。
    """
    result: list[tuple[str, str]] = []
    current_file = ""
    for line in diff_text.splitlines():
        m = _DIFF_GIT_RE.match(line)
        if m:
            current_file = m.group(2)
            continue
        if line.startswith("+++") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            result.append((current_file, line[1:]))
    return result


def find_content_violations(diff_text: str, patterns: list[str]) -> list[tuple[str, str]]:
    """返回 [(文件路径, 原因), ...]，每个文件只报首个命中的敏感词。"""
    if not patterns:
        return []
    lowered = [(term, term.lower()) for term in patterns]
    first_hit: dict[str, str] = {}
    for path, added in _added_lines_by_file(diff_text):
        low = added.lower()
        for term, term_low in lowered:
            if term_low in low:
                first_hit.setdefault(path, term)
                break
    return [
        (path, f"命中敏感词 {term!r}（内容门禁——该词不得入库，见 sensitive_content.local.txt）")
        for path, term in first_hit.items()
    ]


def _report(violations: list[tuple[str, str]], headline: str) -> int:
    print(headline, file=sys.stderr)
    for path, reason in violations:
        print(f"  ✗ {path}", file=sys.stderr)
        print(f"      {reason}", file=sys.stderr)
    print("  确需提交请先脱敏；规则清单见 scripts/hooks/check_staged_paths.py", file=sys.stderr)
    return 1


def _run_content_mode(argv: list[str]) -> int:
    """内容层：扫 git diff --cached 新增行。清单缺失 → 放行（公开仓库默认形态）。"""
    if "--patterns" in argv:
        patterns_path = Path(argv[argv.index("--patterns") + 1])
    else:
        patterns_path = DEFAULT_PATTERNS_PATH
    patterns = load_content_patterns(patterns_path)
    if not patterns:
        return 0
    diff = subprocess.run(
        ["git", "diff", "--cached", "--no-color", "--no-ext-diff"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    violations = find_content_violations(diff, patterns)
    if not violations:
        return 0
    return _report(
        violations,
        "pre-commit 敏感内容门禁：staged 新增内容命中敏感词（事前拒绝优于事后 filter-repo）",
    )


def main(argv: list[str]) -> int:
    # Windows 管道下子进程默认 GBK 编码，父进程按 utf-8 解码会炸——统一 utf-8
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    if "--content" in argv:
        return _run_content_mode(argv)

    args = [a for a in argv[1:] if a != "--stdin"]
    paths = (
        [line for line in sys.stdin.read().splitlines() if line.strip()]
        if "--stdin" in argv
        else args
    )

    violations = find_violations(paths)
    if not violations:
        return 0

    return _report(
        violations, "pre-commit 敏感文件门禁：以下文件禁止提交（事前拒绝优于事后 filter-repo）"
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
