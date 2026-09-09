"""代码评审 Skill — CodeReviewSkill。

对应 Phase 4.5: 代码审查。
负责审查线上版本与变更版本的差异。

CodeReviewDimensionSkill（PERF-11）：单维度审查模板——单块审查拆
3 维度并行调用，每维一个小 prompt（同输入不同审查透镜，P0-3 DQC
拆分范式）。输出契约锁定 `- [Critical/Warning/Confirm] ` 列表行
（原全量模板教模型输出表格，_parse_review_issues 解析不出——
LLM 审查发现从未进过修复循环，2026-09-09 四个 eval 报告实录）。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class CodeReviewSkill(BaseSkill):
    """代码评审 Skill — 注册到全局 Skill 注册中心。"""

    name = "code_review"
    description = "代码审查 — 差异比对、需求覆盖度验证、下游影响分析"
    version = "1.0.0"
    prompt_template_path = "code_review.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行代码审查流程。

        步骤:
          1. 读取线上版本 SQL
          2. 读取变更版本 SQL
          3. 逐行差异比对
          4. 需求覆盖度验证
          5. 下游影响分析
          6. 潜在问题检查
        """
        inp = context.input if isinstance(context.input, dict) else {}

        sql_content = inp.get("sql_content") or context.state.get("sql_content", "")
        # TODO-5: 设计上下文直达审查（input 优先，state 兜底，缺失渲染占位）——
        # 审查器要能核对 SQL 与设计方案取数逻辑、目标表 DDL 字段的对齐
        design_scheme = inp.get("design_scheme") or context.state.get("design_scheme", "")
        ddl_content = inp.get("ddl_content") or context.state.get("ddl_content", "")
        online_sql = inp.get("online_sql") or context.state.get("online_sql", "")
        changed_sql = inp.get("changed_sql") or context.state.get("changed_sql", "")
        requirement_desc = (
            inp.get("requirement_desc")
            or context.state.get("requirement_desc", "")
            or context.state.get("requirement", "")
        )
        validation_result = inp.get("validation_result") or context.state.get(
            "validation_result", {}
        )

        # 将 validation_result dict 格式化为可读文本，避免 str.format() 渲染 Python repr
        if isinstance(validation_result, dict):
            issues = validation_result.get("issues", [])
            if issues:
                validation_text = "\n".join(
                    f"- [{i.get('level', 'INFO')}] Line {i.get('line', '?')}: {i.get('message', '')}"
                    for i in issues
                )
            else:
                validation_text = "未发现校验问题"
        else:
            validation_text = str(validation_result)

        # 加载 Prompt 模板（设计上下文缺失渲染「未获取」占位，不泄漏 $变量）
        prompt = self.load_prompt_template(
            requirement_desc=requirement_desc,
            online_sql=online_sql,
            changed_sql=changed_sql,
            sql_content=sql_content,
            domain_context=context.state.get("domain_context", ""),
            validation_result=validation_text,
            design_scheme=design_scheme or "未获取",
            ddl_content=ddl_content or "未获取",
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "review_ready"},
        )


@register_skill
class CodeReviewDimensionSkill(BaseSkill):
    """单维度代码审查 Skill — PERF-11 拆分模式的 prompt 组装。

    input 传 dimension（维度定义 dict：name + focus）时渲染
    code_review_dimension.tpl.md——单块审查拆 3 维度并行，每维一次
    小调用（关键路径 778s 单调用 → 并行 ~1/3），同输入不同审查透镜。
    输出契约锁定 `- [级别] ` 列表行 + **审查结论** 标记行（有效性门）。
    """

    name = "code_review_dimension"
    description = "单维度代码审查 — 需求与设计对齐 / 逻辑正确性 / 规范与影响 三透镜之一"
    version = "1.0.0"
    prompt_template_path = "code_review_dimension.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """组装单维度审查 prompt（维度名/focus + 全量审查上下文）。"""
        inp = context.input if isinstance(context.input, dict) else {}

        dimension = inp.get("dimension") or {}
        if not isinstance(dimension, dict) or not dimension.get("name"):
            return SkillResult(
                success=False,
                data={},
                error="dimension 输入缺失（需含 name/focus 的维度定义）",
            )

        sql_content = inp.get("sql_content") or context.state.get("sql_content", "")
        design_scheme = inp.get("design_scheme") or context.state.get("design_scheme", "")
        ddl_content = inp.get("ddl_content") or context.state.get("ddl_content", "")
        requirement_desc = (
            inp.get("requirement_desc")
            or context.state.get("requirement_desc", "")
            or context.state.get("requirement_summary", "")
            or context.state.get("requirement", "")
        )
        validation_result = inp.get("validation_result") or context.state.get(
            "validation_result", {}
        )

        # validation_result dict 格式化为可读文本（同 CodeReviewSkill）
        if isinstance(validation_result, dict):
            issues = validation_result.get("issues", [])
            if issues:
                validation_text = "\n".join(
                    f"- [{i.get('level', 'INFO')}] Line {i.get('line', '?')}: {i.get('message', '')}"
                    for i in issues
                )
            else:
                validation_text = "未发现校验问题"
        else:
            validation_text = str(validation_result)

        prompt = self.load_prompt_template(
            requirement_desc=requirement_desc,
            sql_content=sql_content,
            domain_context=context.state.get("domain_context", ""),
            validation_result=validation_text,
            design_scheme=design_scheme or "未获取",
            ddl_content=ddl_content or "未获取",
            dimension_name=dimension["name"],
            dimension_focus=dimension.get("focus", ""),
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "review_ready", "dimension": dimension.get("key", "")},
        )
