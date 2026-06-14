"""CLI 入口测试。

注意：cli.main 模块在 Windows 上会替换 sys.stdout，
因此测试中延迟导入，避免影响 pytest 的 stdout capture。
"""

from __future__ import annotations


def _get_parser():
    """延迟导入 create_parser，避免 Windows stdout 替换问题。"""
    from src.aqueduct.cli.main import create_parser

    return create_parser()


class TestCLIParser:
    """CLI 参数解析测试。"""

    def test_create_parser(self):
        parser = _get_parser()
        assert parser is not None
        assert parser.prog == "aqueduct"

    def test_dev_command(self):
        parser = _get_parser()
        args = parser.parse_args(["dev", "req.md"])
        assert args.command == "dev"
        assert args.requirement == "req.md"

    def test_dev_with_output(self):
        parser = _get_parser()
        args = parser.parse_args(["dev", "req.md", "-o", "custom_output"])
        assert args.output == "custom_output"

    def test_review_command(self):
        parser = _get_parser()
        args = parser.parse_args(["review", "online.sql", "changed.sql"])
        assert args.command == "review"
        assert args.online_sql == "online.sql"
        assert args.changed_sql == "changed.sql"

    def test_validate_command(self):
        parser = _get_parser()
        args = parser.parse_args(["validate", "test.sql"])
        assert args.command == "validate"
        assert args.sql_file == "test.sql"

    def test_validate_strict_mode(self):
        parser = _get_parser()
        args = parser.parse_args(["validate", "test.sql", "--strict"])
        assert args.strict is True

    def test_status_command(self):
        parser = _get_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_no_command_returns_none(self):
        parser = _get_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_verbose_flag(self):
        parser = _get_parser()
        args = parser.parse_args(["--verbose", "status"])
        assert args.verbose is True

    def test_dev_with_verbose(self):
        parser = _get_parser()
        args = parser.parse_args(["--verbose", "dev", "req.md"])
        assert args.verbose is True
        assert args.command == "dev"
