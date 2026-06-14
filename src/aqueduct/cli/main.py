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
import sys
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
from ..engine.state import WorkflowState
from ..engine.workflow import build_review_workflow
from ..exceptions import AqueductError

logger = logging.getLogger(__name__)


def _dev_mode(args: argparse.Namespace) -> int:
    """开发模式：从需求文档到完整交付。"""
    req_path = Path(args.requirement)
    if not req_path.exists():
        print(f"[ERROR] Requirement file not found: {args.requirement}")
        return 1

    print(f"[INFO] Reading requirement: {args.requirement}", flush=True)
    requirement_text = req_path.read_text(encoding="utf-8")

    # 构建工作流状态
    state: WorkflowState = {
        "requirement": requirement_text,
        "mode": "dev",
        "metadata": {"requirement_name": req_path.stem},
        "errors": [],
        "artifacts": [],
    }

    if args.output:
        state["metadata"]["output_dir"] = args.output

    # 逐节点执行工作流（而非一次性 invoke），实现实时进度 + 交互确认
    print("[INFO] Starting development mode workflow...", flush=True)

    from ..engine.nodes import (
        node_ddl,
        node_design,
        node_dqc,
        node_report,
        node_requirement,
        node_review,
        node_sql,
    )

    phases = [
        ("Phase 1/7: Requirement understanding", node_requirement),
        ("Phase 2/7: Design scheme", node_design),
        ("Phase 3/7: DDL generation", node_ddl),
        ("Phase 4/7: SQL development", node_sql),
        ("Phase 4.5/7: Code review", node_review),
        ("Phase 5/7: DQC quality test", node_dqc),
        ("Phase 6/7: Report delivery", node_report),
    ]

    try:
        for phase_name, node_func in phases:
            print(f"  [RUNNING] {phase_name}", flush=True)

            try:
                state = node_func(state)
            except Exception as e:
                state.setdefault("errors", []).append(f"{phase_name} 异常: {e!s}")

            # 检查错误
            if state.get("errors"):
                last_error = state["errors"][-1]
                print(f"  [ERROR] {phase_name} failed: {last_error}", flush=True)
                if "终止" in last_error or "halt" in last_error.lower():
                    print("\n[WARN] Workflow halted.", flush=True)
                    _print_artifacts(state)
                    return 1

            # Phase 1 完成后：展示需求摘要，等待用户确认
            if phase_name.startswith("Phase 1"):
                summary = state.get("requirement_summary", "")
                if summary:
                    print(f"\n{'=' * 60}", flush=True)
                    print("[Phase 1 Complete] 需求理解摘要:", flush=True)
                    print(f"{'=' * 60}", flush=True)
                    print(summary, flush=True)
                    print(f"{'=' * 60}", flush=True)
                    print("\n请确认以上内容是否正确？", flush=True)
                    print("  [Y] 确认，继续后续阶段", flush=True)
                    print("  [N] 停止，需要修改需求文档", flush=True)
                    print("  [Q] 退出", flush=True)
                    try:
                        choice = input("\nYour choice (Y/n/q): ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        choice = "y"  # 非交互环境默认继续
                    if choice in ("n",):
                        print(
                            "\n[INFO] Stopping workflow. Please update requirement doc.", flush=True
                        )
                        _print_artifacts(state)
                        return 0
                    elif choice in ("q",):
                        print("\n[INFO] Exit.", flush=True)
                        _print_artifacts(state)
                        return 1
                    # else: continue

        artifacts = state.get("artifacts", [])
        print(
            f"\n[OK] Development mode workflow completed, {len(artifacts)} artifact(s):", flush=True
        )
        for artifact in artifacts:
            print(f"  [FILE] {artifact}", flush=True)

        return 0

    except AqueductError as e:
        print(f"\n[ERROR] Workflow failed: {e}", flush=True)
        return 1


def _print_artifacts(state: WorkflowState) -> None:
    """打印已生成的产出物。"""
    artifacts = state.get("artifacts", [])
    errors = state.get("errors", [])
    if artifacts:
        print(f"\n已生成 {len(artifacts)} 个产出物:", flush=True)
        for a in artifacts:
            print(f"  [FILE] {a}", flush=True)
    if errors:
        print(f"\n错误记录 ({len(errors)}):", flush=True)
        for e in errors:
            print(f"  - {e}", flush=True)


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

    # 构建工作流状态
    state: WorkflowState = {
        "requirement": "",
        "mode": "change",
        "original_requirement": original_path.read_text(encoding="utf-8"),
        "new_requirement": new_path.read_text(encoding="utf-8"),
        "change_description": args.desc or "",
        "metadata": {"requirement_name": original_path.stem},
        "errors": [],
        "artifacts": [],
    }

    if args.output:
        state["metadata"]["output_dir"] = args.output

    print("[INFO] Starting change management workflow...")

    from ..engine.nodes import (
        node_change_archive,
        node_change_document,
        node_change_identify,
        node_change_merge,
        node_change_review,
        node_change_sql,
    )

    phases = [
        ("Phase 1/6: Change identification", node_change_identify),
        ("Phase 2/6: Change requirement document", node_change_document),
        ("Phase 3/6: Change SQL generation", node_change_sql),
        ("Phase 4/6: Change review", node_change_review),
        ("Phase 5/6: Merge execution", node_change_merge),
        ("Phase 6/6: Archive", node_change_archive),
    ]

    try:
        for phase_name, node_func in phases:
            print(f"  [RUNNING] {phase_name}", flush=True)

            try:
                state = node_func(state)
            except Exception as e:
                state.setdefault("errors", []).append(f"{phase_name} 异常: {e!s}")

            # 检查错误
            if state.get("errors"):
                last_error = state["errors"][-1]
                print(f"  [ERROR] {phase_name} failed: {last_error}", flush=True)
                if "终止" in last_error or "halt" in last_error.lower():
                    print("\n[WARN] Workflow halted.", flush=True)
                    return 1

        cr_dir = state.get("cr_dir", "")
        cr_number = state.get("cr_number", "")
        print(
            "\n[OK] Change management workflow completed",
            flush=True,
        )
        print(f"  [CR] CR-{cr_number}", flush=True)
        if cr_dir:
            print(f"  [DIR] {cr_dir}", flush=True)

        return 0

    except AqueductError as e:
        print(f"\n[ERROR] Change management failed: {e}", flush=True)
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
    root = Path(__file__).resolve().parent.parent.parent.parent

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

    # 统计知识库
    domains_dir = root / "knowledge/domains"
    domains_count = len(list(domains_dir.glob("*.json"))) if domains_dir.exists() else 0

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
