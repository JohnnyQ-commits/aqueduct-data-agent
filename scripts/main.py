"""
Data-Copilot 工作流编排 CLI

统一入口，将 9 个独立工具串联成端到端的数据开发流水线。

用法:
  python main.py full <sql_file> [requirement_name]    # 完整工作流
  python main.py validate <sql_file>                    # 仅校验
  python main.py design <sql_file> [name]               # 仅生成设计文档
  python main.py lineage <sql_file>                     # 仅生成血缘
  python main.py cost <sql_file> [design_file]          # 仅成本预估
  python main.py dqc <dqc_sql_file> [report_file]       # 仅数据质量测试
  python main.py sync <design_file> <ddl_file> [domain] # 仅同步
  python main.py semantic                               # 仅生成语义文档
  python main.py productivity                           # 仅提效看板
  python main.py status                                 # 项目状态概览
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

# 确保 scripts 目录在路径中（独立运行时）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_validate(sql_file: str) -> int:
    from scripts.validate_sql import main as validate_main

    sys.argv = ["validate_sql.py", sql_file]
    return validate_main()


def run_design(sql_file: str, name: str | None = None) -> int:
    from scripts.gen_design import generate_design

    return generate_design(sql_file, requirement_name=name) or 0


def run_lineage(sql_file: str) -> int:
    from scripts.gen_lineage import LineageParser

    parser = LineageParser(sql_file)
    parser.load_sql()
    parser.parse_table_lineage()
    parser.parse_field_lineage()
    print(parser.generate_mermaid())
    return 0


def run_cost(sql_file: str, design_file: str | None = None) -> int:
    from scripts.estimate_cost import CostEstimator

    estimator = CostEstimator(sql_file)
    estimator.load_sql()
    estimator.extract_tables()
    estimator.check_risks()

    if design_file:
        if estimator.update_design_doc(design_file):
            print(f"✅ 成本预估报告已更新至: {design_file}")
        else:
            print(f"⚠️ 设计文档不存在: {design_file}")
    else:
        print(estimator.generate_report())
    return 0


def run_dqc(dqc_file: str, report_file: str | None = None) -> int:
    from scripts.check_data_quality import DQCExecuter

    executer = DQCExecuter(dqc_file, report_file)
    executer.parse_test_cases()
    executer.run_tests_mock()

    if report_file:
        if executer.update_delivery_report():
            print(f"✅ DQC 结果已闭环反馈至: {report_file}")
    else:
        print(executer.generate_dqc_report_md())
    return 0


def run_sync(design_file: str, ddl_file: str, domain_json: str | None = None) -> int:
    from scripts.sync_design import DesignSyncer

    syncer = DesignSyncer(design_file)
    syncer.load_design()
    syncer.parse_structure()

    if syncer.sync_ddl(ddl_file):
        print(f"✅ DDL 已同步更新: {ddl_file}")

    if domain_json and syncer.sync_knowledge(domain_json):
        print(f"✅ 知识库已同步更新: {domain_json}")
        syncer.run_semantic_doc_gen()
    return 0


def run_semantic() -> int:
    from scripts.gen_semantic_doc import main as semantic_main

    semantic_main()
    return 0


def run_productivity() -> int:
    from scripts.analyze_productivity import ProductivityAnalyzer

    analyzer = ProductivityAnalyzer(".")
    analyzer.scan_codebase()
    analyzer.parse_logs()
    report_md = analyzer.generate_report()

    report_path = Path("PRODUCTIVITY_REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"✅ 提效看板已生成: {report_path}")
    print("-" * 30)
    print(report_md)
    return 0


def run_status() -> int:
    """项目状态概览"""
    root = PROJECT_ROOT

    # 统计脚本
    scripts = list((root / "scripts").glob("*.py"))
    scripts = [s for s in scripts if s.name != "__init__.py" and s.name != "utils.py"]

    # 统计测试
    tests = list((root / "tests").glob("test_*.py"))

    # 统计文档
    docs = list(root.glob("**/*.md"))
    docs = [
        d for d in docs
        if "templates" not in str(d)
        and ".git" not in str(d)
        and ".venv" not in str(d)
        and ".claude" not in str(d)
    ]

    # 统计模板
    templates = list((root / "templates").glob("*")) if (root / "templates").exists() else []

    # 统计知识库
    domains = list((root / "knowledge/domains").glob("*.json")) if (root / "knowledge/domains").exists() else []

    print("=== Data-Copilot 项目状态 ===")
    print(f"  工具脚本:     {len(scripts)} 个")
    for s in sorted(scripts):
        print(f"    - {s.name}")
    print(f"  测试文件:     {len(tests)} 个")
    print(f"  文档:         {len(docs)} 个")
    print(f"  模板:         {len(templates)} 个")
    print(f"  领域模型:     {len(domains)} 个")
    for d in sorted(domains):
        print(f"    - {d.stem}")
    return 0


def run_full(sql_file: str, name: str | None = None) -> int:
    """完整工作流: validate -> design -> lineage -> cost"""
    steps: list[tuple[str, Callable[[], int]]] = [
        ("1/4 SQL 校验", lambda: run_validate(sql_file)),
        ("2/4 生成设计文档", lambda: run_design(sql_file, name)),
        ("3/4 生成血缘关系", lambda: run_lineage(sql_file)),
        ("4/4 成本预估", lambda: run_cost(sql_file)),
    ]

    errors = 0
    for step_name, step_fn in steps:
        print(f"\n{'='*50}")
        print(f"  ▶ {step_name}")
        print(f"{'='*50}")
        try:
            rc = step_fn()
            if rc != 0:
                errors += 1
                print(f"  ⚠️ {step_name} 返回码: {rc}")
        except Exception as e:
            errors += 1
            print(f"  ❌ {step_name} 异常: {e}")

    print(f"\n{'='*50}")
    if errors == 0:
        print("  ✅ 完整工作流执行完毕")
    else:
        print(f"  ⚠️ 工作流完成，但有 {errors} 个步骤异常")
    print(f"{'='*50}")
    return errors


COMMANDS: dict[str, dict[str, str]] = {
    "full": {"desc": "完整工作流: 校验 → 设计 → 血缘 → 成本", "args": "<sql_file> [name]"},
    "validate": {"desc": "SQL 规范校验", "args": "<sql_file>"},
    "design": {"desc": "生成设计文档", "args": "<sql_file> [name]"},
    "lineage": {"desc": "生成血缘关系图", "args": "<sql_file>"},
    "cost": {"desc": "资源成本预估", "args": "<sql_file> [design_file]"},
    "dqc": {"desc": "数据质量测试", "args": "<dqc_sql_file> [report_file]"},
    "sync": {"desc": "同步设计文档至 DDL/知识库", "args": "<design_file> <ddl_file> [domain_json]"},
    "semantic": {"desc": "生成语义文档 (JSON → Markdown)", "args": ""},
    "productivity": {"desc": "生成提效看板", "args": ""},
    "status": {"desc": "项目状态概览", "args": ""},
}


def print_usage() -> None:
    print(__doc__)
    print("可用命令:\n")
    for cmd, info in sorted(COMMANDS.items()):
        print(f"  {cmd:<15} {info['desc']}")
        if info["args"]:
            print(f"{'':<17} 用法: {info['args']}")
    print()


def main() -> int:
    if len(sys.argv) < 2:
        print_usage()
        return 1

    command = sys.argv[1]

    if command not in COMMANDS:
        print(f"❌ 未知命令: {command}\n")
        print_usage()
        return 1

    try:
        if command == "full":
            if len(sys.argv) < 3:
                print("用法: python main.py full <sql_file> [name]")
                return 1
            name = sys.argv[3] if len(sys.argv) > 3 else None
            return run_full(sys.argv[2], name)

        elif command == "validate":
            if len(sys.argv) < 3:
                print("用法: python main.py validate <sql_file>")
                return 1
            return run_validate(sys.argv[2])

        elif command == "design":
            if len(sys.argv) < 3:
                print("用法: python main.py design <sql_file> [name]")
                return 1
            name = sys.argv[3] if len(sys.argv) > 3 else None
            return run_design(sys.argv[2], name)

        elif command == "lineage":
            if len(sys.argv) < 3:
                print("用法: python main.py lineage <sql_file>")
                return 1
            return run_lineage(sys.argv[2])

        elif command == "cost":
            if len(sys.argv) < 3:
                print("用法: python main.py cost <sql_file> [design_file]")
                return 1
            df = sys.argv[3] if len(sys.argv) > 3 else None
            return run_cost(sys.argv[2], df)

        elif command == "dqc":
            if len(sys.argv) < 3:
                print("用法: python main.py dqc <dqc_sql_file> [report_file]")
                return 1
            rf = sys.argv[3] if len(sys.argv) > 3 else None
            return run_dqc(sys.argv[2], rf)

        elif command == "sync":
            if len(sys.argv) < 4:
                print("用法: python main.py sync <design_file> <ddl_file> [domain_json]")
                return 1
            dj = sys.argv[4] if len(sys.argv) > 4 else None
            return run_sync(sys.argv[2], sys.argv[3], dj)

        elif command == "semantic":
            return run_semantic()

        elif command == "productivity":
            return run_productivity()

        elif command == "status":
            return run_status()

    except ImportError as e:
        print(f"❌ 导入模块失败: {e}")
        return 1
    except Exception as e:
        print(f"❌ 执行异常: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
