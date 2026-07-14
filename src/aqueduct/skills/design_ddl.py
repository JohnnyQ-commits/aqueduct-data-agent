"""方案设计 + DDL 生成合并 Skill — DesignDDLSkill。

合并 Phase 2（方案设计）和 Phase 3（DDL 生成）为单次 LLM 调用。
减少一次 LLM 往返，节省 ~140s。

对应 Phase 2+3: 同时输出设计方案和目标表 DDL。
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

    # 1. 显式提取的表名（Phase 1 正则从需求文档提取）
    explicit = inp.get("target_table") or state.get("target_table")
    if explicit and explicit != "dw_demo.tmp_target_table":
        return explicit

    # 2. 领域配置中的 target_tables（domain.json 可配置真实目标表）
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

    # 3. 从需求名生成有意义的默认表名（替代无意义的占位符）
    metadata = state.get("metadata", {})
    req_name = metadata.get("requirement_name", "")
    if req_name:
        safe_name = re.sub(r"[^\w]", "_", req_name)[:40].strip("_").lower()
        if safe_name:
            return f"dw_demo.tmp_{safe_name}"

    # 4. 兜底占位符
    return "dw_demo.tmp_target_table"


@register_skill
class DesignDDLSkill(BaseSkill):
    """方案 + DDL 合并 Skill — 注册到全局 Skill 注册中心。"""

    name = "design_ddl"
    description = "方案设计与 DDL 生成合并 — 单次 LLM 调用同时产出设计方案和 CREATE TABLE 语句"
    version = "1.0.0"
    prompt_template_path = "design_ddl.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行方案设计 + DDL 生成。

        步骤:
          1. 读取需求文档和语义模型
          2. 渲染合并 prompt 模板
          3. 返回 prompt（由节点层调用 LLM）
        """
        inp = context.input if isinstance(context.input, dict) else {}

        requirement_doc = (
            inp.get("requirement_doc")
            or context.state.get("requirement_doc", "")
            or context.state.get("requirement", "")
        )
        requirement_summary = inp.get("requirement_summary") or context.state.get(
            "requirement_summary", ""
        )
        target_table = _resolve_target_table(context)

        # 加载合并 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_doc=requirement_doc,
            requirement_summary=requirement_summary,
            domain_context=context.state.get("domain_context", ""),
            target_table=target_table,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "design_ddl_ready"},
        )
