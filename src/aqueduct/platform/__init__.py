"""平台能力端口 — 六动词语义集。

设计来源：知识库《平台适配器架构-开源通用化设计》。
核心论点：协议会迭代（MCP 之后还会有新的），语义能力集相对稳定——
agent 跟数据平台打交道说到底就六个动词。Aqueduct 在语义层定义能力端口，
"用什么协议实现"留给 adapter（dbt 模式：core + adapters + profiles）。

降级阶梯（开源项目的生死线）：
完整接入（全六动词）→ 部分接入（如仅元数据）→ 纯离线（builtin 校验器兜底）。
每个 Phase 声明所需能力，缺什么就降级什么——新用户 clone 下来什么平台
都没有也能跑通管道，平台能力是加分项不是前置条件。
"""

from .auto import AutoAdapter
from .base import ALL_CAPABILITIES, Capability, PlatformAdapter
from .bdp import BDPAdapter
from .loader import (
    describe_adapter,
    get_platform_adapter,
    load_adapter,
    log_platform_banner,
    reset_platform_adapter,
)
from .none import NoneAdapter

__all__ = [
    "ALL_CAPABILITIES",
    "AutoAdapter",
    "BDPAdapter",
    "Capability",
    "NoneAdapter",
    "PlatformAdapter",
    "describe_adapter",
    "get_platform_adapter",
    "load_adapter",
    "log_platform_banner",
    "reset_platform_adapter",
]
