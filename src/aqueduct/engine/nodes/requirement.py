"""Phase 1: 需求理解节点。"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from typing import Any

from ...memory.recall import KnowledgeRecall
from ...memory.store import MemoryStore
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ...utils.sql_engine import is_timeout_error, iter_engine_attempts
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
    from ...platform import Capability, get_platform_adapter

    adapter = get_platform_adapter()
    if not adapter.has_capability(Capability.TABLE_METADATA):
        logger.info(
            "平台 %s 未声明 table_metadata 能力，跳过表结构查询（静态分析模式）",
            adapter.name,
        )
        return {}

    from ...mcp.client import SyncMCPClient

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


# ---------------------------------------------------------------------------
# 确定性数据探查（2026-10-16 受控对比定靶）
#
# CLI 的 MCP 通道只查表结构不跑数据，需求里埋的 guid/枚举两陷阱全踩；
# skill 模式的杀手锏是行数/枚举/空值/唯一性实查。此处用确定性代码补上：
# 对每个源表最新分区跑固定画像探查，纯代码执行（不经 LLM），结果注入
# Phase 1 prompt。探查仅限单分区聚合（每表 ≤4 条），逐条 try/except 降级，
# 平台无 sql_execute 能力时整体跳过（开源离线模式照跑）。
# ---------------------------------------------------------------------------

# 画像候选列：需求提及列 + 键形列（*_no/*_id/*_code/*_guid/*_key）
_KEY_LIKE_SUFFIXES = ("_no", "_id", "_code", "_guid", "_key")
_PROFILE_COL_CAP = 8
_ENUM_COL_CAP = 3
_PROFILE_TABLE_CAP = 8

# 枚举形字段名特征（2026-10-16 实录：cap 截断把 signin_tm 时间戳拿去白探
# 枚举分布，真正的 hl_scene_type 反而被挤掉）——枚举形列优先，时间戳排除
_ENUMISH_MARKERS = ("type", "status", "state", "scene", "flag", "level", "category", "grade")
_TIMESTAMP_LIKE = ("_tm", "_time", "_date", "_dt", "timestamp")

# 探查专用轮询上限（run 3 实录 2026-10-19）：默认 60 次 × 5s = 5 分钟在 MR
# 慢队列下不够，明细表探查全灭 → 枚举目标落空。120 次 = 10 分钟。
_PROBE_WAIT_ATTEMPTS = 120

# 当日探查结果缓存 {(sql, 日期): (rows, err)}：同日分区数据不变，halt 重跑 /
# 修复环复跑直接命中；只缓存成功结果（超时/失败可重试）
_PROBE_CACHE: dict[tuple[str, str], tuple[list[list[Any]] | None, str]] = {}

# 从 _query_table_schemas 的格式化文本解析字段：`  - name (type) — comment`
_RE_SCHEMA_FIELD = re.compile(r"^\s*-\s+(\w+)\s*\(([^)]+)\)", re.MULTILINE)
_STRING_TYPES = {"string", "varchar", "char"}


def _select_profile_columns(schema_text: str, requirement: str) -> tuple[list[str], list[str]]:
    """从表结构文本 + 需求文本选出探查列。

    Returns:
        (enum_cols, profile_cols) — enum_cols 限 string 类型且需求提及
        （做 top-N 枚举分布，上限 3；枚举形字段名优先、时间戳字段排除）；
        profile_cols = 需求提及 ∪ 键形列（做空值率 + distinct 画像，上限 8）。
    """
    fields = _RE_SCHEMA_FIELD.findall(schema_text)
    mentioned = [name for name, _ in fields if re.search(rf"\b{re.escape(name)}\b", requirement)]
    key_like = [
        name for name, _ in fields if name.endswith(_KEY_LIKE_SUFFIXES) and name not in mentioned
    ]
    # 需求提及优先，键形列兜底，截断到画像上限
    profile_cols = list(dict.fromkeys(mentioned + key_like))[:_PROFILE_COL_CAP]
    # 枚举探查只对 string 类型的需求提及列有意义（int 枚举无业务价值）
    type_by_name = dict(fields)
    enum_candidates = [
        name for name in mentioned if type_by_name.get(name, "").lower() in _STRING_TYPES
    ]

    def _enum_rank(name: str) -> tuple[int, int]:
        low = name.lower()
        if any(m in low for m in _ENUMISH_MARKERS):
            return (0, enum_candidates.index(name))  # 枚举形优先
        if any(low.endswith(sfx) for sfx in _TIMESTAMP_LIKE):
            return (2, enum_candidates.index(name))  # 时间戳最后（无枚举形候选时兜底）
        return (1, enum_candidates.index(name))

    enum_cols = sorted(enum_candidates, key=_enum_rank)[:_ENUM_COL_CAP]
    return enum_cols, profile_cols


def _probe_once(executor: Any, sql: str, engine: str) -> tuple[list[list[Any]] | None, str]:
    """用指定引擎执行单条探查 SQL。返回 (rows, error)。"""
    try:
        result = executor.execute(
            action="execute", sql=sql, wait_attempts=_PROBE_WAIT_ATTEMPTS, engine=engine
        )
        if not getattr(result, "success", False):
            err = str(getattr(result, "error", ""))
            logger.warning("数据探查失败（%s，engine=%s）: %s", sql[:120], engine, err[:300])
            return None, err
        return (result.data or {}).get("rows") or [], ""
    except Exception as e:
        logger.warning("数据探查异常（%s，engine=%s）: %s", sql[:120], engine, e)
        return None, str(e)


def _run_probe(executor: Any, sql: str) -> tuple[list[list[Any]] | None, str]:
    """执行单条探查 SQL。返回 (rows, error)——失败 rows=None、error 非空
    （降级不抛，错误串供分区护栏等分支判定）。

    探查快车道引擎链 presto→spark→hive（run 4 实录：同一探查 SQL MR 10 分钟
    超时 ×4 全灭，presto 16s 返回；引擎链与转换语义见 utils/sql_engine）。
    不兼容语法优先转换重试，超时立即中止整条链。成功结果按 (sql, 当日) 缓存
    ——halt 后重跑不再白等一轮，失败不入缓存（恢复后可重试）。
    """
    from datetime import date

    cache_key = (sql, date.today().isoformat())
    if cache_key in _PROBE_CACHE:
        return _PROBE_CACHE[cache_key]
    rows, err = None, ""
    for engine, attempt_sql in iter_engine_attempts(sql):
        rows, err = _probe_once(executor, attempt_sql, engine)
        if rows is not None or _is_probe_timeout(err):
            break
    if rows is not None:
        _PROBE_CACHE[cache_key] = (rows, "")
    return rows, err


def _is_probe_timeout(err: str) -> bool:
    """探查错误是否为超时（超时未探查必须显式标注，不能冒充普通失败）。"""
    return is_timeout_error(err)


# 平台分区护栏（2026-10-16 UI 实录）：无分区过滤的探查 SQL 被平台拒绝
# （PartitionOverLimitException: can only query max 400 partition）——
# 大表分区数 1390/1449/3205 全部超限，4 表探查全灭。回退走 show partitions
# （metastore 列表，不触发分区扫描 hook）取最新分区再带过滤探查。
_RE_PARTITION_LIMIT = re.compile(r"partitionoverlimit|max\s+400\s+partition", re.IGNORECASE)


def _latest_partition_fallback(executor: Any, table: str) -> tuple[str | None, str]:
    """护栏命中时的回退：年份窗口谓词探 max(inc_day)。

    2026-10-16 UI 实录弃用 show partitions——返回被平台截断，max 只反映
    截断子集（探到 20230104 陈旧分区）。年份窗口靠分区裁剪把扫描分区压到
    ~365 < 400 不撞护栏，且拿到真实最新分区；当年无数据回退上一年再试。

    Returns:
        (latest_partition, last_err)——失败时把最后一次错误带出来供超时
        判定（run 4 实录：回退超时错误被吞，探查超时冒充「无 inc_day
        分区键」，标注与降级横幅全哑火）。
    """
    from datetime import date

    year = date.today().year
    last_err = ""
    for y in (year, year - 1):
        rows, err = _run_probe(
            executor,
            f"select max(inc_day) as latest_partition from {table} "
            f"where inc_day >= '{y}0101' and inc_day < '{y + 1}0101'",
        )
        if rows and rows[0][0] is not None:
            return rows[0][0], ""
        last_err = err or last_err
    return None, last_err


def _profile_one_table(executor: Any, table: str, schema_text: str, requirement: str) -> str:
    """对单张表跑画像探查，返回注入 prompt 的格式化文本。"""
    enum_cols, profile_cols = _select_profile_columns(schema_text, requirement)

    # 探查① 最新分区（护栏命中回退年份窗口）
    latest_rows, latest_err = _run_probe(
        executor, f"select max(inc_day) as latest_partition from {table}"
    )
    latest = latest_rows[0][0] if latest_rows and latest_rows[0][0] is not None else None
    if latest is None and _is_probe_timeout(latest_err):
        # run 3 实录：探查超时被静默吞掉，枚举占位上线风险无人知晓——显式标注
        return (
            f"表: {table}\n数据探查: 探查超时（10 分钟未返回，MR 慢队列）"
            "——超时未探查，枚举/画像值未实测，禁止作为已验证口径"
        )
    if latest is None and _RE_PARTITION_LIMIT.search(latest_err):
        latest, fallback_err = _latest_partition_fallback(executor, table)
        if latest is None and _is_probe_timeout(fallback_err):
            # run 4 实录：回退年窗超时的错误曾被吞掉，超时冒充「无 inc_day
            # 分区键」——标注与降级横幅全哑火。回退路径超时同样显式标注
            return (
                f"表: {table}\n数据探查: 探查超时（10 分钟未返回，MR 慢队列）"
                "——超时未探查，枚举/画像值未实测，禁止作为已验证口径"
            )
    if latest is None:
        return f"表: {table}\n数据探查: 无 inc_day 分区键或探查失败，跳过数据探查（仅静态分析）"

    lines = [f"表: {table}（探查分区 {latest}，以下为平台实测数据）"]
    where = f"where inc_day='{latest}'"

    # 探查② 分区行数
    cnt_rows, cnt_err = _run_probe(executor, f"select count(*) from {table} {where}")
    row_cnt = cnt_rows[0][0] if cnt_rows and cnt_rows[0][0] is not None else None
    if row_cnt is not None:
        lines.append(f"行数: {row_cnt}")
    elif _is_probe_timeout(cnt_err):
        lines.append("行数: 探查超时，超时未探查")
    else:
        lines.append("行数: 探查失败")

    # 探查③ 需求提及 string 列的枚举分布（top 10）
    for col in enum_cols:
        rows, err = _run_probe(
            executor,
            f"select {col}, count(*) as cnt from {table} {where} "
            f"group by {col} order by cnt desc limit 10",
        )
        if rows:
            tops = ", ".join(f"{r[0]}={r[1]}" for r in rows)
            lines.append(f"{col} 枚举分布: {tops}")
        elif _is_probe_timeout(err):
            lines.append(f"{col} 枚举分布: 探查超时，超时未探查（枚举值未实测，占位符禁止上线）")
        else:
            lines.append(f"{col} 枚举分布: 探查失败")

    # 探查④ 画像：空值率 + distinct（一条 SQL 查全部画像列）
    if profile_cols:
        parts = ["count(*) as total"]
        for col in profile_cols:
            parts.append(f"sum(case when {col} is null then 1 else 0 end) as {col}_null")
            parts.append(f"count(distinct {col}) as {col}_dist")
        rows, profile_err = _run_probe(executor, f"select {', '.join(parts)} from {table} {where}")
        if rows and len(rows[0]) == 1 + 2 * len(profile_cols):
            row = rows[0]
            for i, col in enumerate(profile_cols):
                null_cnt, dist = row[1 + 2 * i], row[2 + 2 * i]
                note = "全唯一" if dist == row_cnt else "存在重复"
                line = f"- {col}: 空值={null_cnt}, distinct={dist}"
                if row_cnt is not None:
                    line += f"（{note}）"
                lines.append(line)
        elif _is_probe_timeout(profile_err):
            lines.append("画像探查（空值/distinct）: 探查超时，超时未探查")
        else:
            lines.append("画像探查（空值/distinct）失败")

    return "\n".join(lines)


def _profile_source_tables(state: WorkflowState, table_names: list[str]) -> dict[str, str]:
    """对源表跑确定性数据探查，返回 {表名: 探查文本}。

    门禁：平台必须声明 SQL_EXECUTE 能力（NoneAdapter / execution_enabled=False
    时优雅返回空 dict）。逐表 try/except，单表失败降级为探查失败标记。
    上限 _PROFILE_TABLE_CAP 张表，防止需求误提大量表名时探查失控。
    """
    from ...platform import Capability, get_platform_adapter

    adapter = get_platform_adapter()
    if not adapter.has_capability(Capability.SQL_EXECUTE):
        logger.info("平台 %s 未声明 sql_execute 能力，跳过 Phase 1 数据探查", adapter.name)
        return {}
    if not table_names:
        return {}

    from ...tools.registry import get_tool

    executor = get_tool("executor")
    requirement = state.get("requirement", "")
    schemas = state.get("table_schemas", {})
    # 目标表（还不存在）剔除——2026-10-16 实录：白探一条 Table not found。
    # target_table 可能带反引号（需求文档 markdown 内联代码原样提取），归一化再比
    target = (state.get("target_table") or "").strip("`\"' ").lower()
    if target:
        table_names = [t for t in table_names if t.strip("`\"' ").lower() != target]

    profiles: dict[str, str] = {}
    for table in table_names[:_PROFILE_TABLE_CAP]:
        if len(table_names) > _PROFILE_TABLE_CAP:
            logger.warning(
                "源表 %d 张超过探查上限 %d，仅探查前 %d 张",
                len(table_names),
                _PROFILE_TABLE_CAP,
                _PROFILE_TABLE_CAP,
            )
        try:
            profiles[table] = _profile_one_table(
                executor, table, schemas.get(table, ""), requirement
            )
        except Exception as e:
            logger.warning("数据探查失败: %s - %s", table, e)
            profiles[table] = f"表: {table}\n数据探查失败: {e}"
    if profiles:
        logger.info("Phase 1 数据探查完成: %d/%d 张表", len(profiles), len(table_names))
    return profiles


def _extract_mapping_section(design_scheme: str) -> str:
    """定位「字段映射」章节原文（标题行到下一个同级/更高级标题之间）。

    run 8 实录：真实输出的映射表常按产出表拆成 H3 子小节（两张 ADS 表
    各一张映射表）——子标题不是边界，提取必须持续到同级/更高级标题。
    无法定位章节时返回空串。
    """
    m = re.search(r"^(#{2,4})\s*字段映射\s*$", design_scheme, re.MULTILINE)
    if not m:
        return ""

    anchor_level = len(m.group(1))
    seg = design_scheme[m.end() :]
    nxt = re.search(rf"^#{{1,{anchor_level}}}\s", seg, re.MULTILINE)
    if nxt:
        seg = seg[: nxt.start()]
    return seg


def _extract_mapping_fields(design_scheme: str) -> set[str]:
    """从设计方案「字段映射」章节提取目标字段集合。

    定位 `字段映射` 标题（H2-H4）到下一个同级/更高级标题之间的表格，
    取每行首列且形如 snake_case 的字段名；表头行（含"字段"/"目标"）与
    分隔行自动跳过。无法定位章节或无有效字段时返回空集合。
    """
    seg = _extract_mapping_section(design_scheme)
    if not seg:
        return set()

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


def _build_ddl_consistency_fix_prompt(
    state: WorkflowState, design_scheme: str, miss_fields: list[str]
) -> str:
    """P1-1 一致性修复 prompt：B 路径上下文 + 映射全文内嵌 + 缺失警示。

    run 8 实录：只列缺失字段名、不带映射上下文——B 重生成的命名锚点
    仍是需求文档（不含派生字段名），"修复仍缺"构造性注命，2 轮全败
    回退 Phase 3。内嵌映射全文让字段命名与设计方案逐字对齐。
    """
    ddl_prompt = _build_ddl_prompt(state)
    return (
        f"{ddl_prompt}"
        f"\n\n---\n\n"
        f"⚠️ **一致性警示**：设计方案的字段映射包含以下字段，但 DDL 缺失："
        f"{'、'.join(miss_fields)}。字段命名必须与下方设计方案映射逐字对齐"
        f"（同名同义，不得另行命名），重新生成完整 DDL（产出表逐表建齐）。\n\n"
        f"**设计方案字段映射全文**：\n\n{_extract_mapping_section(design_scheme)}\n"
    )


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
        # 第四刀 4b：增量跳过也要查源表结构——table_schemas 不在 manifest
        # 缓存字段里，跳过后 Phase 4 渲染"未获取"，按设计方案字段映射写 SQL
        # 会把转述笔误带上试跑（run 6 sign_time 无效列引用实录）。查询有
        # 缓存兜底，平台无 table_metadata 能力时快速返回空，不阻塞。
        state["table_schemas"] = _query_table_schemas(state) or {}
        # 第四刀 4a-1：restore 只回填 ddl_content 不落 ddl_file——Phase 3 的
        # OPT-4 判断（ddl_content AND ddl_file）永远失败，白白重跑独立 DDL
        # 生成（run 6 实录：白烧一次 LLM 调用 48s）。落盘 artifact 补齐断链。
        if state.get("ddl_content") and not state.get("ddl_file"):
            state["ddl_file"] = save_artifact(state, "Phase3-表结构.sql", state["ddl_content"])
            logger.info(
                "[task=%s, phase=1] 增量跳过: manifest 中的 DDL 落盘 artifact，Phase 3 可直接复用",
                req_name,
            )
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

    # 确定性数据探查（失败不阻塞）：对源表最新分区跑画像探查，
    # 结果注入需求理解 prompt——guid 口径歧义、枚举占位符类陷阱靠它识破。
    # 表名与 _query_table_schemas 同源（需求文本提取），保证探的是查过结构的表
    table_profiles = _profile_source_tables(
        state, _extract_table_names(state.get("requirement", ""))
    )
    state["table_profiles"] = table_profiles
    if table_profiles:
        logger.info("数据探查覆盖 %d 张源表", len(table_profiles))

    try:
        # OPT-5: 二合一 Skill（需求分析+方案设计）；DDL 由 B 路径并行生成
        skill = get_skill("requirement_and_design")
        context = SkillContext(
            input={
                "requirement_doc": state.get("requirement", ""),
                "domain_context": state.get("domain_context", ""),
                "table_schemas": table_schemas,
                "table_profiles": table_profiles,
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
            # B 失败不炸管道：ddl_content 为空 → Phase 3 独立生成节点接管。
            # run 6 实录 P1：降级线索（失败原因）落 hint 供 Phase 3 prompt
            # 交代上下文，Phase 3 不再对降级一无所知
            state.setdefault("errors", []).append(ddl_error)
            state["ddl_fallback_hint"] = {
                "missing_fields": [],
                "ddl_draft": "",
                "reason": ddl_error,
            }

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
                fix_prompt = _build_ddl_consistency_fix_prompt(state, design_scheme, miss_fields)
                if fix_prompt.strip():
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
                    # run 6 实录 P1：丢弃 B 草稿 = Phase 3 从零另起炉灶，DDL
                    # 字段体系与 SQL 别名脱节 → 对齐审查炸 16 Critical。落
                    # hint（缺失清单 + 草稿全文）供 Phase 3 以草稿为修复基础
                    state["ddl_fallback_hint"] = {
                        "missing_fields": list(miss_fields),
                        "ddl_draft": ddl_content,
                        "reason": "DDL 与设计方案字段映射不一致",
                    }
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
