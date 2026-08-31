"""历史交付物检索 — 从 output/ 历史交付物与 knowledge/ 知识库中检索表级证据。

解决的问题：需求理解阶段只检索 knowledge/domains/*.json 语义模型，
对"交付过但未沉淀语义模型"的表完全不可见，导致对产出节奏/口径做错误推断。
本模块扫描历史交付 SQL（insert 语句、分区表达式）、知识沉淀文档与语义模型，
输出结构化检索结果与可读报告。

纯标准库实现（re/pathlib/logging/argparse/sys），不 import 任何项目内模块，
可在无虚拟环境时直接脚本运行：
    python src/aqueduct/memory/history.py --doc 需求文档.md
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 表名前缀白名单（数仓分层/临时表命名约定）
_TABLE_PREFIXES = (
    "ods_",
    "dwd_",
    "dws_",
    "dm_",
    "dim_",
    "ads_",
    "tmp_",
    "sds_",
    "sd_",
    "tt_",
    "ti_",
    "tb_",
    "glt_",
    "pkg_",
)

# 标识符链（可含点：库.表 / 表.字段 / URL 域名）
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

# insert (overwrite )?(into )?table 目标表
_INSERT_RE = re.compile(
    r"insert\s+(?:overwrite\s+)?(?:into\s+)?table\s+([A-Za-z0-9_.]+)",
    re.IGNORECASE,
)

# partition (… — 捕获到行尾（分区表达式内可能嵌套括号，不能用 [^)]*）
_PARTITION_RE = re.compile(r"partition\s*\((.*)$", re.IGNORECASE)

# insert 行起的分区检索窗口（含 insert 行本身，共 4 行）
_PARTITION_WINDOW = 4

# 扫描根目录与文件类型
_SCAN_BASES = ("output", "knowledge")
_SCAN_SUFFIXES = frozenset({".sql", ".md", ".json"})

# 排除目录（任意层级命中即跳过）
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        ".claude",
        ".tmp",
        "logs",
        ".idea",
        ".pytest_cache",
    }
)

# md 命中行最多展示数
_MAX_DOC_HITS = 5


def _is_table_like(name: str) -> bool:
    """判断标识符是否为白名单表名：命中前缀且长度 ≥5。"""
    return len(name) >= 5 and name.startswith(_TABLE_PREFIXES)


def extract_table_names(text: str) -> list[str]:
    """从需求文档文本提取表名。

    规则（小写、去重、保持出现顺序）：
    - 全限定名 ``库.表``：右侧命中白名单前缀且长度 ≥5 → 收集右侧，
      左侧加入库名排除集（后续独立出现时不再当作表名）
    - 右侧不命中白名单 → 视为 ``表.字段`` 引用：不收集右侧，
      左侧不加入排除集（作为普通 token 由独立 token 规则兜底提取）
    - 独立 token：命中白名单前缀、长度 ≥5、不在库名排除集 → 收集

    URL 域名（xxx.example.com）与字段名（emp_salary_ratio）天然被过滤。

    Args:
        text: 需求文档文本。

    Returns:
        表名列表（小写、去重、按首次出现顺序）。
    """
    tokens = [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]

    # 第一遍：从全限定名收集库名排除集（先建全集，再过滤独立 token，顺序不受影响）
    db_names: set[str] = set()
    for token in tokens:
        parts = token.split(".")
        for i in range(len(parts) - 1):
            if _is_table_like(parts[i + 1]):
                db_names.add(parts[i])

    # 第二遍：按出现顺序收集（右侧表名 + 独立 token 兜底）
    result: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            result.append(name)

    for token in tokens:
        parts = token.split(".")
        collected: set[int] = set()  # 已作为"右侧表名"收集的部件下标
        for i in range(len(parts) - 1):
            if _is_table_like(parts[i + 1]):
                collected.add(i + 1)
                _add(parts[i + 1])
        for i, part in enumerate(parts):
            if i in collected:
                continue
            if _is_table_like(part) and part not in db_names:
                _add(part)

    return result


def _iter_scan_files(project_root: Path):
    """遍历 output/ 与 knowledge/ 下待扫描文件（跳过排除目录）。"""
    for base in _SCAN_BASES:
        base_dir = project_root / base
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(base_dir).parts
            if any(part in _EXCLUDED_DIRS for part in rel_parts[:-1]):
                continue
            if path.suffix.lower() in _SCAN_SUFFIXES:
                yield path


def _extract_inserts(lines: list[str], table: str) -> list[dict]:
    """从 SQL 行列表中提取目标为指定表的 insert 语句。

    仅当 insert 目标表按 ``.`` 分割后的表部分等于检索表名时才算该表的 insert。
    partition 在 insert 行起 4 行窗口内查找，捕获到行尾再修剪一个尾部右括号
    （分区表达式内可能嵌套括号，如 ``$[time(yyyyMMdd,-1d)]'``）。

    Args:
        lines: SQL 文件行列表。
        table: 检索表名（小写）。

    Returns:
        insert 记录列表：{"line": 行号, "table": 完整目标表, "partition": 分区表达式}。
    """
    inserts: list[dict] = []
    for i, line in enumerate(lines):
        m = _INSERT_RE.search(line)
        if not m:
            continue
        target = m.group(1)
        if target.split(".")[-1].lower() != table:
            continue

        partition = ""
        for window_line in lines[i : i + _PARTITION_WINDOW]:
            pm = _PARTITION_RE.search(window_line)
            if pm:
                partition = pm.group(1).rstrip()
                if partition.endswith(")"):
                    partition = partition[:-1].rstrip()
                break

        inserts.append(
            {
                "line": i + 1,
                "table": target,
                "partition": partition,
                "statement": m.group(0),
            }
        )
    return inserts


def search_history(tables: list[str], project_root: Path) -> dict:
    """按表名检索历史交付物与知识库。

    扫描 ``output/``（含 changes/ 子目录）与 ``knowledge/``，
    对每个表名做词边界匹配（``\\b表名\\b``，IGNORECASE），按命中文件类型分类：
    - .sql → 交付 SQL + insert 语句提取
    - .md → 知识文档 + 命中行（最多展示 5 行）+ 命中总数
    - .json → 语义模型命中

    Args:
        tables: 检索表名列表（自动小写、去重）。
        project_root: 项目根目录。

    Returns:
        ``{表名: {"sql_files": [{"path", "inserts"}], "docs": [{"path", "hits",
        "hit_count"}], "domains": [路径]}}``，路径为相对 project_root 的 POSIX 风格。
    """
    normalized: list[str] = []
    for t in tables:
        t = t.strip().lower()
        if t and t not in normalized:
            normalized.append(t)

    results: dict = {t: {"sql_files": [], "docs": [], "domains": []} for t in normalized}
    if not normalized:
        return results

    project_root = Path(project_root)
    patterns = {t: re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in normalized}

    scanned = 0
    for file_path in _iter_scan_files(project_root):
        scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("跳过无法读取的文件 %s: %s", file_path, e)
            continue
        lines = text.splitlines()
        suffix = file_path.suffix.lower()
        rel = file_path.relative_to(project_root).as_posix()

        for table, pattern in patterns.items():
            if not any(pattern.search(line) for line in lines):
                continue
            if suffix == ".sql":
                results[table]["sql_files"].append(
                    {"path": rel, "inserts": _extract_inserts(lines, table)}
                )
            elif suffix == ".md":
                hits = [line.strip() for line in lines if line.strip() and pattern.search(line)]
                results[table]["docs"].append(
                    {"path": rel, "hits": hits[:_MAX_DOC_HITS], "hit_count": len(hits)}
                )
            elif suffix == ".json":
                results[table]["domains"].append(rel)

    logger.info("历史交付物检索完成: %d 个表, 扫描 %d 个文件", len(normalized), scanned)
    return results


def format_report(results: dict) -> str:
    """将 search_history 结果格式化为可读报告。

    Args:
        results: search_history 的返回值。

    Returns:
        按表名分组的报告文本；有 sql/md 命中但无 json 命中时
        追加"未沉淀语义模型"提示。
    """
    lines = ["=== 历史交付物检索 ===", ""]
    for table, entry in results.items():
        sql_files = entry.get("sql_files", [])
        docs = entry.get("docs", [])
        domains = entry.get("domains", [])
        total = len(sql_files) + len(docs) + len(domains)

        if total == 0:
            lines.append(f"[表] {table} — 无历史命中（新表或未开发过）")
            lines.append("")
            continue

        lines.append(f"[表] {table} — 命中 {total} 个文件")
        for sql_file in sql_files:
            lines.append(f"  [交付SQL] {sql_file['path']}")
            for ins in sql_file["inserts"]:
                lines.append(f"    L{ins['line']}: {ins['statement']}")
                if ins.get("partition"):
                    lines.append(f"      partition ({ins['partition']})")
        for doc in docs:
            lines.append(f"  [知识文档] {doc['path']} ({doc['hit_count']} 处)")
            for hit in doc["hits"]:
                lines.append(f"    - {hit}")
        for domain in domains:
            lines.append(f"  [语义模型] {domain}")
        if (sql_files or docs) and not domains:
            lines.append(f"  [提示] {table} 未沉淀语义模型 → 建议补建 knowledge/domains/*.json")
        lines.append("")

    return "\n".join(lines).rstrip("\n")


def _default_project_root() -> Path:
    """脚本直跑时推导项目根目录（src/{pkg}/memory/history.py 上溯 3 级）。"""
    return Path(__file__).resolve().parents[3]


def _main(argv: list[str] | None = None) -> int:
    """独立脚本入口：解析参数 → 提取/合并表名 → 检索 → 打印报告。"""
    parser = argparse.ArgumentParser(
        description="历史交付物检索 — 扫描 output/ 与 knowledge/ 中的表引用"
    )
    parser.add_argument("tables", nargs="*", help="要检索的表名（可多个）")
    parser.add_argument("--doc", help="需求文档路径（自动提取表名后与位置参数合并去重）")
    parser.add_argument("--root", help="项目根目录（默认按脚本位置自动推导）")
    args = parser.parse_args(argv)

    tables = [t.strip().lower() for t in args.tables if t.strip()]
    if args.doc:
        doc_path = Path(args.doc)
        if not doc_path.is_file():
            print(f"[ERROR] 需求文档不存在: {args.doc}", file=sys.stderr)
            return 1
        doc_text = doc_path.read_text(encoding="utf-8", errors="replace")
        for t in extract_table_names(doc_text):
            if t not in tables:
                tables.append(t)

    if not tables:
        parser.print_help()
        return 1

    root = Path(args.root).resolve() if args.root else _default_project_root()
    print(format_report(search_history(tables, root)))
    return 0


if __name__ == "__main__":
    # Windows GBK 兼容：报告含中文与特殊符号，GBK 控制台会 UnicodeEncodeError
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(_main())
