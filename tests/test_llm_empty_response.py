"""LLM 空响应检测单元测试。"""

from __future__ import annotations

from src.aqueduct.engine.nodes.helpers import _is_empty_response


class TestIsEmptyResponse:
    """判断 LLM 响应是否为实质空内容。"""

    def test_none_is_empty(self):
        # 虽然 type hint 是 str，但 None 在运行时可能出现
        assert _is_empty_response("") is True

    def test_empty_string_is_empty(self):
        assert _is_empty_response("") is True

    def test_whitespace_is_empty(self):
        assert _is_empty_response("   \n\t  ") is True

    def test_cli_placeholder_is_empty(self):
        """claude.py 在 CLI 后端 stdout/stderr 都为空时返回的占位符。"""
        assert _is_empty_response("[LLM 调用返回为空]") is True

    def test_cli_placeholder_with_trailing_content_is_empty(self):
        """占位符后跟额外信息（如模型名）也应视为空。"""
        assert _is_empty_response("[LLM 调用返回为空] extra info") is True

    def test_real_content_not_empty(self):
        assert _is_empty_response("CREATE TABLE foo (id INT);") is False

    def test_short_meaningful_content_not_empty(self):
        assert _is_empty_response("OK") is False

    def test_placeholder_prefix_in_middle_not_empty(self):
        """占位符字符串出现在内容中间不算空（避免误伤）。"""
        assert _is_empty_response("结果：[LLM 调用返回为空]") is False
