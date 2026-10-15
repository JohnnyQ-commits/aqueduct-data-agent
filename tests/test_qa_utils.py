"""utils/qa.py — 澄清问答三件套（parse/format/append）单元测试。

该模块从 cli/main.py 移出共享（CLI 与 Gradio UI 双消费方），
本测试钉死行为契约：提取口径、格式契约、文件追加语义。
"""

import pytest

from src.aqueduct.utils.qa import (
    append_qa_to_file,
    format_qa_section,
    parse_questions,
)


def _make_state(tmp_path, with_file: bool):
    state = {"metadata": {"output_dir": str(tmp_path)}}
    if with_file:
        (tmp_path / "Phase1-需求理解摘要.md").write_text(
            "# 需求摘要\n\n### 待确认问题\n\n1. GMV 是否含退款？\n", encoding="utf-8"
        )
    return state


class TestParseQuestions:
    def test_parse_questions_numbered_list(self):
        summary = (
            "## 需求摘要\n\n内容...\n\n### 待确认问题\n\n"
            "1. [歧义点] GMV 是否包含退款订单？ → 建议: 仅统计已支付订单\n"
            "2. [缺失信息] 是否按品类维度拆分\n"
        )
        assert parse_questions(summary) == [
            "[歧义点] GMV 是否包含退款订单？ → 建议: 仅统计已支付订单",
            "[缺失信息] 是否按品类维度拆分",
        ]

    def test_parse_questions_skipped_when_user_clarification_present(self):
        # 已有澄清记录（重跑场景）时不再提取，防重复追加
        summary = "### 待确认问题\n\n1. Q?\n\n### 用户澄清记录\n\n**Q1**: Q?\n"
        assert parse_questions(summary) == []

    def test_parse_questions_no_section_returns_empty(self):
        assert parse_questions("## 需求摘要\n\n没有问题节") == []
        assert parse_questions("") == []

    def test_parse_questions_stops_at_next_heading(self):
        # 问题节在下一个 ### 处截断，后继小节内容不误吞
        summary = "### 待确认问题\n\n1. 问题一\n2. 问题二\n\n### 其他小节\n3. 不是问题\n"
        assert parse_questions(summary) == ["问题一", "问题二"]


class TestFormatQaSection:
    def test_format_qa_section_contains_pairs(self):
        section = format_qa_section([{"question": "GMV 口径？", "answer": "仅已支付"}])
        assert "### 用户澄清记录" in section
        assert "**Q1**: GMV 口径？" in section
        assert "**A1**: 仅已支付" in section


class TestAppendQaToFile:
    def test_append_qa_to_file_writes_to_phase1_md(self, tmp_path):
        state = _make_state(tmp_path, with_file=True)
        qa_pairs = [{"question": "Q1?", "answer": "A1"}]
        assert append_qa_to_file(state, qa_pairs) is True
        content = (tmp_path / "Phase1-需求理解摘要.md").read_text(encoding="utf-8")
        assert "### 用户澄清记录" in content
        assert "**A1**: A1" in content

    def test_append_qa_to_file_missing_file_returns_false(self, tmp_path):
        state = _make_state(tmp_path, with_file=False)
        assert append_qa_to_file(state, [{"question": "Q", "answer": "A"}]) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
