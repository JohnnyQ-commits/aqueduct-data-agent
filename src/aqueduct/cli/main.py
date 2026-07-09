"""CLI 入口 — 基于 argparse 的命令行界面。

提供 aqueduct CLI 命令，支持开发/审查双模式工作流执行。
零外部依赖，仅使用 Python 标准库 argparse。

Windows GBK 编码兼容：强制 UTF-8 stdout/stderr，所有输出使用 ASCII 符号。

用法:
    aqueduct dev req.md
    aqueduct review online.sql changed.sql
    aqueduct validate sql_file.sql --strict
    aqueduct status
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path

# Windows GBK 编码兼容：强制 UTF-8 输出
# 注意：在 pytest 环境下跳过，避免干扰测试捕获
if sys.platform == "win32" and not getattr(sys, "pytest_running", False):
    # 检查是否在 pytest 环境下
    import os

    if "PYTEST_CURRENT_TEST" not in os.environ:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 导入 Skills 和 Tools 以触发 @register_skill / @register_tool 装饰器注册
from .. import (
    skills,  # noqa: F401
    tools,  # noqa: F401
)
from ..config.settings import get_settings
from ..core import Aqueduct, AqueductResult
from ..engine.state import WorkflowState
from ..engine.workflow import build_review_workflow
from ..exceptions import AqueductError

logger = logging.getLogger(__name__)


def _make_progress_callback() -> Callable:
    """创建进度回调函数，用于实时打印阶段信息。"""

    def on_progress(phase_name: str, idx: int, total: int, state: WorkflowState) -> None:
        print(f"  [RUNNING] Phase {idx}/{total}: {phase_name}", flush=True)

    return on_progress


def _parse_questions(summary: str) -> list[str]:
    """从需求理解摘要中提取待确认问题列表。"""
    if "### 用户澄清" in summary:
        return []
    m = re.search(r"###\s*待确认问题\s*\n(.*?)(?=\n###|\Z)", summary, re.DOTALL)
    if not m:
        return []
    return [q.strip() for q in re.findall(r"\d+\.\s*(.+)", m.group(1)) if q.strip()]


def _collect_qa(questions: list[str]) -> list[dict]:
    """逐条展示问题，收集用户回答。"""
    print(f"\n--- 待确认问题 ({len(questions)} 条) ---", flush=True)
    qa_pairs = []
    for i, q in enumerate(questions, 1):
        print(f"\nQ{i}: {q}", flush=True)
        try:
            answer = input(f"A{i}: ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        qa_pairs.append({"question": q, "answer": answer or "(未回答)"})
    return qa_pairs


def _format_qa_section(qa_pairs: list[dict]) -> str:
    """将 Q&A 列表格式化为 '### 用户澄清记录' Markdown 段落。

    文件写入和 state 更新共用此函数，确保格式一致。
    """
    lines = ["\n\n---\n\n### 用户澄清记录\n"]
    for i, qa in enumerate(qa_pairs, 1):
        lines.append(f"**Q{i}**: {qa['question']}")
        lines.append(f"**A{i}**: {qa['answer']}\n")
    return "\n".join(lines)


def _append_qa_to_file(state: WorkflowState, qa_pairs: list[dict]) -> None:
    """将 Q&A 记录追加到 Phase1-需求理解摘要.md。"""
    from ..engine.nodes.helpers import get_output_dir

    filepath = get_output_dir(state) / "Phase1-需求理解摘要.md"
    if not filepath.exists():
        return

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(_format_qa_section(qa_pairs))

    print(f"\n[OK] 已追加 {len(qa_pairs)} 条澄清记录到 Phase1-需求理解摘要.md", flush=True)


def _make_confirm_callback() -> Callable:
    """创建确认回调函数，用于 Phase 1 后的用户交互确认。"""

    def on_confirm(state: WorkflowState) -> bool:
        summary = state.get("requirement_summary", "")
        if not summary:
            return True

        print(f"\n{'=' * 60}", flush=True)
        print("[Phase 1 Complete] 需求理解摘要:", flush=True)
        print(f"{'=' * 60}", flush=True)
        print(summary, flush=True)
        print(f"{'=' * 60}", flush=True)

        # 检测非交互环境（如 Claude Code 子进程、CI/CD 管道等）
        if not sys.stdin.isatty():
            print(
                "\n[WARNING] 非交互环境（stdin 不是 TTY），无法等待用户确认。",
                flush=True,
            )
            print(
                "工作流已暂停。如需跳过确认，请使用 Python API: "
                "Aqueduct().dev(..., interactive=False)",
                flush=True,
            )
            return False

        # --- Q&A 收集环节 ---
        questions = _parse_questions(summary)
        if questions:
            print(f"\n是否要逐个回答以上 {len(questions)} 个待确认问题？(Y/n): ", flush=True)
            try:
                want_qa = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                want_qa = "n"

            if want_qa in ("", "y", "yes"):
                qa_pairs = _collect_qa(questions)
                _append_qa_to_file(state, qa_pairs)

                # 更新 state 中的摘要，供后续 Phase 使用
                state["requirement_summary"] = summary.rstrip() + _format_qa_section(qa_pairs)

        # --- 确认环节 ---
        print("\n请确认以上内容是否正确？", flush=True)
        print("  [Y] 确认，继续后续阶段", flush=True)
        print("  [N] 停止，需要修改需求文档", flush=True)
        print("  [Q] 退出", flush=True)
        try:
            choice = input("\nYour choice (y/N/q): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"  # Ctrl+C / Ctrl+D 默认停止（安全优先）

        if choice in ("y", "yes"):
            return True
        elif choice in ("q",):
            print("\n[INFO] Exit.", flush=True)
            return False
        else:
            print(
                "\n[INFO] Stopping workflow. Please update requirement doc.",
                flush=True,
            )
            return False

    return on_confirm


def _print_result(result: AqueductResult) -> None:
    """打印工作流执行结果。"""
    if result.success:
        print(
            f"\n[OK] Workflow completed, {len(result.artifacts)} artifact(s):",
            flush=True,
        )
        for artifact in result.artifacts:
            print(f"  [FILE] {artifact}", flush=True)
    elif result.halted:
        print("\n[WARN] Workflow halted.", flush=True)
        _print_partial_result(result)
    else:
        _print_partial_result(result)


def _print_partial_result(result: AqueductResult) -> None:
    """打印中途终止/失败时的部分产出物和错误。"""
    if result.artifacts:
        print(f"\n已生成 {len(result.artifacts)} 个产出物:", flush=True)
        for a in result.artifacts:
            print(f"  [FILE] {a}", flush=True)
    if result.errors:
        print(f"\n错误记录 ({len(result.errors)}):", flush=True)
        for e in result.errors:
            print(f"  - {e}", flush=True)


def _dev_mode(args: argparse.Namespace) -> int:
    """开发模式：从需求文档到完整交付。"""
    req_path = Path(args.requirement)
    if not req_path.exists():
        print(f"[ERROR] Requirement file not found: {args.requirement}")
        return 1

    print(f"[INFO] Reading requirement: {args.requirement}", flush=True)
    print("[INFO] Starting development mode workflow...", flush=True)

    # 加载外部插件（skill / tool）
    if args.skill_dir:
        from ..skills.registry import load_plugins

        try:
            new_skills = load_plugins(args.skill_dir)
            print(f"[INFO] Loaded {len(new_skills)} external skill(s): {new_skills}", flush=True)
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", flush=True)
            return 1

    if args.tool_dir:
        from ..tools.registry import load_plugins

        try:
            new_tools = load_plugins(args.tool_dir)
            print(f"[INFO] Loaded {len(new_tools)} external tool(s): {new_tools}", flush=True)
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", flush=True)
            return 1

    # 外部 SQL 文件路径
    external_sql_path = getattr(args, "sql_file", None)
    if external_sql_path:
        sql_path = Path(external_sql_path)
        if not sql_path.exists():
            print(f"[ERROR] SQL file not found: {external_sql_path}")
            return 1
        print(f"[INFO] Using external SQL: {external_sql_path}", flush=True)

    agent = Aqueduct()
    result = agent.dev(
        args.requirement,
        output_dir=args.output,
        interactive=True,
        on_confirm=_make_confirm_callback(),
        on_progress=_make_progress_callback(),
        external_sql_path=external_sql_path,
    )

    _print_result(result)
    return 0 if result.success else 1


def _review_mode(args: argparse.Namespace) -> int:
    """审查模式：验证 SQL 变更的正确性。"""
    online_path = Path(args.online_sql)
    changed_path = Path(args.changed_sql)

    if not online_path.exists():
        print(f"[ERROR] Online version not found: {args.online_sql}")
        return 1
    if not changed_path.exists():
        print(f"[ERROR] Changed version not found: {args.changed_sql}")
        return 1

    print(f"[INFO] Online version: {args.online_sql}")
    print(f"[INFO] Changed version: {args.changed_sql}")

    # 构建工作流状态
    state: WorkflowState = {
        "requirement": args.desc or "",
        "mode": "review",
        "online_sql": online_path.read_text(encoding="utf-8"),
        "changed_sql": changed_path.read_text(encoding="utf-8"),
        "errors": [],
        "artifacts": [],
    }

    print("[INFO] Starting review mode workflow...")

    try:
        workflow = build_review_workflow()
        final_state = workflow.invoke(state)

        if final_state.get("errors"):
            print(f"\n[WARN] Review completed with {len(final_state['errors'])} error(s):")
            for err in final_state["errors"]:
                print(f"  - {err}")
            return 1

        print("\n[OK] Review mode workflow completed")
        return 0

    except AqueductError as e:
        print(f"\n[ERROR] Review failed: {e}")
        return 1


def _change_mode(args: argparse.Namespace) -> int:
    """变更管理模式：管理需求交付后的变更。"""
    original_path = Path(args.original)
    new_path = Path(args.new)

    if not original_path.exists():
        print(f"[ERROR] Original requirement not found: {args.original}")
        return 1
    if not new_path.exists():
        print(f"[ERROR] New requirement not found: {args.new}")
        return 1

    print(f"[INFO] Original requirement: {args.original}")
    print(f"[INFO] New requirement: {args.new}")
    if args.desc:
        print(f"[INFO] Change description: {args.desc}")

    print("[INFO] Starting change management workflow...")

    agent = Aqueduct()
    result = agent.change(
        args.original,
        args.new,
        desc=args.desc or "",
        output_dir=args.output,
        on_progress=_make_progress_callback(),
    )

    if result.success:
        cr_number = result.state.get("cr_number", "")
        cr_dir = result.state.get("cr_dir", "")
        print("\n[OK] Change management workflow completed", flush=True)
        print(f"  [CR] CR-{cr_number}", flush=True)
        if cr_dir:
            print(f"  [DIR] {cr_dir}", flush=True)
        return 0

    _print_result(result)
    return 1


def _validate_sql(args: argparse.Namespace) -> int:
    """校验 SQL 文件的规范性。"""
    from ..tools.registry import get_tool

    path = Path(args.sql_file)
    if not path.exists():
        print(f"[ERROR] File not found: {args.sql_file}")
        return 1

    tool = get_tool("validator")
    result = tool.execute(sql_file=str(path), strict=args.strict)

    if result.success:
        print(f"[OK] {path.name} validation passed")
        return 0
    else:
        print(f"[ERROR] {path.name} validation failed: {result.error}")
        return 1


def _status(args: argparse.Namespace) -> int:
    """项目状态概览。"""
    settings = get_settings()
    root = settings.project_root

    # 统计各层模块
    tools_dir = root / "src/aqueduct/tools"
    tools_count = len(list(tools_dir.glob("*.py"))) - 1 if tools_dir.exists() else 0

    skills_dir = root / "src/aqueduct/skills"
    skills_count = len(list(skills_dir.glob("*.py"))) - 1 if skills_dir.exists() else 0

    # 统计测试
    tests_count = len(list((root / "tests").glob("test_*.py")))

    # 统计文档
    docs_count = sum(
        1
        for d in root.glob("**/*.md")
        if ".git" not in str(d) and ".venv" not in str(d) and "egg-info" not in str(d)
    )

    # 统计知识库（支持域目录模式和扁平模式）
    domains_dir = root / "knowledge/domains"
    if domains_dir.exists():
        nested = list(domains_dir.glob("*/domain.json"))
        flat = list(domains_dir.glob("*.json")) if not nested else []
        domains_count = len(nested) + len(flat)
    else:
        domains_count = 0

    print("=== Aqueduct Project Status ===")
    print(f"  Tools (tools/):                {tools_count}")
    print(f"  Skills (skills/):              {skills_count}")
    print(f"  Test files:                    {tests_count}")
    print(f"  Documentation (.md):           {docs_count}")
    print(f"  Domain models:                 {domains_count}")

    return 0


def create_parser() -> argparse.ArgumentParser:
    """创建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="aqueduct",
        description="Data Engineering Automation Agent -- From requirement to deployment",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # dev 命令
    dev_parser = subparsers.add_parser(
        "dev",
        help="Development mode: from requirement to full delivery",
    )
    dev_parser.add_argument("requirement", help="Requirement document path (.md file)")
    dev_parser.add_argument("--output", "-o", help="Output directory")
    dev_parser.add_argument(
        "--sql-file",
        help="External SQL file path (skip LLM SQL generation in Phase 4)",
    )
    dev_parser.add_argument(
        "--skill-dir",
        help="External skill plugin directory path",
    )
    dev_parser.add_argument(
        "--tool-dir",
        help="External tool plugin directory path",
    )

    # review 命令
    review_parser = subparsers.add_parser(
        "review",
        help="Review mode: validate SQL changes",
    )
    review_parser.add_argument("online_sql", help="Online version SQL path")
    review_parser.add_argument("changed_sql", help="Changed version SQL path")
    review_parser.add_argument("--desc", "-d", help="Requirement description")

    # validate 命令
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate SQL file规范性",
    )
    validate_parser.add_argument("sql_file", help="SQL file path")
    validate_parser.add_argument("--strict", "-s", action="store_true", help="Enable strict mode")

    # status 命令
    subparsers.add_parser(
        "status",
        help="Project status overview",
    )

    # change 命令 — 变更管理
    change_parser = subparsers.add_parser(
        "change",
        help="Change management: manage post-delivery requirement changes",
    )
    change_parser.add_argument(
        "original", help="Original requirement document path (before change)"
    )
    change_parser.add_argument("new", help="New requirement document path (after change)")
    change_parser.add_argument("--desc", "-d", help="Change description / summary")
    change_parser.add_argument("--output", "-o", help="Output directory")

    return parser


def main() -> int:
    """CLI 入口点（pyproject.toml 入口）。"""
    parser = create_parser()
    args = parser.parse_args()

    # 初始化日志系统
    from ..utils.logging_config import setup_logging

    setup_logging(level="DEBUG" if args.verbose else "INFO")

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "dev": _dev_mode,
        "review": _review_mode,
        "change": _change_mode,
        "validate": _validate_sql,
        "status": _status,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
