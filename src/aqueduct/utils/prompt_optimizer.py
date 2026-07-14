"""Prompt 压缩工具 — 减少长 prompt 的 token 数量。

两种压缩模式：

1. **规则压缩**（默认）：
   - 去除 Markdown 图片/HTML
   - 压缩重复的分隔线
   - 截断过长的表结构描述
   - 去除冗余空行

2. **LLM 压缩**（可选）：
   - 通过 ModelRouter 路由到可配置的模型（默认 Haiku）
   - 调用 `prompt_compress` 任务类型
   - 用户可通过环境变量切换模型（如换 Codex）

用法:
    # 规则压缩（无 LLM 成本）
    compressed = PromptCompressor().compress_rule(long_prompt)

    # LLM 压缩（需要 state 和路由）
    compressor = PromptCompressor()
    compressed = compressor.compress_llm(long_prompt, state)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class PromptCompressor:
    """Prompt 压缩器。

    支持规则压缩（快速、无成本）和 LLM 压缩（高质量、可配置模型）。
    """

    # 规则压缩配置
    MAX_TABLE_SCHEMA_CHARS = 3000  # 单个表结构最大保留字符数
    MAX_PROMPT_CHARS = 15000  # 压缩后 prompt 最大字符数（软上限）
    KEEP_SECTIONS = {"## 任务", "## 输入", "## 输出格式", "# 任务", "# 输入"}  # 必须保留的章节

    def compress_rule(self, prompt: str) -> str:
        """规则压缩：去除冗余内容，保留关键信息。

        不需要 LLM 调用，零成本、零延迟。
        """
        if not prompt or len(prompt) < 500:
            return prompt  # 短 prompt 无需压缩

        original_len = len(prompt)
        result = prompt

        # 1. 去除 Markdown 图片
        result = re.sub(r"!\[.*?\]\(.*?\)", "", result)

        # 2. 去除 HTML 标签
        result = re.sub(r"<[^>]+>", "", result)

        # 3. 压缩连续分隔线（3+ 条 → 1 条）
        result = re.sub(r"(^---\s*\n){3,}", "---\n\n", result, flags=re.MULTILINE)

        # 4. 压缩连续空行（3+ 行 → 2 行）
        result = re.sub(r"\n{3,}", "\n\n", result)

        # 5. 截断过长的表结构描述
        result = self._truncate_table_schemas(result)

        # 6. 去除示例中的冗余（保留第一个示例，截断后续）
        result = self._truncate_examples(result)

        compressed_len = len(result)
        reduction = (1 - compressed_len / original_len) * 100 if original_len > 0 else 0

        if reduction > 5:
            logger.info(
                "规则压缩: %d → %d 字符（减少 %.0f%%）",
                original_len,
                compressed_len,
                reduction,
            )

        return result

    def compress_llm(self, prompt: str, state: Any) -> str:
        """LLM 压缩：通过路由到可配置模型进行智能压缩。

        默认使用 Haiku（通过 prompt_compress 任务类型路由）。
        用户可通过环境变量切换模型（如换 Codex 只需修改路由配置）。

        Args:
            prompt: 要压缩的 prompt。
            state: WorkflowState，用于访问 LLM 路由。

        Returns:
            压缩后的 prompt。
        """
        if not prompt or len(prompt) < 1000:
            return prompt  # 短 prompt 无需压缩

        # 先用规则压缩预处理（减少 LLM 输入 token）
        pre_compressed = self.compress_rule(prompt)

        # 构建压缩指令
        compress_instruction = (
            "你是一个 prompt 压缩专家。请压缩以下 prompt，目标减少 40% 的 token 数量。\n\n"
            "压缩规则：\n"
            "1. 保留所有任务指令、输入数据、输出格式要求\n"
            "2. 去除重复的说明和冗余的示例\n"
            "3. 保留章节结构（## 标题）\n"
            "4. 保留关键约束和禁止项\n"
            "5. 直接输出压缩后的 prompt，不要添加解释\n\n"
            "---\n\n"
            f"{pre_compressed}"
        )

        try:
            from ..engine.nodes.helpers import call_llm

            compressed = call_llm(state, "prompt_compress", compress_instruction)

            if compressed and len(compressed) < len(prompt):
                reduction = (1 - len(compressed) / len(prompt)) * 100
                logger.info(
                    "LLM 压缩: %d → %d 字符（减少 %.0f%%）",
                    len(prompt),
                    len(compressed),
                    reduction,
                )
                return compressed
            else:
                logger.warning("LLM 压缩未产生更短结果，使用规则压缩结果")
                return pre_compressed

        except Exception as e:
            logger.warning("LLM 压缩失败，降级到规则压缩: %s", e)
            return pre_compressed

    # ── 内部工具方法 ──────────────────────────────────────────────────────────

    def _truncate_table_schemas(self, text: str) -> str:
        """截断过长的表结构描述。

        查找 "表: xxx" 开头的段落，如果超过阈值则截断并添加 "...（省略 N 个字段）"。
        """
        pattern = re.compile(
            r"(表: \S+\n注释:.*?\n字段 \(\d+ 个\):\n)(.*?)(?=\n\n表: |\Z)",
            re.DOTALL,
        )

        def truncate_match(m: re.Match) -> str:
            header = m.group(1)
            fields_text = m.group(2)

            if len(fields_text) <= self.MAX_TABLE_SCHEMA_CHARS:
                return m.group(0)

            # 截断字段列表
            truncated = fields_text[: self.MAX_TABLE_SCHEMA_CHARS]
            # 在最后一个完整行处截断
            last_newline = truncated.rfind("\n")
            if last_newline > 0:
                truncated = truncated[:last_newline]

            # 统计被省略的字段
            total_fields = fields_text.count("\n  - ")
            kept_fields = truncated.count("\n  - ")
            omitted = total_fields - kept_fields

            if omitted > 0:
                truncated += f"\n  ...（省略 {omitted} 个字段）"

            return header + truncated

        return pattern.sub(truncate_match, text)

    def _truncate_examples(self, text: str) -> str:
        """截断冗余示例：保留第一个完整示例，截断后续示例。"""
        # 查找示例章节的起始位置（支持 ## 示例 或 ### 示例）
        start_match = re.search(r"#{2,3}\s+示例[^\n]*\n", text)
        if not start_match:
            return text

        header_end = start_match.end()

        # 找到示例章节之后的下一个同级标题边界（\n## 或 \n###）
        next_section = re.search(r"\n#{2,3}\s+(?!示例)", text[header_end:])
        section_end = header_end + next_section.start() if next_section else len(text)

        content = text[header_end:section_end]

        # 按 \n### 分割多个示例（第一个元素为空字符串，因为 content 以 \n### 开头）
        examples = content.split("\n###")

        if len(examples) <= 2:  # [空, 唯一示例] 或 [空]
            return text

        # 保留第一个示例（examples[1]），截断后续
        kept = "\n###" + examples[1]
        omitted_count = len(examples) - 1
        kept += f"\n\n（省略 {omitted_count} 个相似示例）"

        return text[:header_end] + kept + text[section_end:]


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量。

    简化估算：1 token ≈ 1.5 个中文字符或 4 个英文字符。
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.25)
