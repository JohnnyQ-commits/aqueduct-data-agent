"""DDL 生成 Skill — DDLGenerateSkill。

对应 Phase 3: 表结构设计。
负责根据设计方案生成目标表 DDL。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..config.settings import get_settings
from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill

logger = logging.getLogger(__name__)


def _resolve_target_table(context: SkillContext) -> str:
    """解析目标表名，优先级：显式提取 > 领域配置 > 需求名生成 > 占位符。"""
    inp = context.input if isinstance(context.input, dict) else {}
    state = context.state

    explicit = inp.get("target_table") or state.get("target_table")
    if explicit and explicit != "dw_demo.tmp_target_table":
        return explicit

    domain_id = state.get("domain_id", "")
    if domain_id:
        try:
            settings = get_settings()
            domain_json = Path(settings.knowledge_dir) / domain_id / "domain.json"
            if domain_json.exists():
                data = json.loads(domain_json.read_text(encoding="utf-8"))
                target_tables = data.get("target_tables", {})
                if target_tables:
                    main = target_tables.get("main", {})
                    name = main.get("name", "")
                    db = main.get("database", "")
                    if name:
                        result = f"{db}.{name}" if db else name
                        logger.info("使用领域配置目标表: %s", result)
                        return result
        except Exception as e:
            logger.warning("读取领域 target_tables 失败: %s", e)

    metadata = state.get("metadata", {})
    req_name = metadata.get("requirement_name", "")
    if req_name:
        safe_name = re.sub(r"[^\w]", "_", req_name)[:40].strip("_").lower()
        if safe_name:
            return f"dw_demo.tmp_{safe_name}"

    return "dw_demo.tmp_target_table"


def _render_fallback_note(hint: dict) -> str:
    """降级线索（ddl_fallback_hint）渲染为 prompt 段；空 hint 返回空串。

    run 6 实录 P1：Phase 1 需求侧 DDL 一致性门禁降级后，Phase 3 曾对降级
    一无所知、从零另起炉灶——字段体系与 SQL 别名脱节。hint 带缺失字段
    清单 + B 路径草稿全文，声明草稿是修复基础而非参考。
    """
    if not isinstance(hint, dict):
        return ""
    reason = hint.get("reason", "")
    missing = hint.get("missing_fields") or []
    draft = hint.get("ddl_draft") or ""
    if not (reason or missing or draft):
        return ""
    lines = ["---", "", "⚠️ **Phase 1 降级线索**：需求侧 DDL 直出失败，本次生成是承接修复。"]
    if reason:
        lines.append(f"- 失败原因：{reason}")
    if missing:
        lines.append(f"- 缺失字段（必须补齐）：{'、'.join(str(f) for f in missing)}")
    if draft:
        lines += [
            "- 以下草稿是**修复基础**——在草稿上补齐缺失字段、修正不一致命名，"
            "字段命名必须与设计方案映射逐字对齐，**禁止另起炉灶重新命名**：",
            "",
            "```sql",
            draft,
            "```",
        ]
    return "\n".join(lines)


@register_skill
class DDLGenerateSkill(BaseSkill):
    """DDL 生成 Skill — 注册到全局 Skill 注册中心。"""

    name = "ddl_generate"
    description = "DDL 生成 — 根据设计方案生成目标表 CREATE TABLE 语句"
    version = "1.0.0"
    prompt_template_path = "ddl_generate.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行 DDL 生成流程。

        步骤:
          1. 读取设计方案
          2. 生成 CREATE TABLE 语句
          3. 校验 DDL 规范（分区、存储格式、注释）
        """
        inp = context.input if isinstance(context.input, dict) else {}
        state = context.state

        design_scheme = inp.get("design_scheme") or state.get("design_scheme", "")
        target_table = _resolve_target_table(context)

        # run 6 实录 P1：探查上下文 + 降级线索注入 prompt——DDL 与 SQL 同源
        schemas = inp.get("table_schemas") or state.get("table_schemas") or {}
        schemas_text = (
            "\n\n".join(str(v) for v in schemas.values())
            if isinstance(schemas, dict)
            else str(schemas)
        ) or "未获取"
        profiles = inp.get("table_profiles") or state.get("table_profiles") or {}
        profiles_text = (
            "\n\n".join(str(v) for v in profiles.values())
            if isinstance(profiles, dict)
            else str(profiles)
        ) or "未获取"
        fallback_note = _render_fallback_note(inp.get("ddl_fallback_hint") or {})

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            design_scheme=design_scheme,
            target_table=target_table,
            requirement_summary=inp.get("requirement_summary")
            or state.get("requirement_summary", "")
            or "未获取",
            table_schemas=schemas_text,
            table_profiles=profiles_text,
            ddl_fallback_note=fallback_note,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "ddl_ready"},
        )
