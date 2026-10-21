"""任务级日志模块单元测试。"""

from __future__ import annotations

import logging

from src.aqueduct.utils.task_logger import remove_task_handler, setup_task_logging


class TestSetupTaskLogging:
    """setup_task_logging 创建任务日志文件。"""

    def test_creates_log_file_when_output_dir_missing(self, tmp_path):
        """输出目录不存在时自动创建，日志文件从 Phase 1 起即可写入。

        回归：旧实现不建父目录，API 调用（无 CLI 预建目录）时
        FileHandler 在首个产出物保存前初始化必然失败，整轮管道无任务日志。
        """
        log_path = tmp_path / "output" / "new_task" / "task.2026-08-31.log"
        assert not log_path.parent.exists()

        handler = setup_task_logging("new_task", log_path)

        try:
            assert handler is not None
            assert log_path.is_file()
            logging.getLogger("aqueduct.test").info("hello-task-log")
            for h in logging.getLogger("aqueduct").handlers:
                h.flush()
            content = log_path.read_text(encoding="utf-8")
            assert "hello-task-log" in content
        finally:
            remove_task_handler(handler)

    def test_returns_none_on_unwritable_path(self, tmp_path):
        """父路径是文件而非目录时优雅降级返回 None。"""
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("i am a file", encoding="utf-8")
        handler = setup_task_logging("bad", blocker / "task.log")
        assert handler is None


class TestLoggerNameParam:
    """handler 可挂到指定包根 logger——src.aqueduct 导入形态路由。

    run 6 实录：driver/测试用 `from src.aqueduct...` 导入时，模块 logger
    全在 src.* 树上，handler 挂 aqueduct logger 收不到任何消息，task log
    0 字节。core.py 现传入自身包根（__name__ 前缀），两棵树都可达。
    """

    def test_routes_messages_from_src_hierarchy(self, tmp_path):
        log_path = tmp_path / "task.test.log"
        handler = setup_task_logging("t", log_path, logger_name="src")
        try:
            assert handler is not None
            logging.getLogger("src.aqueduct.core").info("src-tree-message")
            for h in logging.getLogger("src").handlers:
                h.flush()
            assert "src-tree-message" in log_path.read_text(encoding="utf-8")
        finally:
            remove_task_handler(handler)

    def test_logger_level_forced_to_info(self, tmp_path):
        """包根 logger level 置 INFO，API 调用（不经 CLI）不丢 INFO。"""
        log_path = tmp_path / "task.test2.log"
        handler = setup_task_logging("t", log_path, logger_name="src")
        try:
            assert logging.getLogger("src").level == logging.INFO
        finally:
            remove_task_handler(handler)
