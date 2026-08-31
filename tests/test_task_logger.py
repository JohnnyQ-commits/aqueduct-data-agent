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
