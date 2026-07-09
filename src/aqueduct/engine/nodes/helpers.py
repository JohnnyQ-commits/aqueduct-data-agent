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
import time
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
    """通过 ModelRouter 调用 LLM，带完整日志和空响应检测。

    Args:
        state: 工作流状态（用于传递 LLM 实例）。
        task_type: 任务类型（sql_gen, ddl_gen 等）。
        prompt: 发送给 LLM 的 Prompt。

    Returns:
        LLM 回复的文本内容。

    Raises:
        LLMEmptyResponseError: LLM 多次返回空响应时抛出。
        LLMTimeoutError: LLM 调用超时。
        LLMError: 其他 LLM 调用失败。
    """
    from ...exceptions import LLMEmptyResponseError

    req_name = state.get("metadata", {}).get("requirement_name", "unknown")

    router = state.get("_llm_router")
    if router is None:
        router = ModelRouter()
        state["_llm_router"] = router

    llm = router.route(task_type)

    logger.info(
        "[task=%s] LLM 调用开始: task_type=%s, model=%s, prompt=%d 字符",
        req_name,
        task_type,
        llm.model_id,
        len(prompt),
    )

    # 空响应自动重试（LLM CLI 子进程可能静默失败，重试常能恢复）
    max_empty_retries = 2
    last_content = ""

    for attempt in range(max_empty_retries + 1):
        start_time = time.time()
        try:
            messages = [LLMMessage(role="user", content=prompt)]
            response = llm.chat(messages, max_tokens=32768)
            elapsed = time.time() - start_time
            last_content = response.content

            logger.info(
                "[task=%s] LLM 调用完成: task_type=%s, model=%s, "
                "response=%d 字符, tokens(prompt=%d, completion=%d), "
                "耗时=%.1fs",
                req_name,
                task_type,
                llm.model_id,
                len(response.content),
                response.usage.prompt_tokens if response.usage else 0,
                response.usage.completion_tokens if response.usage else 0,
                elapsed,
            )

            # 空响应检测：重试（避免 LLM CLI 静默失败污染下游）
            if _is_empty_response(response.content):
                if attempt < max_empty_retries:
                    logger.warning(
                        "[task=%s] LLM 返回空响应，%d 秒后第 %d/%d 次重试",
                        req_name,
                        5,
                        attempt + 1,
                        max_empty_retries,
                    )
                    time.sleep(5)
                    continue
                # 重试耗尽，抛出异常（不再返回占位符污染下游）
                raise LLMEmptyResponseError(
                    f"LLM 返回空响应（已重试 {max_empty_retries} 次）: "
                    f"task_type={task_type}, model={llm.model_id}"
                )

            return response.content

        except LLMEmptyResponseError:
            raise

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                "[task=%s] LLM 调用失败: task_type=%s, model=%s, error=%s, 耗时=%.1fs",
                req_name,
                task_type,
                llm.model_id,
                str(e),
                elapsed,
            )
            raise

    # 兜底（理论上不会到达）
    raise LLMEmptyResponseError(
        f"LLM 返回空响应: task_type={task_type}, model={llm.model_id}, "
        f"last_content={last_content!r}"
    )


def _is_empty_response(content: str) -> bool:
    """判断 LLM 响应是否为实质空内容（含 CLI 后端的占位符）。"""
    if not content:
        return True
    stripped = content.strip()
    if not stripped:
        return True
    # claude.py 在 CLI 后端 stdout/stderr 都为空时返回的占位符
    return stripped.startswith("[LLM 调用返回为空]")


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


def is_valid_sql(content: str) -> bool:
    """检查内容是否为有效 SQL（至少包含 SELECT/INSERT/CREATE 之一且长度 > 50）。

    用于在 extract_sql_block 后做有效性兜底校验，
    防止 LLM 超时/异常产出的垃圾内容被当作正常 SQL 保存。
    """
    if not content or len(content) <= 50:
        return False
    keywords = ["select", "insert", "create", "update", "merge"]
    lower = content.lower()
    return any(kw in lower for kw in keywords)
