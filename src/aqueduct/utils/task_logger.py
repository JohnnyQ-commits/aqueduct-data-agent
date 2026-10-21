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
    logger_name: str = "aqueduct",
) -> logging.Handler | None:
    """为当前任务创建独立日志文件。

    在指定路径创建日志文件（自动创建缺失的父目录——
    API 调用时输出目录在首个产出物保存前尚不存在）。
    添加 FileHandler，日志自动同步写入任务日志文件。

    Args:
        requirement_name: 需求名称（用于日志标识）。
        log_file_path: 日志文件完整路径。
        logger_name: handler 挂载的包根 logger 名。默认 "aqueduct"；
            `from src.aqueduct...` 导入形态（测试/driver）下模块 logger
            在 `src.*` 树上，调用方须传自身包根（__name__ 前缀），
            否则 handler 收不到任何消息（run 6 实录 task log 0 字节）。

    Returns:
        添加的 FileHandler 实例（用于后续清理），失败时返回 None。
    """
    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(log_file_path), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        aqueduct_logger = logging.getLogger(logger_name)
        aqueduct_logger.addHandler(handler)
        # 确保 logger 自身 level 允许 INFO 通过，
        # 否则在 Python API（不经 CLI）调用时会继承 root WARNING 导致 INFO 丢失
        if aqueduct_logger.level == logging.NOTSET or aqueduct_logger.level > logging.INFO:
            aqueduct_logger.setLevel(logging.INFO)
        # 记录挂载目标，remove_task_handler 从对应 logger 摘除
        handler._task_log_logger_name = logger_name  # type: ignore[attr-defined]

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
        logger_name = getattr(handler, "_task_log_logger_name", "aqueduct")
        logging.getLogger(logger_name).removeHandler(handler)
        handler.close()
