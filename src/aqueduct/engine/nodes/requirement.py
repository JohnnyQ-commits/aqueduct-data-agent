"""Phase 1: 需求理解节点。"""

from __future__ import annotations

import contextlib
import logging
import re
import time

from ...config.settings import get_settings
from ...memory.recall import KnowledgeRecall
from ...memory.store import MemoryStore
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


# URL / 文件扩展名等误识别为表名的黑名单片段
_NOISE_SUFFIXES = (
    "aliyuncs.com",
    "aliyuncs",
    "mydocs.oss",
    "oss-cn-",
    "oss.",
)
_NOISE_PARTS = {
    "aliyuncs",
    "mydocs",
    "aliyun",
    "aliyuncs.com",
    "oss",
    "oss-cn",
    "www",
    "http",
    "https",
    "com",
    "cn",
    "net",
    "org",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "pdf",
    "xlsx",
    "csv",
    "html",
    "json",
}


def _preprocess_markdown(text: str) -> str:
    r"""去除 Markdown 转义：`\_` → `_`、`\=` → `=` 等。

    需求文档经常用 `\` 转义标点，导致正则匹配失败。
    """
    return re.sub(r"\\([_\-=`*~])", r"\1", text)


def _is_noise(name: str) -> bool:
    """判断候选名是否为 URL 片段 / 文件扩展名等噪声。"""
    lower = name.lower()
    if any(lower.endswith(s) or f".{s}" in lower for s in _NOISE_SUFFIXES):
        return True
    parts = lower.split(".")
    return any(p in _NOISE_PARTS for p in parts)


def _extract_target_table(text: str) -> str:
    r"""从需求文档中提取目标表名。

    预处理：去除 Markdown 转义（`\_` → `_`）。

    匹配模式（按优先级）:
      1. '目标表[：:] xxx' 中文提示
      2. 'CREATE TABLE schema.table' SQL 语句
      3. '表[：:] xxx' / '写入/输出到 xxx' 中文动词 + 表名
      4. 独立的 schema.table 或 db.schema.table 格式标识符

    Returns:
        提取到的表名，未找到时返回空字符串。
    """
    text = _preprocess_markdown(text)

    # 模式 1: 中文提示 "目标表：xxx" / "目标表: xxx"
    m = re.search(r"目标表[：:]\s*(\S+)", text)
    if m:
        return m.group(1).rstrip("，。,.）)")

    # 模式 2: CREATE TABLE 语句
    m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip("(，。,.")

    # 模式 3: 中文动词 + 表名（支持三段式 db.schema.table）
    m = re.search(
        r"(?:表[：:]|写入|输出到|存入|保存到)\s*"
        r"((?:\w+\.)?(?:\w+)\.(?:\w+))",
        text,
    )
    if m:
        candidate = m.group(1)
        if not _is_noise(candidate):
            return candidate

    # 模式 4: 独立的三段式 db.schema.table
    for m in re.finditer(r"\b([a-z_]\w*)\.([a-z_]\w*)\.([a-z_]\w*)\b", text, re.IGNORECASE):
        candidate = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
        if not _is_noise(candidate):
            return candidate

    # 模式 5: 独立的 schema.table 格式（两段均 ≥2 字符，排除 e.g. / i.e. 等缩写）
    for m in re.finditer(r"\b([a-z_]{2,})\.([a-z_]{2,})\b", text, re.IGNORECASE):
        candidate = f"{m.group(1)}.{m.group(2)}"
        if not _is_noise(candidate):
            return candidate

    return ""


def _extract_table_names(text: str) -> list[str]:
    """从需求文档中提取所有可能的表名。

    预处理：去除 Markdown 转义。
    支持三段式 db.schema.table 和两段式 schema.table。
    自动过滤 URL 片段和文件扩展名。

    Returns:
        表名列表，未找到时返回空列表。
    """
    text = _preprocess_markdown(text)

    seen: set[str] = set()
    result: list[str] = []

    # 三段式：db.schema.table（优先匹配）
    for m in re.finditer(r"\b([a-z_]\w*)\.([a-z_]\w*)\.([a-z_]\w*)\b", text, re.IGNORECASE):
        name = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
        if not _is_noise(name) and name not in seen:
            seen.add(name)
            result.append(name)

    # 两段式：schema.table（补充匹配）
    for m in re.finditer(r"\b([a-z_]{2,})\.([a-z_]{2,})\b", text, re.IGNORECASE):
        name = f"{m.group(1)}.{m.group(2)}"
        if _is_noise(name) or name in seen:
            continue
        # 若已是三段式表名的子串则跳过（避免 dw_demo.xxx 与 xxx.yyy 重复）
        seen.add(name)
        result.append(name)

    return result


def _parse_table_name(full_name: str) -> tuple[str, str]:
    """将 database.table 格式拆分为 (database, table)。"""
    parts = full_name.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", full_name


def _recall_domain_knowledge(state: WorkflowState) -> None:
    """从本体知识库中召回与需求匹配的业务域上下文。

    在需求理解阶段调用，将结果写入 state["domain_id"] 和 state["domain_context"]，
    供后续所有节点复用。全局仅执行一次。

    无匹配领域时写入空字符串，工作流正常继续。
    """
    try:
        settings = get_settings()
        store = MemoryStore(domains_dir=settings.knowledge_dir)
        recall = KnowledgeRecall(store=store)
        result = recall.recall(state.get("requirement", ""))

        domain_id = result.get("domain_id", "")
        domain_context = result.get("domain_context", "")

        state["domain_id"] = domain_id
        state["domain_context"] = domain_context

        logger.info(
            "领域知识召回完成: domain=%s, context_length=%d",
            domain_id or "(无匹配)",
            len(domain_context),
        )
        if domain_context:
            logger.debug("召回领域内容片段预览: %s", domain_context[:200])
    except Exception:
        # 召回失败不中断工作流，写入空值继续
        state["domain_id"] = ""
        state["domain_context"] = ""
        logger.warning("领域知识召回异常，跳过", exc_info=True)


def _query_table_schemas(state: WorkflowState) -> dict[str, str]:
    """尝试通过 MCP 查询需求中涉及的表结构。

    MCP 未配置或查询失败时返回空字典（不阻塞流程）。

    Returns:
        {表名: 格式化的表结构文本} 字典。
    """
    from ...mcp.client import SyncMCPClient
    from ...mcp.config import MCPConfig

    if not MCPConfig().is_configured():
        logger.info("MCP 未配置，跳过表结构查询")
        return {}

    requirement = state.get("requirement", "")
    table_names = _extract_table_names(requirement)
    if not table_names:
        logger.info("需求文档中未找到表名，跳过表结构查询")
        return {}

    logger.info("尝试通过 MCP 查询 %d 个表的结构: %s", len(table_names), table_names)

    schemas: dict[str, str] = {}
    client: SyncMCPClient | None = None
    try:
        client = SyncMCPClient()

        for table_name in table_names:
            try:
                db, tbl = _parse_table_name(table_name)
                schema = client.get_table_schema(db, tbl)
                # 格式化为文本供 prompt 使用
                columns_text = "\n".join(
                    f"  - {c.name} ({c.type}){f' — {c.comment}' if c.comment else ''}"
                    for c in schema.columns
                )
                schemas[table_name] = (
                    f"表: {schema.database}.{schema.table}\n"
                    f"注释: {schema.comment or '无'}\n"
                    f"字段 ({len(schema.columns)} 个):\n{columns_text}"
                )
                logger.info("MCP 查询表结构成功: %s (%d 字段)", table_name, len(schema.columns))
            except Exception as e:
                logger.warning("MCP 查询表结构失败: %s - %s", table_name, e)

    except Exception as e:
        logger.warning("MCP 客户端初始化失败，跳过表结构查询: %s", e)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

    return schemas


def node_requirement(state: WorkflowState) -> WorkflowState:
    """Phase 1: 需求理解节点。

    调用 RequirementClarifySkill 生成 prompt -> LLM 解析需求。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=1] 需求理解开始", req_name)

    # 自动召回领域知识，填充 domain_context 供全流程使用
    _recall_domain_knowledge(state)

    # 从需求文档中提取目标表名，供 Phase 2+3 DDL 生成使用
    target_table = _extract_target_table(state.get("requirement", ""))
    if target_table:
        state["target_table"] = target_table
        logger.info("提取目标表名: %s", target_table)

    # 尝试通过 MCP 查询表结构（失败不阻塞）
    table_schemas = _query_table_schemas(state)
    if table_schemas:
        state["table_schemas"] = table_schemas
        logger.info("MCP 查询到 %d 个表的结构", len(table_schemas))
    else:
        state["table_schemas"] = {}

    try:
        skill = get_skill("requirement_clarify")
        context = SkillContext(
            input={
                "requirement_doc": state.get("requirement", ""),
                "domain_context": state.get("domain_context", ""),
                "table_schemas": table_schemas,
            },
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"需求解析失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "requirement_parse", prompt)

        save_artifact(state, "Phase1-需求理解摘要.md", llm_response)
        state["requirement_summary"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "requirement_parsed": "true"}

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=1] 需求理解完成: summary=%d 字符, 耗时=%.1fs",
            req_name,
            len(llm_response),
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"需求解析异常: {e!s}")
        logger.error(
            "[task=%s, phase=1] 需求理解异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state
