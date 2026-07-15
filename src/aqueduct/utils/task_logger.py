"""任务级日志配置 — 为每个管道任务创建独立日志文件。

用法:
    from aqueduct.utils.task_logger import setup_task_logging

    setup_task_logging(state)
    # 之后所有 aqueduct.* 的日志自动同步写入任务日志文件
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_task_logging(
    requirement_name: str,
    log_file_path: Path,
) -> logging.Handler | None:
    """为当前任务创建独立日志文件。

    在指定路径创建日志文件（不自动创建父目录，
    父目录由 helpers.get_output_dir() 在保存产出物时创建）。
    添加 FileHandler，日志自动同步写入任务日志文件。

    Args:
        requirement_name: 需求名称（用于日志标识）。
        log_file_path: 日志文件完整路径。

    Returns:
        添加的 FileHandler 实例（用于后续清理），失败时返回 None。
    """
    try:
        handler = logging.FileHandler(str(log_file_path), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        aqueduct_logger = logging.getLogger("aqueduct")
        aqueduct_logger.addHandler(handler)
        # 确保 aqueduct logger 自身 level 允许 INFO 通过，
        # 否则在 Python API（不经 CLI）调用时会继承 root WARNING 导致 INFO 丢失
        if aqueduct_logger.level == logging.NOTSET or aqueduct_logger.level > logging.INFO:
            aqueduct_logger.setLevel(logging.INFO)

        logger.info(
            "[task=%s] 任务日志文件: %s",
            requirement_name,
            log_file_path,
        )
        return handler

    except Exception:
        logger.warning("任务日志文件创建失败，仅使用全局日志", exc_info=True)
        return None


def remove_task_handler(handler: logging.Handler | None) -> None:
    """移除任务日志处理器。

    Args:
        handler: setup_task_logging 返回的 handler 实例。
    """
    if handler is not None:
        logging.getLogger("aqueduct").removeHandler(handler)
        handler.close()
