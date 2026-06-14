"""错误恢复策略 — RecoveryStrategy。

当 DAG 节点执行失败时，根据错误类型采取不同恢复策略：
1. 临时错误（网络超时、API 限流）→ 重试（指数退避 + jitter）
2. 校验错误（输入参数缺失、格式错误）→ 跳过并记录
3. 致命错误（Skill 不存在、代码逻辑错误）→ 终止工作流
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """错误严重程度。"""

    TRANSIENT = "transient"  # 临时错误，可重试
    VALIDATION = "validation"  # 校验错误，可跳过
    FATAL = "fatal"  # 致命错误，终止工作流


@dataclass
class RecoveryPolicy:
    """恢复策略配置。"""

    max_retries: int = 3  # 最大重试次数
    base_delay_seconds: float = 1.0  # 基础重试间隔（秒）
    max_delay_seconds: float = 60.0  # 最大重试间隔（秒）
    exponential_base: float = 2.0  # 指数退避基数
    jitter_range: tuple[float, float] = field(default=(0.5, 1.5))  # jitter 乘数范围
    retry_on_transient: bool = True  # 临时错误是否重试
    skip_on_validation: bool = True  # 校验错误是否跳过
    halt_on_fatal: bool = True  # 致命错误是否终止


@dataclass
class RecoveryResult:
    """恢复执行结果。"""

    action: str  # "retry" | "skip" | "halt"
    success: bool
    message: str = ""
    attempt: int = 0
    delay_seconds: float = 0.0  # 建议等待时间


class RecoveryStrategy:
    """错误恢复策略执行器。"""

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        """初始化恢复策略。

        Args:
            policy: 恢复策略配置。未指定时使用默认值。
        """
        self._policy = policy or RecoveryPolicy()

    def classify_error(self, error: Exception) -> ErrorSeverity:
        """根据异常类型分类错误严重程度。

        Args:
            error: 捕获的异常。

        Returns:
            错误严重程度枚举值。
        """
        error_msg = str(error).lower()

        # 临时错误：网络相关、超时、限流
        transient_keywords = [
            "timeout",
            "rate limit",
            "429",
            "connection",
            "retry",
            "network",
            "unreachable",
            "reset",
            "temporarily",
            "overloaded",
            "throttl",
        ]
        if any(kw in error_msg for kw in transient_keywords):
            return ErrorSeverity.TRANSIENT

        # 校验错误：参数缺失、格式错误
        validation_keywords = [
            "missing",
            "invalid",
            "format",
            "not found",
            "未注册",
            "校验失败",
            "参数错误",
            "required",
        ]
        if any(kw in error_msg for kw in validation_keywords):
            return ErrorSeverity.VALIDATION

        # 其他为致命错误
        return ErrorSeverity.FATAL

    def calculate_delay(self, attempt: int) -> float:
        """计算指数退避 + jitter 的重试延迟。

        公式: min(base_delay * exponential_base^(attempt-1), max_delay) * jitter

        Args:
            attempt: 当前尝试次数（从 1 开始）。

        Returns:
            建议等待秒数。
        """
        # 指数退避
        delay = self._policy.base_delay_seconds * (self._policy.exponential_base ** (attempt - 1))
        # 上限截断
        delay = min(delay, self._policy.max_delay_seconds)
        # 随机 jitter
        jitter = random.uniform(*self._policy.jitter_range)
        delay *= jitter
        return round(delay, 2)

    def recover(
        self,
        node_name: str,
        error: Exception,
        attempt: int = 1,
    ) -> RecoveryResult:
        """执行恢复策略。

        Args:
            node_name: 失败的节点名称。
            error: 捕获的异常。
            attempt: 当前尝试次数（从 1 开始）。

        Returns:
            RecoveryResult，包含恢复动作和结果。
        """
        severity = self.classify_error(error)

        if severity == ErrorSeverity.TRANSIENT and self._policy.retry_on_transient:
            if attempt < self._policy.max_retries:
                delay = self.calculate_delay(attempt)
                logger.warning(
                    "节点 '%s' 临时错误（第 %d/%d 次重试，等待 %.1fs）: %s",
                    node_name,
                    attempt,
                    self._policy.max_retries,
                    delay,
                    error,
                )
                return RecoveryResult(
                    action="retry",
                    success=False,
                    message=f"临时错误，第 {attempt}/{self._policy.max_retries} 次重试，等待 {delay}s",
                    attempt=attempt,
                    delay_seconds=delay,
                )
            else:
                logger.error(
                    "节点 '%s' 重试 %d 次后仍失败: %s",
                    node_name,
                    self._policy.max_retries,
                    error,
                )
                return RecoveryResult(
                    action="halt",
                    success=False,
                    message=f"重试 {self._policy.max_retries} 次后仍失败",
                    attempt=attempt,
                )

        elif severity == ErrorSeverity.VALIDATION and self._policy.skip_on_validation:
            logger.warning("节点 '%s' 校验错误，跳过: %s", node_name, error)
            return RecoveryResult(
                action="skip",
                success=False,
                message=f"校验错误，已跳过: {error}",
                attempt=attempt,
            )

        else:
            # 致命错误或策略不允许恢复
            if self._policy.halt_on_fatal:
                logger.error("节点 '%s' 致命错误，终止工作流: %s", node_name, error)
                return RecoveryResult(
                    action="halt",
                    success=False,
                    message=f"致命错误: {error}",
                    attempt=attempt,
                )

            # 策略允许继续（不终止）
            return RecoveryResult(
                action="skip",
                success=False,
                message=f"错误但继续执行: {error}",
                attempt=attempt,
            )

    def wait_and_retry(self, result: RecoveryResult) -> None:
        """根据 RecoveryResult 的 delay_seconds 等待。

        Args:
            result: recover() 返回的结果。
        """
        if result.action == "retry" and result.delay_seconds > 0:
            time.sleep(result.delay_seconds)
