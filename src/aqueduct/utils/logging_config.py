"""统一日志配置。

提供 `setup_logging()` 函数，为整个 aqueduct 应用配置日志。
支持 Console（彩色）+ File（按日期轮转）双输出。

用法:
    from aqueduct.utils.logging_config import setup_logging

    setup_logging(level="INFO")
    logger = logging.getLogger(__name__)
    logger.info("日志已配置")
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# 彩色输出 ANSI 码
_COLORS = {
    "DEBUG": "\033[36m",  # Cyan
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[1;31m",  # Bold Red
}
_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    """Console 专用：根据日志级别添加 ANSI 颜色。"""

    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname:<8}{_RESET}"
        return super().format(record)


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    log_file: str = "aqueduct.log",
    console: bool = True,
) -> None:
    """配置全局日志系统。

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）。
        log_dir: 日志文件目录。默认项目根目录 logs/。
        log_file: 日志文件名。
        console: 是否输出到控制台。
    """
    root_logger = logging.getLogger()

    # 避免重复配置
    if root_logger.handlers:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(log_level)

    # ── Console Handler ──
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_fmt = _ColorFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        console_handler.setFormatter(console_fmt)
        root_logger.addHandler(console_handler)

    # ── File Handler（按天轮转，保留 30 天）──
    if log_dir is None:
        # 默认: 项目根目录/logs/
        log_dir = Path(__file__).resolve().parent.parent.parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_file

    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)

    # 抑制第三方库的过多日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    root_logger.info("日志系统已初始化: level=%s, file=%s", level.upper(), log_path)
