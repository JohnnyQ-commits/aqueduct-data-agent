"""平台适配器协议 — 能力声明 + 探测 + 健康体检。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Capability:
    """六动词语义能力名（str 常量，避免枚举序列化负担）。"""

    TABLE_METADATA = "table_metadata"  # 查元数据（表结构/分区/字段）
    SQL_EXECUTE = "sql_execute"  # 执行 SQL（只读验证 / 受控写入）
    LINEAGE = "lineage"  # 查血缘
    DQC_EXECUTE = "dqc_execute"  # 执行 DQC
    TASK_OPS = "task_ops"  # 任务部署与运维
    ARTIFACT_SEARCH = "artifact_search"  # 检索历史交付物


ALL_CAPABILITIES = frozenset(
    {
        Capability.TABLE_METADATA,
        Capability.SQL_EXECUTE,
        Capability.LINEAGE,
        Capability.DQC_EXECUTE,
        Capability.TASK_OPS,
        Capability.ARTIFACT_SEARCH,
    }
)


class PlatformAdapter(ABC):
    """平台适配器协议。

    adapter 不进 state——用模块级单例（``loader.get_platform_adapter``），
    规避 ``_llm_router`` 式 checkpoint 序列化失败（d4ade7c 教训）。
    """

    name: str = "base"

    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """声明的能力集合（ALL_CAPABILITIES 的子集）。"""

    def has_capability(self, cap: str) -> bool:
        """能力探测：未知能力名直接 ValueError（拼写错误不该静默 False）。"""
        if cap not in ALL_CAPABILITIES:
            raise ValueError(f"未知能力: {cap!r}，可用: {sorted(ALL_CAPABILITIES)}")
        return cap in self.capabilities()

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """登录态体检（如 bdp-cli --version / 数据平台 health_check）。

        Returns:
            ``{"status": "ok" | "unavailable", ...}``——status 必有，其余自由。
        """
