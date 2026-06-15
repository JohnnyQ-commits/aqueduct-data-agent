"""节点辅助函数 — 所有节点共享的通用工具。

包含：
- _get_output_dir: 获取输出目录
- _save_artifact: 保存产出文件
- _call_llm: 调用 LLM
- _extract_sql_block: 从 LLM 回复中提取 SQL 代码块
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ...llm.base import LLMMessage
from ...llm.router import ModelRouter
from ..state import WorkflowState

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


def get_output_dir(state: WorkflowState) -> Path:
    """获取输出目录，优先使用 metadata 中的 output_dir，否则用 output/。"""
    metadata = state.get("metadata", {})
    raw_dir = metadata.get("output_dir") or metadata.get("requirement_name", "output")
    output_dir = raw_dir.replace("\\", "/")
    out = Path(output_dir)
    if not out.is_absolute():
        out = _PROJECT_ROOT / "output" / out.name
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_artifact(state: WorkflowState, filename: str, content: str) -> str:
    """保存产出文件到输出目录，返回相对路径。"""
    out_dir = get_output_dir(state)
    filepath = out_dir / filename
    filepath.write_text(content, encoding="utf-8")
    rel = str(filepath.relative_to(_PROJECT_ROOT))
    state.setdefault("artifacts", []).append(rel)
    return rel


def call_llm(state: WorkflowState, task_type: str, prompt: str) -> str:
    """通过 ModelRouter 调用 LLM。

    Args:
        state: 工作流状态（用于传递 LLM 实例）。
        task_type: 任务类型（sql_gen, ddl_gen 等）。
        prompt: 发送给 LLM 的 Prompt。

    Returns:
        LLM 回复的文本内容。
    """
    router = state.get("_llm_router")
    if router is None:
        router = ModelRouter()
        state["_llm_router"] = router

    llm = router.route(task_type)
    messages = [LLMMessage(role="user", content=prompt)]
    response = llm.chat(messages, max_tokens=32768)
    return response.content


def extract_sql_block(text: str) -> str:
    """从 LLM 回复中提取 ```sql ... ``` 代码块。

    如果没有找到代码块，返回原始文本。
    """
    m = re.search(r"```sql\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    return text.strip()
