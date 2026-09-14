"""auto adapter — 探测语义与既有各触点门控 1:1 对齐（零行为变化）。"""

from __future__ import annotations

import logging
from typing import Any

from .base import ALL_CAPABILITIES, Capability, PlatformAdapter

logger = logging.getLogger(__name__)


class AutoAdapter(PlatformAdapter):
    """自动探测 adapter。

    探测谓词复刻既有隐式门控，保证内部部署行为不变：
    - ``table_metadata`` ← ``MCPConfig().is_configured()``（Phase 1 表结构查询原判断）
    - ``sql_execute`` / ``dqc_execute`` ← ``settings.execution_enabled is True``
      （P1-2 严格口径：非 bool True 一律视为未声明，防单测 MagicMock 意外真连）
    - ``lineage`` / ``task_ops`` / ``artifact_search`` 无实现，永不声明（不谎报）
    """

    def __init__(
        self,
        *,
        mcp_configured: bool,
        execution_enabled: bool,
        declared_name: str | None = None,
    ):
        self._mcp_configured = bool(mcp_configured)
        self._execution_enabled = execution_enabled is True
        self._declared_name = declared_name

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._declared_name or "auto"

    def capabilities(self) -> frozenset[str]:
        caps: set[str] = set()
        if self._mcp_configured:
            caps.add(Capability.TABLE_METADATA)
        if self._execution_enabled:
            caps.update({Capability.SQL_EXECUTE, Capability.DQC_EXECUTE})
        return frozenset(caps & ALL_CAPABILITIES)

    def health_check(self) -> dict[str, Any]:
        if not self.has_capability(Capability.SQL_EXECUTE):
            return {
                "status": "unavailable",
                "platform": self.name,
                "message": "未声明 sql_execute 能力，跳过执行类体检",
            }
        from ..tools.registry import get_tool

        result = get_tool("executor").execute(action="health_check")
        ok = bool(getattr(result, "success", False))
        return {
            "status": "ok" if ok else "unavailable",
            "platform": self.name,
            "detail": getattr(result, "data", None),
        }
