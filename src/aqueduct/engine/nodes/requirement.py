"""Phase 1: 需求理解节点。"""

from __future__ import annotations

import contextlib
import logging
import re
import time

from ...memory.recall import KnowledgeRecall
from ...memory.store import MemoryStore
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..contract import gate_response
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, save_artifact

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
        # 若已是三段式表名的子串则跳过（避免 db.schema.table 与 schema.table 重复）
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
        store = MemoryStore()
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

    支持表结构缓存：先查缓存，未命中再查 MCP，结果写入缓存。
    缓存实例从 state["_table_schema_cache"] 获取（由 pipeline 入口注入）。
    缓存不存在时退化为无缓存模式。

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

    logger.info("尝试查询 %d 个表的结构: %s", len(table_names), table_names)

    # 从 state 获取缓存实例（pipeline 入口注入，跨 Phase 共享）
    cache = state.get("_table_schema_cache")

    # 先批量查缓存
    cached_schemas: dict[str, str] = {}
    uncached_names: list[str] = []

    if cache is not None:
        cached_schemas = cache.get_many(table_names)
        uncached_names = [n for n in table_names if n not in cached_schemas]
        if cached_schemas:
            logger.info("缓存命中 %d / %d 个表结构", len(cached_schemas), len(table_names))
    else:
        uncached_names = table_names

    if not uncached_names:
        # 全部命中缓存，跳过 MCP 查询
        return cached_schemas

    # 未命中的表走 MCP 查询
    schemas: dict[str, str] = dict(cached_schemas)
    client: SyncMCPClient | None = None

    try:
        client = SyncMCPClient()

        for table_name in uncached_names:
            try:
                db, tbl = _parse_table_name(table_name)
                schema = client.get_table_schema(db, tbl)
                # 格式化为文本供 prompt 使用
                columns_text = "\n".join(
                    f"  - {c.name} ({c.type}){f' — {c.comment}' if c.comment else ''}"
                    for c in schema.columns
                )
                formatted = (
                    f"表: {schema.database}.{schema.table}\n"
                    f"注释: {schema.comment or '无'}\n"
                    f"字段 ({len(schema.columns)} 个):\n{columns_text}"
                )
                schemas[table_name] = formatted

                # 写入缓存
                if cache is not None:
                    cache.set(table_name, formatted)

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


def _extract_mapping_fields(design_scheme: str) -> set[str]:
    """从设计方案「字段映射」章节提取目标字段集合。

    定位 `字段映射` 标题（H2-H4）到下一个同级/更高级标题之间的表格，
    取每行首列且形如 snake_case 的字段名；表头行（含"字段"/"目标"）与
    分隔行自动跳过。无法定位章节或无有效字段时返回空集合。
    """
    m = re.search(r"^#{2,4}\s*字段映射\s*$", design_scheme, re.MULTILINE)
    if not m:
        return set()

    seg = design_scheme[m.end() :]
    nxt = re.search(r"^#{1,4}\s", seg, re.MULTILINE)
    if nxt:
        seg = seg[: nxt.start()]

    fields: set[str] = set()
    for line in seg.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0].strip("`* ")
        if not first or set(first) <= {"-", ":", " "}:
            continue  # 分隔行
        if "字段" in first or "目标" in first:
            continue  # 表头行
        # 剥类型标注：真实输出首列常为 "order_count (bigint)" 形式
        first = first.split()[0]
        if re.fullmatch(r"[a-z_][a-z0-9_]*", first):
            fields.add(first)
    return fields


_DDL_TYPES = (
    r"(?:string|bigint|int|smallint|tinyint|double|float|decimal\s*\([^)]*\)"
    r"|boolean|date|timestamp|varchar\s*\([^)]*\))"
)


def _extract_ddl_fields(ddl_content: str) -> set[str]:
    """从 CREATE TABLE 语句提取列字段集合（含 PARTITIONED BY 分区字段）。

    列定义要求行首缩进 + 标识符 + 类型关键字，避免匹配 CREATE TABLE 行
    与注释文本；提取失败（非 DDL 文本）返回空集合。
    """
    fields: set[str] = set()
    for m in re.finditer(rf"^\s+(\w+)\s+{_DDL_TYPES}", ddl_content, re.MULTILINE | re.IGNORECASE):
        fields.add(m.group(1).lower())
    for m in re.finditer(r"partitioned\s+by\s*\(([^)]*)\)", ddl_content, re.IGNORECASE):
        # 第二组同样约束为类型关键字，避免 COMMENT 中文误当字段名
        for pm in re.finditer(rf"(\w+)\s+{_DDL_TYPES}", m.group(1), re.IGNORECASE):
            fields.add(pm.group(1).lower())
    return fields


def _check_ddl_consistency(design_scheme: str, ddl_content: str) -> list[str]:
    """设计方案字段映射 vs DDL 字段集比对，返回 DDL 缺失的映射字段（排序后）。

    只查映射→DDL 方向（DDL 比 mapping 多的字段如 etl_time 属正常扩展）；
    任一侧提取失败时返回空列表（零误报原则，跳过校验）。
    """
    mapping = _extract_mapping_fields(design_scheme)
    ddl_fields = _extract_ddl_fields(ddl_content)
    if not mapping or not ddl_fields:
        return []
    return sorted(mapping - ddl_fields)


def _build_ddl_prompt(state: WorkflowState) -> str:
    """渲染 P1-1 B 路径（需求侧 DDL 直出）prompt；模板缺失返回空串。"""
    from string import Template

    from ...config.settings import get_settings

    tpl_path = get_settings().prompt_dir / "ddl_generate_req.tpl.md"
    if not tpl_path.exists():
        logger.warning("DDL 需求侧模板不存在: %s，回退 Phase 3 独立生成", tpl_path)
        return ""

    table_schemas = state.get("table_schemas", {})
    if isinstance(table_schemas, dict):
        schemas_text = "\n\n".join(table_schemas.values()) if table_schemas else "未获取"
    else:
        schemas_text = str(table_schemas) if table_schemas else "未获取"

    return Template(tpl_path.read_text(encoding="utf-8")).safe_substitute(
        requirement_doc=state.get("requirement", ""),
        domain_context=state.get("domain_context", ""),
        table_schemas=schemas_text,
        target_table=state.get("target_table", ""),
    )


def _generate_ddl_parallel(state: WorkflowState) -> tuple[str, str]:
    """P1-1 B 路径：从需求 + 表结构直接生成目标表 DDL（不经设计方案）。

    Returns:
        (ddl_content, error_msg)——失败时 ddl 为空串、error 非空，
        由调用方记录 errors 并回退 Phase 3 独立生成。
    """
    prompt = _build_ddl_prompt(state)
    if not prompt:
        return "", "DDL 需求侧模板缺失，已回退 Phase 3 独立生成"

    resp = call_llm(state, "ddl_gen", prompt)
    if not re.search(r"```sql\s*\n", resp):
        logger.warning("并行 DDL 响应无 SQL 代码块，回退 Phase 3 独立生成")
        return "", "并行 DDL 响应无 SQL 代码块，已回退 Phase 3 独立生成"
    return extract_sql_block(resp), ""


def node_requirement(state: WorkflowState) -> WorkflowState:
    """Phase 1: 需求理解节点。

    OPT-5: 需求摘要 + 设计方案合并为单次 LLM 调用。
    P1-1: 三合一再拆分——A（需求+方案，design_ddl）∥ B（DDL 需求侧直出，
    ddl_gen）两个并行小调用，各自思考密度减半、远离螺旋阈值；一致性由
    代码侧字段集比对兜底（1 次定向修复），失败回退 Phase 3 独立生成。

    OPT-7: 增量管道 — 需求未变更时跳过 Phase 1，从 manifest 恢复输出。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()

    # OPT-7: 增量管道 — 检查是否可以跳过 Phase 1
    from ...utils.change_analyzer import ChangeAnalyzer
    from .helpers import get_output_dir

    output_dir = get_output_dir(state)
    analyzer = ChangeAnalyzer(output_dir=output_dir)
    requirement = state.get("requirement", "")

    if analyzer.should_skip_phase1(requirement):
        analyzer.restore_phase1_outputs(state)
        logger.info(
            "[task=%s, phase=1] 增量跳过: 需求未变更，从 manifest 恢复输出（耗时 %.1fs）",
            req_name,
            time.time() - start,
        )
        return state

    logger.info("[task=%s, phase=1] 需求理解开始（A需求+方案 ∥ B DDL 拆分模式）", req_name)

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
        # OPT-5: 二合一 Skill（需求分析+方案设计）；DDL 由 B 路径并行生成
        skill = get_skill("requirement_and_design")
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
            state.setdefault("errors", []).append(f"需求+方案设计失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")

        # P1-1: A（需求+方案）∥ B（DDL）并行——B 输入同为需求+表结构，不依赖 A 产物
        from concurrent.futures import ThreadPoolExecutor

        from .design import _split_requirement_and_design

        def _gen_ddl() -> tuple[str, str]:
            try:
                return _generate_ddl_parallel(state)
            except Exception as e:
                logger.warning("并行 DDL 生成异常，回退 Phase 3 独立生成", exc_info=True)
                return "", f"并行 DDL 生成失败（{e}），已回退 Phase 3 独立生成"

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="phase1-split") as executor:
            a_future = executor.submit(call_llm, state, "design_ddl", prompt)
            b_future = executor.submit(_gen_ddl)
            llm_response = a_future.result()
            ddl_content, ddl_error = b_future.result()

        if ddl_error:
            # B 失败不炸管道：ddl_content 为空 → Phase 3 独立生成节点接管
            state.setdefault("errors", []).append(ddl_error)

        # A 响应拆分（二合一响应无 SQL 块，DDL 一律来自 B）
        def _split_pair(resp: str) -> dict[str, str]:
            req, design, _ = _split_requirement_and_design(resp)
            return {
                "Phase1-需求理解摘要.md": req,
                "Phase2-设计方案.md": design,
            }

        # P0-2: 结构契约门禁——只管 A 产物，缺章触发 1 次定向重生成，仍缺降级横幅 + errors
        def _regen(retry_prompt: str, missing: list[str]) -> str:
            return call_llm(state, "design_ddl", retry_prompt)

        products, missing = gate_response(
            prompt,
            llm_response,
            _split_pair,
            ["Phase1-需求理解摘要.md", "Phase2-设计方案.md"],
            _regen,
        )
        if missing:
            state.setdefault("errors", []).append(
                f"Phase1/Phase2 结构缺章（定向重生成 1 次后仍缺）: {'、'.join(missing)}"
            )
            logger.warning("[task=%s, phase=1] 结构缺章降级落盘: %s", req_name, "、".join(missing))

        req_summary = products["Phase1-需求理解摘要.md"]
        design_scheme = products["Phase2-设计方案.md"]

        # P1-1 一致性兜底：设计方案字段映射 vs B 生成的 DDL 字段集比对
        if ddl_content and len(ddl_content) > 50 and design_scheme:
            miss_fields = _check_ddl_consistency(design_scheme, ddl_content)
            if miss_fields:
                logger.warning(
                    "[task=%s, phase=1] DDL 与字段映射不一致（缺 %s），定向修复 1 次",
                    req_name,
                    "、".join(miss_fields),
                )
                ddl_prompt = _build_ddl_prompt(state)
                if ddl_prompt:
                    fix_prompt = (
                        f"{ddl_prompt}\n\n---\n\n⚠️ **一致性警示**：设计方案的字段映射包含以下字段，"
                        f"但 DDL 缺失：{'、'.join(miss_fields)}。请对齐字段映射重新生成完整 DDL。\n"
                    )
                    try:
                        fix_resp = call_llm(state, "ddl_gen", fix_prompt)
                        if re.search(r"```sql\s*\n", fix_resp):
                            fixed = extract_sql_block(fix_resp)
                            if not _check_ddl_consistency(design_scheme, fixed):
                                ddl_content = fixed
                                miss_fields = []
                    except Exception:
                        logger.warning("DDL 一致性修复调用异常", exc_info=True)
                if miss_fields:
                    state.setdefault("errors", []).append(
                        f"DDL 与设计方案字段映射不一致（缺 {'、'.join(miss_fields)}），已回退 Phase 3 独立生成"
                    )
                    logger.warning(
                        "[task=%s, phase=1] DDL 一致性修复后仍缺字段，回退 Phase 3: %s",
                        req_name,
                        "、".join(miss_fields),
                    )
                    ddl_content = ""

        # 保存需求理解摘要
        save_artifact(state, "Phase1-需求理解摘要.md", req_summary)
        state["requirement_summary"] = req_summary

        # 保存设计方案
        if design_scheme:
            save_artifact(state, "Phase2-设计方案.md", design_scheme)
            state["design_scheme"] = design_scheme

        # 保存 DDL
        if ddl_content and len(ddl_content) > 50:
            ddl_path = save_artifact(state, "Phase3-表结构.sql", ddl_content)
            state["ddl_content"] = ddl_content
            state["ddl_file"] = ddl_path

        state["metadata"] = {
            **(state.get("metadata", {})),
            "requirement_parsed": "true",
            "design_done": "true",
            "ddl_done": "true" if ddl_content and len(ddl_content) > 50 else "false",
        }

        # OPT-7: 保存 manifest 供下次增量管道使用
        analyzer.save_manifest(state.get("requirement", ""), state)

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=1] 拆分完成: summary=%d 字符, design=%d 字符, ddl=%d 字符, 耗时=%.1fs",
            req_name,
            len(req_summary),
            len(design_scheme),
            len(ddl_content),
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"需求+方案设计异常: {e!s}")
        logger.error(
            "[task=%s, phase=1] 拆分异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state
