"""Recovery 策略测试 — 指数退避 + jitter。"""

from __future__ import annotations

import pytest

from src.aqueduct.engine.recovery import (
    ErrorSeverity,
    RecoveryPolicy,
    RecoveryStrategy,
)


class TestErrorClassification:
    """错误分类测试。"""

    def setup_method(self):
        self.strategy = RecoveryStrategy()

    def test_timeout_is_transient(self):
        assert (
            self.strategy.classify_error(TimeoutError("connection timeout"))
            == ErrorSeverity.TRANSIENT
        )

    def test_rate_limit_is_transient(self):
        assert (
            self.strategy.classify_error(RuntimeError("429 rate limit exceeded"))
            == ErrorSeverity.TRANSIENT
        )

    def test_connection_error_is_transient(self):
        assert (
            self.strategy.classify_error(ConnectionError("connection refused"))
            == ErrorSeverity.TRANSIENT
        )

    def test_network_unreachable_is_transient(self):
        assert (
            self.strategy.classify_error(OSError("network unreachable")) == ErrorSeverity.TRANSIENT
        )

    def test_missing_param_is_validation(self):
        assert (
            self.strategy.classify_error(ValueError("missing required parameter"))
            == ErrorSeverity.VALIDATION
        )

    def test_invalid_format_is_validation(self):
        assert (
            self.strategy.classify_error(ValueError("invalid format for field"))
            == ErrorSeverity.VALIDATION
        )

    def test_not_found_is_validation(self):
        assert (
            self.strategy.classify_error(FileNotFoundError("file not found"))
            == ErrorSeverity.VALIDATION
        )

    def test_unknown_error_is_fatal(self):
        assert (
            self.strategy.classify_error(RuntimeError("something completely unexpected"))
            == ErrorSeverity.FATAL
        )

    def test_null_pointer_is_fatal(self):
        assert (
            self.strategy.classify_error(AttributeError("'NoneType' has no attribute"))
            == ErrorSeverity.FATAL
        )


class TestExponentialBackoff:
    """指数退避测试。"""

    def test_delay_increases_exponentially(self):
        strategy = RecoveryStrategy(RecoveryPolicy(base_delay_seconds=1.0, jitter_range=(1.0, 1.0)))
        d1 = strategy.calculate_delay(1)
        d2 = strategy.calculate_delay(2)
        d3 = strategy.calculate_delay(3)
        assert d1 == pytest.approx(1.0, abs=0.01)
        assert d2 == pytest.approx(2.0, abs=0.01)
        assert d3 == pytest.approx(4.0, abs=0.01)

    def test_delay_capped_at_max(self):
        strategy = RecoveryStrategy(
            RecoveryPolicy(
                base_delay_seconds=10.0,
                max_delay_seconds=30.0,
                jitter_range=(1.0, 1.0),
            )
        )
        d = strategy.calculate_delay(10)  # 10 * 2^9 = 5120, capped at 30
        assert d == pytest.approx(30.0, abs=0.01)

    def test_jitter_applied(self):
        strategy = RecoveryStrategy(
            RecoveryPolicy(
                base_delay_seconds=1.0,
                jitter_range=(0.5, 1.5),
            )
        )
        delays = {strategy.calculate_delay(1) for _ in range(50)}
        assert len(delays) > 1  # jitter produces different values
        assert all(0.5 <= d <= 1.5 for d in delays)


class TestRecoverAction:
    """恢复动作测试。"""

    def test_transient_retries_up_to_max(self):
        strategy = RecoveryStrategy(RecoveryPolicy(max_retries=3))
        for attempt in (1, 2):
            result = strategy.recover("test_node", TimeoutError("timeout"), attempt=attempt)
            assert result.action == "retry"
            assert result.delay_seconds > 0

    def test_transient_halts_after_max(self):
        strategy = RecoveryStrategy(RecoveryPolicy(max_retries=3))
        result = strategy.recover("test_node", TimeoutError("timeout"), attempt=3)
        assert result.action == "halt"

    def test_validation_skips(self):
        strategy = RecoveryStrategy()
        result = strategy.recover("test_node", ValueError("missing param"))
        assert result.action == "skip"

    def test_fatal_halts(self):
        strategy = RecoveryStrategy()
        result = strategy.recover("test_node", RuntimeError("unknown"))
        assert result.action == "halt"

    def test_result_contains_delay(self):
        strategy = RecoveryStrategy()
        result = strategy.recover("node", TimeoutError("timeout"), attempt=1)
        assert result.delay_seconds > 0
        assert "等待" in result.message
