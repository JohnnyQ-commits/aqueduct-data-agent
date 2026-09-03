"""交付物结构契约（P0-2）。

LLM 产出落盘前的确定性结构门禁：每类交付物定义必需章节 → 校验 →
缺章触发 1 次定向重生成 → 仍缺则横幅降级落盘 + errors 记录（不死管道）。

契约与 prompt 模板的输出格式指令**对齐**——模板要求的章节 = 契约校验的章节，
避免门禁与模板脱节导致 100% 重生成。契约只拦「缺章」，不拦「多内容」：
多内容（如 v5 Phase6-Design.md 三文档合一）靠模板指令修正，缺章靠本门禁兜底。

校验对象仅限 LLM 直接产出的文档（Phase1/Phase2/Phase6-Design/Phase6-知识沉淀）：
SQL 类文件由 is_valid_sql + linter 把关，纯 Python 拼接的报告天然合规。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionRule:
    """一条必需章节规则：label 用于告警文案，pattern 匹配标题行/代码块。"""

    label: str
    pattern: re.Pattern[str]


# 标题行前缀：1~4 级标题，容忍「一、」「## 」等序号/修饰前缀
_H = r"^#{1,4}\s*[^\n]*"

CONTRACTS: dict[str, tuple[SectionRule, ...]] = {
    # requirement_and_design.tpl.md「输出格式 · 第一部分」：
    # 目标表等关键列表项 + ### 待确认问题
    "Phase1-需求理解摘要.md": (
        SectionRule("目标表", re.compile(r"^[-*]\s*\**\s*目标表", re.MULTILINE)),
        SectionRule("待确认问题", re.compile(_H + r"待确认问题", re.MULTILINE)),
    ),
    # 两条模板路径共用：OPT-5 三合一（### 取数逻辑 等 H3）/ OPT-4 二合一（## 取数逻辑 等 H2）
    "Phase2-设计方案.md": (
        SectionRule("取数逻辑", re.compile(r"^#{2,4}\s*取数逻辑", re.MULTILINE)),
        SectionRule("字段映射", re.compile(r"^#{2,4}\s*字段映射", re.MULTILINE)),
        SectionRule("上下游依赖", re.compile(r"^#{2,4}\s*上下游依赖", re.MULTILINE)),
    ),
    # report_delivery.tpl.md「推理步骤 6」：Design.md 章节要求；
    # 血缘关系可用标题表达，也可仅用 mermaid 块表达
    "Phase6-Design.md": (
        SectionRule("需求背景", re.compile(_H + r"背景", re.MULTILINE)),
        SectionRule("设计方案", re.compile(_H + r"设计方案", re.MULTILINE)),
        SectionRule("表结构", re.compile(_H + r"(?:表结构|DDL)", re.MULTILINE)),
        SectionRule("核心SQL", re.compile(_H + r"SQL", re.MULTILINE)),
        SectionRule(
            "血缘图",
            re.compile(r"(?:^#{1,4}\s*[^\n]*血缘|^```mermaid)", re.MULTILINE),
        ),
    ),
    # knowledge_extract.tpl.md「任务」：主标题 + 5 章结构
    "Phase6-知识沉淀.md": (
        SectionRule("知识沉淀标题", re.compile(r"^#\s*知识沉淀", re.MULTILINE)),
        SectionRule("业务域知识", re.compile(_H + r"业务域知识", re.MULTILINE)),
        SectionRule("表结构经验", re.compile(_H + r"表结构经验", re.MULTILINE)),
        SectionRule("SQL开发经验", re.compile(_H + r"SQL\s*开发经验", re.MULTILINE)),
        SectionRule("指标口径", re.compile(_H + r"指标口径", re.MULTILINE)),
        SectionRule("待确认事项", re.compile(_H + r"待确认", re.MULTILINE)),
    ),
}


def validate_structure(filename: str, content: str) -> list[str]:
    """校验交付物结构契约，返回缺失章节 label 列表（空 = 通过）。

    未定义契约的文件与空内容不拦（空响应由 call_llm 层重试）。
    """
    rules = CONTRACTS.get(filename)
    if not rules or not content or not content.strip():
        return []
    return [rule.label for rule in rules if not rule.pattern.search(content)]


def ensure_structure(
    filename: str,
    content: str,
    regenerate: Callable[[list[str]], str] | None = None,
) -> tuple[str, list[str]]:
    """结构门禁三段式：校验 → 1 次定向重生成 → 仍缺横幅降级。

    Args:
        filename: 交付物文件名（契约白名单键）。
        content: 待落盘内容。
        regenerate: 定向重生成回调，入参为缺失章节清单，返回新内容；
            传入 None 表示调用方不支持重试，直接降级。

    Returns:
        (最终内容, 仍缺失的章节清单)。缺失清单非空 = 已降级，
        调用方应记入 state["errors"]（本函数不碰 state，保持线程安全）。
    """
    missing = validate_structure(filename, content)
    if not missing:
        return content, []

    if regenerate is not None:
        try:
            new_content = regenerate(missing)
        except Exception:
            logger.warning(
                "[%s] 结构契约定向重生成异常，降级落盘（缺: %s）",
                filename,
                "、".join(missing),
                exc_info=True,
            )
        else:
            new_missing = validate_structure(filename, new_content)
            if not new_missing:
                logger.info(
                    "[%s] 结构契约重生成修复: 原 %d 处缺章已补全",
                    filename,
                    len(missing),
                )
                return new_content, []
            logger.warning(
                "[%s] 结构契约重生成后仍缺: %s，降级落盘",
                filename,
                "、".join(new_missing),
            )
            content, missing = new_content, new_missing

    return _add_banner(content, filename, missing), missing


def _add_banner(content: str, filename: str, missing: list[str]) -> str:
    """缺章降级横幅（加在内容头部，供人工补充提示）。"""
    names = "、".join(missing)
    return (
        f"> ⚠️ **结构门禁告警**：本文档缺少必需章节——{names}。\n"
        f"> （{filename} 结构契约校验未通过，定向重生成 1 次后仍缺，"
        f"已降级落盘，请人工补充）\n\n{content}"
    )


def build_retry_prompt(prompt: str, filename: str, missing: list[str]) -> str:
    """定向重生成 prompt：原 prompt + 缺章警示。"""
    names = "、".join(missing)
    return (
        f"{prompt}\n\n---\n\n"
        f"⚠️ **结构警示（重试）**：你上次的响应缺少必需章节——{names}。\n"
        f"本次必须严格按「输出格式」要求补全这些章节（{filename} 结构契约）。"
    )


def gate_response(
    prompt: str,
    response: str,
    split: Callable[[str], dict[str, str]],
    filenames: list[str],
    regenerate: Callable[[str, list[str]], str],
) -> tuple[dict[str, str], list[str]]:
    """多产物响应（三合一/二合一）的结构门禁。

    split 将 LLM 响应拆为 {filename: content}；对 filenames 逐一校验；
    缺章 → build_retry_prompt 构造定向重生成提示 → regenerate 执行重调用
    → 重拆分复检；仍缺 → 逐文件加横幅，缺失汇总返回，调用方记 errors。

    regenerate 由调用方注入（闭包捕获节点命名空间的 call_llm 与 state），
    本模块不依赖 LLM 调用层。

    Returns:
        (门禁后的产物 dict —— 含 split 产出的全部键, 仍缺失的章节清单)。
    """

    def _missing_of(products: dict[str, str]) -> list[str]:
        return [m for f in filenames for m in validate_structure(f, products.get(f, ""))]

    products = split(response)
    missing = _missing_of(products)
    if missing:
        logger.warning(
            "[%s] 结构契约缺章: %s，定向重生成 1 次",
            "/".join(filenames),
            "、".join(missing),
        )
        try:
            retry_prompt = build_retry_prompt(prompt, "、".join(filenames), missing)
            products = split(regenerate(retry_prompt, missing))
            missing = _missing_of(products)
        except Exception:
            logger.warning("结构契约定向重生成异常，降级落盘", exc_info=True)
            missing = _missing_of(products)

    if missing:
        for filename in filenames:
            if validate_structure(filename, products.get(filename, "")):
                products[filename] = _add_banner(
                    products[filename], filename, validate_structure(filename, products[filename])
                )

    return products, missing


# 横幅标记（降级产物统一记 errors 的扫描锚点）
BANNER_MARK = "结构门禁告警"


def scan_degradation(content: str) -> list[str]:
    """扫描内容中的门禁降级横幅行（供节点统一记 state["errors"]）。"""
    return [line.strip() for line in content.splitlines() if BANNER_MARK in line]
