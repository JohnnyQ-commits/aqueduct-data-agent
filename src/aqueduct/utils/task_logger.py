"""任务级日志配置 — 为每个管道任务创建独立日志文件。

用法:
    from aqueduct.utils.task_logger import setup_task_logging

    setup_task_logging(state)
    # 之后所有 aqueduct.* 的日志自动同步写入任务日志文件
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_task_logging(
    requirement_name: str,
    output_dir: Path,
) -> logging.Handler | None:
    """为当前任务创建独立日志文件。

    在输出目录下创建 task.YYYY-MM-DD.log，添加 FileHandler，
    日志自动同步写入任务日志文件。

    Args:
        requirement_name: 需求名称（用于日志标识）。
        output_dir: 任务输出目录。

    Returns:
        添加的 FileHandler 实例（用于后续清理），失败时返回 None。
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        task_log = output_dir / f"task.{datetime.now():%Y-%m-%d}.log"

        handler = logging.FileHandler(str(task_log), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        logging.getLogger("aqueduct").addHandler(handler)

        logger.info(
            "[task=%s] 任务日志文件: %s",
            requirement_name,
            task_log,
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
