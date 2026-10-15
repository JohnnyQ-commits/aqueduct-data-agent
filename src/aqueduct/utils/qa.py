"""澄清问答共享工具 — 从需求理解摘要提取待确认问题、格式化并追加澄清记录。

CLI（aqueduct dev 交互确认）与 Gradio UI（--ui 问答面板）双消费方共用，
确保「### 用户澄清记录」段落在文件与 state 中的格式契约一致。

纯标准库 + 函数内延迟导入（get_output_dir），无 gradio / cli 依赖。
"""

from __future__ import annotations

import re

from ..engine.state import WorkflowState


def parse_questions(summary: str) -> list[str]:
    """从需求理解摘要中提取待确认问题列表。

    已有「### 用户澄清」记录（重跑场景）时返回空列表，防重复追加。
    """
    if "### 用户澄清" in summary:
        return []
    m = re.search(r"###\s*待确认问题\s*\n(.*?)(?=\n###|\Z)", summary, re.DOTALL)
    if not m:
        return []
    return [q.strip() for q in re.findall(r"\d+\.\s*(.+)", m.group(1)) if q.strip()]


def format_qa_section(qa_pairs: list[dict]) -> str:
    """将 Q&A 列表格式化为「### 用户澄清记录」Markdown 段落。

    文件写入和 state 更新共用此函数，确保格式一致。
    """
    lines = ["\n\n---\n\n### 用户澄清记录\n"]
    for i, qa in enumerate(qa_pairs, 1):
        lines.append(f"**Q{i}**: {qa['question']}")
        lines.append(f"**A{i}**: {qa['answer']}\n")
    return "\n".join(lines)


def append_qa_to_file(state: WorkflowState, qa_pairs: list[dict]) -> bool:
    """将 Q&A 记录追加到 Phase1-需求理解摘要.md。

    返回是否实际写入（摘要文件不存在时返回 False，不报错）。
    """
    from ..engine.nodes.helpers import get_output_dir

    filepath = get_output_dir(state) / "Phase1-需求理解摘要.md"
    if not filepath.exists():
        return False

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(format_qa_section(qa_pairs))
    return True
