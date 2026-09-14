#!/usr/bin/env python
"""Pre-commit 敏感文件门禁 — 事前拒绝优于事后 filter-repo。

背景：本仓库有真实的 filter-repo 历史重写（内部文档/真实表名/人员任务域
曾混入提交推到远程，事后重写+force push 成本极高）。.gitignore 只防
"未跟踪文件被误 add"，防不住 force-add 与已跟踪文件的误移动——钩子是
最后一道闸。

用法（.githooks/pre-commit 管道调用，也可手工校验）：
    git diff --cached --name-only -z | tr '\\0' '\\n' | python scripts/hooks/check_staged_paths.py --stdin
    python scripts/hooks/check_staged_paths.py <path> [<path> ...]

退出码：0=放行，1=存在敏感路径（拒绝提交）。
模式与 .gitignore 同源维护——改 .gitignore 敏感面时同步改这里。
"""

from __future__ import annotations

import sys

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
]

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


def main(argv: list[str]) -> int:
    # Windows 管道下子进程默认 GBK 编码，父进程按 utf-8 解码会炸——统一 utf-8
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    args = [a for a in argv[1:] if a != "--stdin"]
    paths = (
        [line for line in sys.stdin.read().splitlines() if line.strip()]
        if "--stdin" in argv
        else args
    )

    violations = find_violations(paths)
    if not violations:
        return 0

    print(
        "pre-commit 敏感文件门禁：以下文件禁止提交（事前拒绝优于事后 filter-repo）",
        file=sys.stderr,
    )
    for path, reason in violations:
        print(f"  ✗ {path}", file=sys.stderr)
        print(f"      {reason}", file=sys.stderr)
    print("  确需提交请先脱敏；规则清单见 scripts/hooks/check_staged_paths.py", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
